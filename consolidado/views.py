"""A tela que responde "quanto eu tenho".

Ela mostra os totais **por moeda**, e não um número só: somar reais com dólares
exige uma taxa, e taxa é decisão datada — ela entra na etapa do câmbio, com data
e fonte visíveis em cada número convertido. Até lá, dois números certos valem
mais que um número redondo e errado.

E ela nunca mostra um total sem dizer de quantas fontes ele é feito. O estado de
cada fonte vem no mesmo objeto que os totais, de propósito: é a única defesa
contra o defeito que importa aqui — o patrimônio "cair" porque um dos sistemas
estava reiniciando, com o número continuando plausível.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from consolidado import dashboard, fotos, grafico
from consolidado.cambio import MOEDA_BASE, converter_totais
from consolidado.leitor import consolidar_v2

#: Os recortes da tela de histórico. A chave vai na URL; o valor diz de quando
#: a curva começa (`None` é a história inteira).
PERIODOS = {
    "tudo": ("Tudo", None),
    "desde-2026": ("Desde 2026", fotos.INICIO_DO_PATRIMONIO),
    "12-meses": ("12 meses", "12m"),
}

PERIODOS_DASHBOARD = {
    "1m": ("1 mês", "1m"),
    "3m": ("3 meses", "3m"),
    "ano": ("Este ano", "ano"),
    "1a": ("1 ano", "1a"),
    "5a": ("5 anos", "5a"),
    "tudo": ("Tudo", None),
}

ROTULOS_DO_PAPEL = {"caixa": "Caixa", "investimento": "Investimentos"}
CASAS_DO_PERCENTUAL = Decimal("0.01")


def _inicio_do_periodo(chave: str, referencia: date) -> date | None:
    periodo_analitico = {
        "1m": "1M",
        "3m": "3M",
        "ano": "YTD",
        "1a": "1Y",
        "5a": "5Y",
        "tudo": "Tudo",
    }[chave]
    inicio, _fim = dashboard.intervalo_periodo(periodo_analitico, referencia)
    return inicio


def _valor_do_papel(blocos: list[dict], papel: str, referencia: date) -> Decimal | None:
    bloco = next((item for item in blocos if item["nome"] == papel), None)
    if bloco is None:
        return Decimal("0.00")
    conversao = converter_totais(bloco["totais_por_moeda"], referencia)
    return conversao.total if conversao.possivel else None


def _variacao(atual: Decimal | None, pontos: list[fotos.Ponto], atributo: str) -> dict | None:
    if atual is None:
        return None
    primeiro = next(
        (ponto for ponto in pontos if getattr(ponto, atributo) is not None),
        None,
    )
    if primeiro is None:
        return None
    anterior = getattr(primeiro, atributo)
    absoluto = atual - anterior
    return {
        "absoluto": absoluto,
        "percentual": grafico.variacao(atual, anterior),
        "desde": primeiro.data,
    }


def _incluir_foto_atual(
    pontos: list[fotos.Ponto],
    data_da_tela: date,
    patrimonio: Decimal | None,
    investimentos: Decimal | None,
) -> list[fotos.Ponto]:
    if patrimonio is None and investimentos is None:
        return pontos
    atuais = [ponto for ponto in pontos if ponto.data < data_da_tela]
    atuais.append(
        fotos.Ponto(
            data=data_da_tela,
            investimentos=investimentos,
            patrimonio=patrimonio,
        )
    )
    return atuais


def _desempenhos(consolidado) -> list[dict]:
    resultado = []
    for serie in consolidado.twr:
        pontos = serie.get("pontos") or []
        ultimo = pontos[-1] if pontos else None
        retorno = ultimo.get("retorno_acumulado") if ultimo else None
        resultado.append(
            {
                "moeda": serie.get("moeda", ""),
                "metodo": serie.get("metodo", ""),
                "ultimo": ultimo,
                "retorno_percentual": retorno * 100 if retorno is not None else None,
                "link": serie.get("link", ""),
            }
        )
    return resultado


def _cartoes(blocos: list[dict], referencia: date, rotulos: dict[str, str] | None = None) -> list[dict]:
    """Dá a cada agrupamento seus totais honestos e, quando dá, sua conversão.

    A regra de câmbio é a mesma do cartão principal. Assim, um cartão por
    instituição não troca dólares por reais sem dizer qual taxa permitiu isso.
    """
    cartoes = []
    for bloco in blocos:
        nome = rotulos.get(bloco["nome"], bloco["nome"]) if rotulos else bloco["nome"]
        cartoes.append(
            {
                "nome": nome,
                "totais_por_moeda": bloco["totais_por_moeda"],
                "conversao": converter_totais(bloco["totais_por_moeda"], referencia),
            }
        )
    return cartoes


def _composicao(blocos: list[dict], referencia: date, total: Decimal | None) -> list[dict]:
    """Fatias em moeda base que fecham exatamente 100%.

    Só é chamada depois que o consolidado inteiro pôde ser convertido. Arredondar
    cada fatia sem compensação faria barras que somam 99,99% ou 100,01%, uma
    discrepância pequena que dá a impressão errada de que algo ficou de fora.
    """
    if total is None or not total:
        return []
    fatias = []
    for bloco in blocos:
        conversao = converter_totais(bloco["totais_por_moeda"], referencia)
        if not conversao.possivel:
            return []
        fatias.append({"nome": bloco["nome"], "total": conversao.total})
    fatias.sort(key=lambda fatia: (-fatia["total"], fatia["nome"]))
    for fatia in fatias:
        fatia["percentual"] = (fatia["total"] * 100 / total).quantize(
            CASAS_DO_PERCENTUAL, rounding=ROUND_HALF_UP
        )
    fatias[0]["percentual"] += Decimal("100.00") - sum(
        fatia["percentual"] for fatia in fatias
    )
    return fatias


@login_required
def patrimonio_view(request):
    bruto = (request.GET.get("data") or "").strip()
    referencia = None
    data_invalida = False
    if bruto:
        try:
            referencia = date.fromisoformat(bruto)
        except ValueError:
            data_invalida = True

    periodo = request.GET.get("periodo") or "1a"
    if periodo not in PERIODOS_DASHBOARD:
        periodo = "1a"

    data_da_tela = referencia or timezone.localdate()
    inicio = _inicio_do_periodo(periodo, data_da_tela)
    inicio_da_consulta = inicio or fotos.INICIO_DOS_INVESTIMENTOS
    consolidado = consolidar_v2(
        inicio=inicio_da_consulta,
        data=data_da_tela,
        periodo="all",
    )
    # A taxa é a do dia da foto, não a de hoje: converter o passado pela taxa de
    # hoje faria o patrimônio de março mudar toda manhã.
    conversao = converter_totais(consolidado.totais_por_moeda, referencia or timezone.localdate())
    por_sistema = consolidado.por("papel")
    por_instituicao = consolidado.por("instituicao")
    por_mercado = consolidado.por_mercado()
    por_classe = consolidado.por("classe", "Caixa e não classificados")
    pontos = [
        ponto
        for ponto in fotos.curvas(desde=inicio)
        if ponto.data <= data_da_tela
    ]
    patrimonio_atual = (
        conversao.total if consolidado.completo and conversao.possivel else None
    )
    investimentos_atuais = (
        _valor_do_papel(por_sistema, "investimento", data_da_tela)
        if consolidado.completo
        else None
    )
    pontos = _incluir_foto_atual(
        pontos,
        data_da_tela,
        patrimonio_atual,
        investimentos_atuais,
    )
    desenho = grafico.montar(
        pontos,
        (
            ("patrimonio", "Patrimônio", "curva-patrimonio"),
            ("investimentos", "Investimentos", "curva-investimentos"),
        ),
    )
    return render(
        request,
        "consolidado/patrimonio.html",
        {
            "consolidado": consolidado,
            "conversao": conversao,
            "moeda_base": MOEDA_BASE,
            "referencia": referencia,
            "data_invalida": data_invalida,
            "por_instituicao": consolidado.por_instituicao(),
            "grafico": desenho,
            "primeira_data": pontos[0].data if pontos else None,
            "ultima_data": pontos[-1].data if pontos else None,
            "periodos_dashboard": [
                (valor, rotulo)
                for valor, (rotulo, _recorte) in PERIODOS_DASHBOARD.items()
            ],
            "periodo": periodo,
            "top_posicoes": dashboard.top_posicoes(consolidado, limite=10),
            "fluxos_por_natureza": dashboard.resumo_fluxos_por_natureza(consolidado),
            "rendas": consolidado.rendas,
            "ganhos_realizados": consolidado.ganhos_realizados,
            "desempenhos": _desempenhos(consolidado),
            "links_de_secao": consolidado.secoes,
            "qualidade_v2": consolidado.qualidade,
            "variacao_patrimonio": _variacao(patrimonio_atual, pontos[:-1], "patrimonio"),
            "variacao_investimentos": _variacao(
                investimentos_atuais, pontos[:-1], "investimentos"
            ),
            "cartoes_por_sistema": _cartoes(por_sistema, data_da_tela, ROTULOS_DO_PAPEL),
            "cartoes_por_instituicao": _cartoes(por_instituicao, data_da_tela),
            "composicoes": (
                [
                    ("Por moeda", _composicao(
                        [
                            {"nome": total["moeda"], "totais_por_moeda": [total]}
                            for total in consolidado.totais_por_moeda
                        ],
                        data_da_tela,
                        conversao.total,
                    )),
                    ("Por sistema", _composicao(por_sistema, data_da_tela, conversao.total)),
                    ("Por instituição", _composicao(
                        por_instituicao, data_da_tela, conversao.total
                    )),
                    ("Por mercado", _composicao(por_mercado, data_da_tela, conversao.total)),
                    ("Por classe", _composicao(por_classe, data_da_tela, conversao.total)),
                ]
                if conversao.possivel
                else []
            ),
        },
    )


@login_required
def historico_view(request):
    """As duas curvas, a partir das fotos gravadas. Nenhuma fonte é consultada."""
    chave = request.GET.get("periodo") or "tudo"
    if chave not in PERIODOS:
        chave = "tudo"
    inicio = PERIODOS[chave][1]
    if inicio == "12m":
        inicio = timezone.localdate() - timedelta(days=365)

    pontos = fotos.curvas(desde=inicio)
    desenho = grafico.montar(
        pontos,
        (
            ("patrimonio", "Patrimônio", "curva-patrimonio"),
            ("investimentos", "Investimentos", "curva-investimentos"),
        ),
    )
    mensais = grafico.ultimo_de_cada_mes(pontos)
    linhas = []
    for indice, ponto in enumerate(mensais):
        anterior = mensais[indice + 1] if indice + 1 < len(mensais) else None
        linhas.append(
            {
                "ponto": ponto,
                "variacao_investimentos": grafico.variacao(
                    ponto.investimentos, anterior.investimentos if anterior else None
                ),
                "variacao_patrimonio": grafico.variacao(
                    ponto.patrimonio, anterior.patrimonio if anterior else None
                ),
            }
        )
    return render(
        request,
        "consolidado/historico.html",
        {
            "grafico": desenho,
            "primeira_data": pontos[0].data if pontos else None,
            "ultima_data": pontos[-1].data if pontos else None,
            # Desde o início do caixa, patrimônio vazio só pode ser falta de taxa.
            "sem_taxa": sum(
                1
                for ponto in pontos
                if ponto.data >= fotos.INICIO_DO_PATRIMONIO and ponto.patrimonio is None
            ),
            "mensais": linhas,
            "moeda_base": MOEDA_BASE,
            "periodos": [(valor, rotulo) for valor, (rotulo, _inicio) in PERIODOS.items()],
            "periodo": chave,
            "inicio_do_patrimonio": fotos.INICIO_DO_PATRIMONIO,
        },
    )
