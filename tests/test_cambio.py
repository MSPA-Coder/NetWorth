"""Converter para moeda base -- e recusar converter quando não dá.

O QUE ESTE ARQUIVO PROTEGE

Um total em reais é a coisa mais fácil de produzir errado neste aplicativo: basta
pegar a taxa de hoje e multiplicar. O número sai redondo, plausível, e muda toda
manhã -- inclusive o patrimônio de março, que já aconteceu e não deveria mudar
nunca mais.

Então os testes aqui medem as quatro regras: a taxa é a do dia da foto; nunca
uma posterior; sexta serve para sábado (e a tela diz qual dia foi usado); e taxa
velha demais não converte, ela recusa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from consolidado import cambio
from consolidado.models import TaxaDeCambio

pytestmark = pytest.mark.django_db


def taxa(dia: str, valor: str, moeda: str = "USD", fonte: str = "yahoo") -> TaxaDeCambio:
    return TaxaDeCambio.objects.create(
        moeda=moeda, data=date.fromisoformat(dia), taxa=Decimal(valor), fonte=fonte
    )


def totais(**por_moeda) -> list[dict]:
    return [
        {"moeda": moeda, "total": Decimal(valor), "linhas": 1}
        for moeda, valor in por_moeda.items()
    ]


# --- A taxa é a do dia da foto ---------------------------------------------


def test_usa_a_taxa_do_dia_pedido():
    taxa("2026-09-15", "5.10")
    taxa("2026-09-16", "5.20")
    taxa("2026-09-17", "5.30")

    aplicada = cambio.taxa_para("USD", date(2026, 9, 16))

    assert aplicada.taxa == Decimal("5.20")
    assert aplicada.data == date(2026, 9, 16)
    assert aplicada.dias_de_defasagem == 0


def test_nunca_usa_taxa_posterior_a_data_pedida():
    """Ela não existia ainda. Converter o passado com ela reescreve a história."""
    taxa("2026-09-20", "5.90")

    assert cambio.taxa_para("USD", date(2026, 9, 16)) is None


def test_sexta_serve_para_sabado_e_a_defasagem_fica_a_vista():
    """Câmbio não fecha no fim de semana. O que não se faz é fingir que a taxa
    é do dia pedido."""
    taxa("2026-09-18", "5.25")  # sexta

    aplicada = cambio.taxa_para("USD", date(2026, 9, 19))  # sábado

    assert aplicada.taxa == Decimal("5.25")
    assert aplicada.data == date(2026, 9, 18)
    assert aplicada.dias_de_defasagem == 1
    assert aplicada.defasada is False


def test_taxa_velha_demais_e_marcada_como_defasada():
    taxa("2026-08-01", "5.00")

    aplicada = cambio.taxa_para("USD", date(2026, 9, 16))

    assert aplicada.dias_de_defasagem == 46
    assert aplicada.defasada is True


def test_moeda_base_nao_tem_taxa():
    assert cambio.taxa_para(cambio.MOEDA_BASE, date(2026, 9, 16)) is None


def test_ptax_ganha_do_yahoo_no_mesmo_dia():
    """As duas convivem; quando há escolha, vale a oficial."""
    taxa("2026-09-16", "5.20", fonte="yahoo")
    taxa("2026-09-16", "5.2187", fonte="ptax")

    aplicada = cambio.taxa_para("USD", date(2026, 9, 16))

    assert aplicada.fonte == "ptax"
    assert aplicada.taxa == Decimal("5.2187")


# --- Converter, ou recusar --------------------------------------------------


def test_converte_e_soma_com_a_moeda_base():
    taxa("2026-09-16", "5.20")

    conversao = cambio.converter_totais(
        totais(BRL="37711.83", USD="8067.22"), date(2026, 9, 16)
    )

    assert conversao.possivel
    # 8067,22 x 5,20 = 41.949,544 -> 41.949,54
    assert conversao.total == Decimal("79661.37")
    (aplicada,) = conversao.taxas
    assert aplicada.data == date(2026, 9, 16)
    assert aplicada.fonte == "yahoo"


def test_sem_taxa_nao_ha_total_em_moeda_base():
    """Tudo ou nada: somar só as moedas que dá produziria um patrimônio menor
    com cara de completo."""
    conversao = cambio.converter_totais(
        totais(BRL="37711.83", USD="8067.22"), date(2026, 9, 16)
    )

    assert conversao.possivel is False
    assert conversao.total is None
    assert conversao.sem_taxa == ("USD",)
    assert "sem taxa para USD" in conversao.motivo


def test_taxa_defasada_recusa_a_conversao_e_diz_de_quando_ela_e():
    taxa("2026-08-01", "5.00")

    conversao = cambio.converter_totais(
        totais(BRL="10.00", USD="100.00"), date(2026, 9, 16)
    )

    assert conversao.possivel is False
    assert conversao.defasadas[0].data == date(2026, 8, 1)
    assert "velha demais" in conversao.motivo
    assert "01/08/2026" in conversao.motivo


def test_so_moeda_base_converte_sem_taxa_nenhuma():
    conversao = cambio.converter_totais(totais(BRL="37711.83"), date(2026, 9, 16))

    assert conversao.possivel
    assert conversao.total == Decimal("37711.83")
    assert conversao.taxas == ()


def test_sem_nada_a_converter_nao_ha_total():
    """Zero não é "não devo nada": é "não há o que mostrar"."""
    conversao = cambio.converter_totais([], date(2026, 9, 16))

    assert conversao.possivel is False


def test_a_taxa_de_ontem_nao_muda_o_patrimonio_de_ontem():
    """O teste que justifica guardar série em vez de consultar taxa de hoje."""
    taxa("2026-09-10", "5.00")
    taxa("2026-09-16", "6.00")

    antes = cambio.converter_totais(totais(USD="100.00"), date(2026, 9, 10))
    depois = cambio.converter_totais(totais(USD="100.00"), date(2026, 9, 16))

    assert antes.total == Decimal("500.00")
    assert depois.total == Decimal("600.00")
    # E o de 10/09 continua 500 mesmo depois de a taxa de 16/09 existir.
    assert cambio.converter_totais(totais(USD="100.00"), date(2026, 9, 10)).total == Decimal("500.00")


# --- O banco ---------------------------------------------------------------


def test_a_mesma_moeda_no_mesmo_dia_pela_mesma_fonte_nao_duplica():
    from django.db import IntegrityError, transaction

    taxa("2026-09-16", "5.20")

    with pytest.raises(IntegrityError), transaction.atomic():
        taxa("2026-09-16", "5.99")


def test_serie_carregada_responde_igual_a_consulta_por_data():
    """A série existe para o histórico não fazer uma consulta por foto. Ela não
    pode ter regra própria: para cada data, a resposta é a de `taxa_para`."""
    taxa("2026-09-10", "5.00")
    taxa("2026-09-11", "5.10")
    taxa("2026-09-11", "5.05", fonte="ptax")
    taxa("2026-09-14", "5.20")
    taxa("2026-09-01", "1.10", moeda="EUR")
    serie = cambio.SerieDeTaxas(["USD", "EUR", "BRL"])

    for dia in range(1, 30):
        referencia = date(2026, 9, dia)
        for moeda in ("USD", "EUR", "BRL", "GBP"):
            assert serie.para(moeda, referencia) == cambio.taxa_para(moeda, referencia), (
                moeda,
                referencia,
            )


def test_conversao_pela_serie_carregada_segue_as_mesmas_regras():
    taxa("2026-09-01", "5.00")
    serie = cambio.SerieDeTaxas(["USD"])

    no_prazo = cambio.converter_totais(totais(USD="10.00"), date(2026, 9, 8), serie)
    velha = cambio.converter_totais(totais(USD="10.00"), date(2026, 9, 9), serie)

    assert no_prazo.total == Decimal("50.00")
    assert velha.total is None
    assert velha.defasadas


def test_taxa_negativa_e_recusada_pelo_banco():
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        taxa("2026-09-16", "-1.00")
