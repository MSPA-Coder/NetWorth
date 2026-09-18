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

from consolidado import fotos, grafico
from consolidado.cambio import MOEDA_BASE, converter_totais
from consolidado.leitor import consolidar

#: Os recortes da tela de histórico. A chave vai na URL; o valor diz de quando
#: a curva começa (`None` é a história inteira).
PERIODOS = {
    "tudo": ("Tudo", None),
    "desde-2026": ("Desde 2026", fotos.INICIO_DO_PATRIMONIO),
    "12-meses": ("12 meses", "12m"),
}

ROTULOS_DO_PAPEL = {"caixa": "Caixa", "investimento": "Investimentos"}
CASAS_DO_PERCENTUAL = Decimal("0.01")


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

    consolidado = consolidar(referencia)
    # A taxa é a do dia da foto, não a de hoje: converter o passado pela taxa de
    # hoje faria o patrimônio de março mudar toda manhã.
    conversao = converter_totais(consolidado.totais_por_moeda, referencia or timezone.localdate())
    data_da_tela = referencia or timezone.localdate()
    por_sistema = consolidado.por("papel")
    por_instituicao = consolidado.por("instituicao")
    por_mercado = consolidado.por_mercado()
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
