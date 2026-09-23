"""A tela de patrimônio projetado e o cartão dela no Dashboard.

Os números vêm de `consolidado.projecao`; aqui eles só viram texto formatado
para o template. Toda premissa que muda o número vira uma linha visível em
"Premissas" -- a tela não pode parecer mais certa do que é.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from consolidado import fotos, grafico, projecao
from consolidado.cambio import MOEDA_BASE
from consolidado.fontes import Fonte, fontes_configuradas
from consolidado.templatetags.dinheiro import dinheiro
from consolidado.wealthfolio_compat.activities import compose_activities, fetch_activities
from consolidado.wealthfolio_views import _base_context, _shell_vm

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
PROXIMOS_DIAS = 30
PROXIMOS_LIMITE = 8


@dataclass(frozen=True, slots=True)
class _PontoDoGrafico:
    data: date
    historico: Decimal | None = None
    projetado: Decimal | None = None
    caixa: Decimal | None = None


def _data_curta(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


def _mes_curto(dia: date) -> str:
    return f"{MESES[dia.month - 1]}/{dia.year % 100:02d}"


def _valores(valores: dict[str, Decimal] | None) -> str:
    """Formata por moeda, sem somar moedas diferentes."""
    if not valores:
        return "—"
    return " · ".join(dinheiro(valor, moeda) for moeda, valor in sorted(valores.items()))


def _fonte_de_caixa() -> Fonte | None:
    return next((fonte for fonte in fontes_configuradas() if fonte.papel == "caixa"), None)


def _horizonte(request: HttpRequest) -> str:
    chave = request.GET.get("horizonte") or projecao.HORIZONTE_PADRAO
    return chave if chave in projecao.HORIZONTES else projecao.HORIZONTE_PADRAO


def _ate(chave: str, hoje: date, lida: projecao.ProjecaoDeCaixa) -> date:
    dias = projecao.HORIZONTES[chave][1]
    return lida.fim_da_projecao if dias is None else hoje + timedelta(days=dias)


def _menor_caixa(projetado: projecao.Projetado) -> list[dict[str, str]]:
    menores = []
    for moeda in projetado.projecao.moedas:
        dia, valor = min(
            ((ponto.data, ponto.caixa[moeda]) for ponto in projetado.pontos),
            key=lambda item: (item[1], item[0]),
        )
        menores.append({"moeda": moeda, "valor": dinheiro(valor, moeda), "data": _data_curta(dia), "negativo": valor < 0})
    return menores


def _premissas(projetado: projecao.Projetado, fonte: Fonte) -> list[str]:
    lida = projetado.projecao
    linhas = []
    if lida.ultima_recorrencia:
        linhas.append(
            f"Lançamentos do {fonte.nome}; as recorrências estão geradas até "
            f"{_data_curta(lida.ultima_recorrencia)}. Depois disso a curva só mostra lançamentos avulsos."
        )
    else:
        linhas.append(f"Lançamentos do {fonte.nome}; não há recorrências geradas.")
    if projetado.tem_patrimonio:
        linhas.append("Investimentos pelo valor de hoje, sem estimar rendimento. Aportes programados voltam ao patrimônio no dia do aporte.")
    for bloco in lida.vencidos:
        tratamento = "incluídos no saldo de hoje" if lida.vencidos_incluidos else "fora do saldo"
        linhas.append(
            f"{bloco.quantidade} lançamento(s) vencido(s) e não realizado(s) em {bloco.moeda} "
            f"(entradas {dinheiro(bloco.entradas, bloco.moeda)}, saídas {dinheiro(bloco.saidas, bloco.moeda)}), {tratamento}."
        )
    conversao = projetado.conversao
    if conversao is not None:
        for taxa in conversao.taxas:
            linhas.append(
                f"{taxa.moeda} convertido pela taxa de {_data_curta(taxa.data)} ({taxa.fonte}) também nas datas futuras."
            )
        faltantes = list(conversao.sem_taxa) + [taxa.moeda for taxa in conversao.defasadas]
        if faltantes:
            linhas.append(
                f"Sem taxa válida para {', '.join(faltantes)}: o total em {MOEDA_BASE} não aparece, só os valores por moeda."
            )
    linhas.append("Proventos anunciados e ainda não pagos não entram: o Controle de Renda Variável ainda não os publica.")
    return linhas


def _grafico(projetado: projecao.Projetado, hoje: date, historico_desde: date, total_hoje: Decimal | None):
    pontos = [
        _PontoDoGrafico(data=ponto.data, historico=ponto.patrimonio)
        for ponto in fotos.curvas(desde=historico_desde)
        if ponto.data < hoje
    ]
    for indice, ponto in enumerate(projetado.pontos):
        pontos.append(
            _PontoDoGrafico(
                data=ponto.data,
                historico=total_hoje if indice == 0 else None,
                projetado=projetado.em_moeda_base(ponto.patrimonio),
                caixa=projetado.em_moeda_base(ponto.caixa),
            )
        )
    destaques = [(hoje, "hoje")]
    ultima = projetado.projecao.ultima_recorrencia
    if ultima and hoje < ultima < projetado.ate:
        destaques.append((ultima, "fim das recorrências"))
    return grafico.montar(
        pontos,
        (
            ("historico", "Patrimônio", "curva-patrimonio"),
            ("projetado", "Patrimônio projetado", "curva-projetada"),
            ("caixa", "Caixa projetado", "curva-caixa"),
        ),
        destaques=tuple(destaques),
    )


def _proximos(fonte: Fonte, hoje: date) -> dict[str, Any]:
    composicao = compose_activities(
        [
            fetch_activities(
                fonte,
                inicio=hoje,
                fim=hoje + timedelta(days=PROXIMOS_DIAS),
                page_size=100,
                filters={"status": "a_vencer"},
            )
        ]
    )
    itens = sorted(composicao.activities, key=lambda item: (item.date, item.id))[:PROXIMOS_LIMITE]
    return {
        "disponivel": composicao.coverage.available,
        "motivo": "; ".join(composicao.coverage.omissions),
        "itens": [
            {
                "data": _data_curta(item.date),
                "descricao": item.description,
                "conta": item.account,
                "categoria": item.category,
                "valor": dinheiro(item.value.amount, item.value.currency),
                "entrada": item.kind == "receita",
                "investimento": item.category_kind == "movimentacao",
                "link": fonte.link(item.link) if item.link else "",
            }
            for item in itens
        ],
    }


def pagina(request: HttpRequest, context: dict[str, Any], hoje: date) -> dict[str, Any]:
    """Monta `wf_page` da tela. Sem fonte de caixa ou sem projeção, é estado de erro."""
    chave = _horizonte(request)
    horizontes = [
        {
            "rotulo": rotulo,
            "url": f"{reverse('consolidado:projecao')}?{urlencode({'horizonte': valor})}",
            "ativo": valor == chave,
        }
        for valor, (rotulo, _dias) in projecao.HORIZONTES.items()
    ]
    base = {
        "kind": "projection",
        "kicker": "Planejamento",
        "title": "Patrimônio projetado",
        "subtitle": "Se o que está lançado no Controle Bancário acontecer, quanto você terá.",
        "horizontes": horizontes,
        "actions": [{"label": "Painel", "url": reverse("consolidado:dashboard")}],
        "meta": {
            "complete": context["consolidado"].completo,
            "warning": context["wf_shell"]["partial_message"],
            "sources_responded": context["wf_shell"]["sources_responded"],
            "sources_expected": context["wf_shell"]["sources_expected"],
        },
    }
    fonte = _fonte_de_caixa()
    if fonte is None:
        return {**base, "state": "error", "error_title": "Controle Bancário não configurado",
                "error_message": "Sem a fonte de caixa não há o que projetar."}
    leitura = projecao.buscar(fonte, projecao.fim_do_pedido(chave, hoje))
    if leitura.projecao is None:
        return {**base, "state": "error", "error_title": "Projeção indisponível",
                "error_message": f"{fonte.nome}: {leitura.motivo}.", "retry_url": request.get_full_path()}

    lida = leitura.projecao
    investimentos = projecao.investimentos_por_moeda(context["consolidado"])
    projetado = projecao.montar(lida, investimentos, _ate(chave, hoje, lida))
    total_hoje = context["total"] if context["consolidado"].completo else None
    final = projetado.pontos[-1]
    total_final = projetado.em_moeda_base(final.patrimonio)
    variacao = total_final - total_hoje if total_final is not None and total_hoje is not None else None
    # Histórico do mesmo tamanho da projeção, até 240 dias: a janela inteira
    # fica abaixo de 540 dias e o eixo mostra meses em vez de anos.
    dias_de_historico = max(60, min(240, (projetado.ate - hoje).days))
    geometria = _grafico(projetado, hoje, hoje - timedelta(days=dias_de_historico), total_hoje)

    return {
        **base,
        "state": "ready",
        "as_of_label": f"Base {_data_curta(lida.data_base)} · até {_data_curta(projetado.ate)}",
        "actions": [
            *base["actions"],
            *([{"label": "Relatório no Controle Bancário", "url": lida.meses[0].link}] if lida.meses and lida.meses[0].link else []),
        ],
        "tem_patrimonio": projetado.tem_patrimonio,
        "sem_patrimonio_motivo": (
            "" if projetado.tem_patrimonio
            else "Sem todas as fontes não há patrimônio projetado honesto; abaixo, só o caixa."
        ),
        "metricas": {
            "hoje": dinheiro(total_hoje, MOEDA_BASE) if total_hoje is not None else "—",
            "final_rotulo": f"Em {_data_curta(projetado.ate)}",
            "final": (
                dinheiro(total_final, MOEDA_BASE) if total_final is not None
                else _valores(final.patrimonio if final.patrimonio is not None else final.caixa)
            ),
            "final_e_caixa": final.patrimonio is None,
            "variacao": (f"{'+' if variacao > 0 else ''}{dinheiro(variacao, MOEDA_BASE)}" if variacao is not None else "—"),
            "variacao_positiva": variacao is not None and variacao >= 0,
            "menor_caixa": _menor_caixa(projetado),
        },
        "grafico": geometria,
        "contas_negativas": [
            {
                "nome": conta.nome,
                "valor": dinheiro(conta.menor_saldo, conta.moeda),
                "data": _data_curta(conta.data_do_menor),
                "link": conta.link,
            }
            for conta in lida.contas
            if conta.menor_saldo < 0 and conta.data_do_menor <= projetado.ate
        ],
        "meses": [
            {
                "mes": _mes_curto(linha.mes),
                "moeda": linha.moeda,
                "entradas": dinheiro(linha.entradas, linha.moeda),
                "saidas": dinheiro(linha.saidas, linha.moeda),
                "para_investir": dinheiro(linha.para_investir, linha.moeda),
                "caixa": dinheiro(linha.caixa_no_fim, linha.moeda),
                "patrimonio": dinheiro(linha.patrimonio_no_fim, linha.moeda) if linha.patrimonio_no_fim is not None else "—",
                "link": linha.link,
            }
            for linha in projetado.meses
        ],
        "proximos": _proximos(fonte, hoje),
        "premissas": _premissas(projetado, fonte),
    }


@login_required
@require_GET
def projecao_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="patrimonio")
    hoje = date.today()
    context["page_type"] = "projection"
    context["wf_shell"] = _shell_vm(request, context, active="projection")
    context["wf_page"] = pagina(request, context, hoje)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


def cartao_do_painel(context: dict[str, Any], hoje: date) -> dict[str, Any] | None:
    """O resumo "em 6 meses" da aba Patrimônio líquido. Some em silêncio se falhar.

    O cartão é um atalho, não o número principal da aba: se a fonte não
    publica a projeção, ele não aparece, e a tela de projeção diz o motivo.
    """
    fonte = _fonte_de_caixa()
    if fonte is None or not context["consolidado"].completo:
        return None
    leitura = projecao.buscar(fonte, projecao.fim_do_pedido(projecao.HORIZONTE_PADRAO, hoje))
    if leitura.projecao is None:
        return None
    projetado = projecao.montar(
        leitura.projecao,
        projecao.investimentos_por_moeda(context["consolidado"]),
        _ate(projecao.HORIZONTE_PADRAO, hoje, leitura.projecao),
    )
    total = projetado.em_moeda_base(projetado.pontos[-1].patrimonio)
    if total is None:
        return None
    return {
        "rotulo": f"Em {_data_curta(projetado.ate)}",
        "valor": dinheiro(total, MOEDA_BASE),
        "url": reverse("consolidado:projecao"),
    }
