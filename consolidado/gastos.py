"""O detalhe da aba Gastos: categorias, lançamentos recentes e período anterior.

O resumo v2 do Controle Bancário publica fluxos por dia e natureza, sem
categoria. O detalhe vem dos lançamentos individuais que ele já publica em
`/patrimonio/v3/activities`, lidos só da fonte de caixa.

SÓ CONTA COMO GASTO O LANÇAMENTO GERENCIAL REALIZADO

Transferência entre contas próprias e aplicação só mudam o dinheiro de bolso
ou de forma; somá-las como gasto mostraria o dinheiro que circulou, não o que
foi gasto. A fonte recebe o filtro, e ele é conferido de novo aqui: publicador
que ignore o filtro não pode inflar a soma.

MEIA LEITURA NÃO VIRA MEIO TOTAL

Se qualquer página falhar, o detalhe inteiro fica indisponível com o motivo.
Categorias somadas sobre parte dos lançamentos seriam plausíveis e erradas.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from consolidado.fontes import Fonte
from consolidado.templatetags.dinheiro import dinheiro
from consolidado.wealthfolio_compat.activities import (
    STATUS_ERROR,
    ActivitySourceResult,
    fetch_activities,
)
from consolidado.wealthfolio_compat.models import ActivityDTO

NATUREZA_GERENCIAL = "gerencial"
STATUS_REALIZADO = "realizado"
TIPO_DESPESA = "despesa"
TIPO_RECEITA = "receita"

#: O maior tamanho de página que o Controle Bancário aceita.
TAMANHO_DA_PAGINA = 100
#: Teto de páginas por período (6.000 lançamentos). Acima disso o detalhe
#: fica indisponível, em vez de a tela esperar dezenas de segundos.
MAX_PAGINAS = 60
LEITURAS_SIMULTANEAS = 4
CATEGORIAS_VISIVEIS = 6
RECENTES = 8

Buscar = Callable[..., ActivitySourceResult]


class LeituraIncompletaError(Exception):
    """Uma página não veio: o período inteiro fica sem detalhe."""


@dataclass(frozen=True, slots=True)
class Periodo:
    inicio: date | None
    fim: date


#: Quantos meses cada período da aba recua para achar o anterior. "Este mês"
#: até o dia 23 compara com o mês passado até o dia 23, e o YTD com o mesmo
#: trecho do ano anterior: contar dias iguais para trás compararia setembro
#: parcial com o fim de agosto.
MESES_DO_PERIODO = {"este_mes": 1, "mes_passado": 1, "1m": 1, "3m": 3, "6m": 6, "ano": 12, "1a": 12, "5a": 60}


def _ultimo_dia(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _recuar(dia: date, meses: int, *, fim_do_mes: bool = False) -> date:
    total = dia.year * 12 + dia.month - 1 - meses
    ano, mes = divmod(total, 12)
    ultimo = _ultimo_dia(ano, mes + 1)
    return date(ano, mes + 1, ultimo if fim_do_mes else min(dia.day, ultimo))


def periodo_anterior(atual: Periodo, chave: str = "") -> Periodo | None:
    """O mesmo trecho do calendário, recuado; sem chave conhecida, os mesmos dias."""
    if atual.inicio is None:
        return None
    meses = MESES_DO_PERIODO.get(chave)
    if meses is None:
        dias = (atual.fim - atual.inicio).days + 1
        fim = atual.inicio - timedelta(days=1)
        return Periodo(fim - timedelta(days=dias - 1), fim)
    # Mês fechado continua fechado: agosto inteiro compara com julho inteiro.
    fim_do_mes = atual.fim.day == _ultimo_dia(atual.fim.year, atual.fim.month)
    fim = _recuar(atual.fim, meses, fim_do_mes=fim_do_mes)
    # Os períodos rolantes incluem as duas pontas (23/06 a 23/09): recuado, o
    # fim cairia no primeiro dia do atual, e esse dia contaria duas vezes.
    return Periodo(_recuar(atual.inicio, meses), min(fim, atual.inicio - timedelta(days=1)))


def _pagina(buscar: Buscar, fonte: Fonte, periodo: Periodo, numero: int) -> ActivitySourceResult:
    return buscar(
        fonte,
        inicio=periodo.inicio,
        fim=periodo.fim,
        page=numero,
        page_size=TAMANHO_DA_PAGINA,
        filters={"status": STATUS_REALIZADO, "natureza": NATUREZA_GERENCIAL},
    )


def _conferir(resultado: ActivitySourceResult, fonte: Fonte) -> ActivitySourceResult:
    if resultado.status == STATUS_ERROR:
        raise LeituraIncompletaError(f"{fonte.nome}: {resultado.error or 'a fonte não respondeu'}")
    return resultado


def _ler_periodo(
    executor: ThreadPoolExecutor, buscar: Buscar, fonte: Fonte, periodo: Periodo
) -> tuple[ActivityDTO, ...]:
    primeira = _conferir(_pagina(buscar, fonte, periodo, 1), fonte)
    if primeira.pages > MAX_PAGINAS:
        raise LeituraIncompletaError(
            f"{fonte.nome}: {primeira.total} lançamentos no período, acima do que esta tela lê de uma vez"
        )
    demais = executor.map(
        lambda numero: _pagina(buscar, fonte, periodo, numero),
        range(2, primeira.pages + 1),
    )
    atividades = list(primeira.activities)
    for resultado in demais:
        atividades.extend(_conferir(resultado, fonte).activities)
    return tuple(
        item
        for item in atividades
        if item.status == STATUS_REALIZADO and item.category_kind == NATUREZA_GERENCIAL
    )


def _valor(item: ActivityDTO) -> Decimal:
    return (item.realized_value or item.value).amount


def _despesas_por_moeda(atividades: tuple[ActivityDTO, ...]) -> dict[str, Decimal]:
    totais: dict[str, Decimal] = defaultdict(Decimal)
    for item in atividades:
        if item.kind == TIPO_DESPESA:
            totais[item.value.currency] += _valor(item)
    return dict(totais)


def _percentual(parte: Decimal, todo: Decimal) -> Decimal:
    return (parte * 100 / todo).quantize(Decimal("0.1")) if todo else Decimal("0")


def _rotulo_percentual(valor: Decimal) -> str:
    return f"{valor:.1f}".replace(".", ",") + "%"


def _categorias(atividades: tuple[ActivityDTO, ...]) -> list[dict]:
    por_categoria: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in atividades:
        if item.kind == TIPO_DESPESA:
            por_categoria[(item.value.currency, item.category or "Sem categoria")] += _valor(item)
    totais = _despesas_por_moeda(atividades)
    linhas = []
    for moeda in sorted(totais):
        itens = sorted(
            ((nome, valor) for (m, nome), valor in por_categoria.items() if m == moeda),
            key=lambda par: (-par[1], par[0]),
        )
        visiveis = itens[:CATEGORIAS_VISIVEIS]
        resto = itens[CATEGORIAS_VISIVEIS:]
        if resto:
            visiveis.append((f"Demais categorias ({len(resto)})", sum((v for _n, v in resto), Decimal("0"))))
        sufixo = f" · {moeda}" if len(totais) > 1 else ""
        for nome, valor in visiveis:
            percentual = _percentual(valor, totais[moeda])
            linhas.append(
                {
                    "label": f"{nome}{sufixo}",
                    "value": dinheiro(valor, moeda),
                    "percent": _rotulo_percentual(percentual),
                    "percent_number": percentual,
                }
            )
    return linhas


def _recentes(atividades: tuple[ActivityDTO, ...], links: dict[str, str]) -> list[dict]:
    ordenadas = sorted(atividades, key=lambda item: (item.date, item.id), reverse=True)
    linhas = []
    for item in ordenadas[:RECENTES]:
        receita = item.kind == TIPO_RECEITA
        valor = dinheiro(_valor(item), item.value.currency)
        linhas.append(
            {
                "date": " · ".join(
                    parte for parte in (item.date.strftime("%d/%m/%Y"), item.category) if parte
                ),
                "label": item.description,
                "value": f"+{valor}" if receita else f"−{valor}",
                "positive": receita,
                "url": links.get(item.id, ""),
            }
        )
    return linhas


def _comparacao(
    atuais: tuple[ActivityDTO, ...], anteriores: tuple[ActivityDTO, ...] | None, periodo: Periodo | None
) -> dict:
    if anteriores is None or periodo is None:
        return {"label": "Sem período anterior para comparar.", "positive": False}
    datas = f"{periodo.inicio:%d/%m/%Y} a {periodo.fim:%d/%m/%Y}"
    # Período sem lançamento nenhum é antes de a fonte existir (o CB começa em
    # 2026), não um período sem gasto: "R$ 148 mil a mais" seria falso.
    if not anteriores:
        return {"label": f"Sem lançamentos no período anterior ({datas}) para comparar.", "positive": False}
    atual = _despesas_por_moeda(atuais)
    anterior = _despesas_por_moeda(anteriores)
    partes = []
    gastou_menos = True
    for moeda in sorted(set(atual) | set(anterior)):
        agora = atual.get(moeda, Decimal("0"))
        antes = anterior.get(moeda, Decimal("0"))
        diferenca = agora - antes
        gastou_menos = gastou_menos and diferenca <= 0
        texto = f"{dinheiro(abs(diferenca), moeda)} {'a menos' if diferenca <= 0 else 'a mais'}"
        if antes:
            texto += f" ({'+' if diferenca > 0 else '−' if diferenca < 0 else ''}{_rotulo_percentual(abs(_percentual(diferenca, antes)))})"
        partes.append(texto)
    if not partes:
        return {"label": f"Sem gastos neste período nem no anterior ({datas}).", "positive": True}
    return {"label": f"{' · '.join(partes)} que no período anterior ({datas})", "positive": gastou_menos}


def detalhe(
    fontes: list[Fonte], periodo: Periodo, *, chave: str = "", buscar: Buscar = fetch_activities
) -> dict:
    """Categorias, recentes e comparação; ou indisponível, com o motivo."""
    caixa = [fonte for fonte in fontes if fonte.papel == "caixa"]
    if not caixa:
        return {"disponivel": False, "motivo": "Nenhuma fonte de caixa configurada."}
    anterior = periodo_anterior(periodo, chave)
    try:
        with ThreadPoolExecutor(max_workers=LEITURAS_SIMULTANEAS) as executor:
            atuais: list[ActivityDTO] = []
            anteriores: list[ActivityDTO] = []
            links: dict[str, str] = {}
            for fonte in caixa:
                lidas = _ler_periodo(executor, buscar, fonte, periodo)
                atuais.extend(lidas)
                links.update({item.id: fonte.link(item.link) for item in lidas if item.link})
                if anterior is not None:
                    anteriores.extend(_ler_periodo(executor, buscar, fonte, anterior))
    except LeituraIncompletaError as exc:
        return {"disponivel": False, "motivo": f"Lançamentos indisponíveis: {exc}."}
    atual = tuple(atuais)
    return {
        "disponivel": True,
        "motivo": "",
        "categorias": _categorias(atual),
        "recentes": _recentes(atual, links),
        "comparacao": _comparacao(atual, tuple(anteriores) if anterior is not None else None, anterior),
    }
