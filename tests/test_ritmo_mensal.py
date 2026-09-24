"""Ritmo mensal do patrimônio: variação das fotos, dividida entre sobra e o resto."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from consolidado import contexto

HOJE = date(2026, 9, 23)
VARIACAO = {"absoluto": Decimal("9131.25"), "percentual": Decimal("3"), "desde": date(2026, 6, 23)}


def fluxo(dia, liquido, natureza="gerencial", moeda="BRL"):
    return {"data": dia, "moeda": moeda, "natureza": natureza, "liquido": Decimal(liquido)}


def test_ritmo_divide_a_variacao_entre_sobra_gerencial_e_o_resto():
    fluxos = [
        fluxo(date(2026, 7, 10), "4000"),
        fluxo(date(2026, 8, 10), "-1000"),
        # Transferência não é sobra, e o dia da primeira foto já está nela.
        fluxo(date(2026, 8, 11), "50000", natureza="transferencia"),
        fluxo(date(2026, 6, 23), "777"),
    ]

    ritmo = contexto.ritmo_mensal_do_patrimonio(VARIACAO, fluxos, HOJE, "BRL")

    # 92 dias / 30,4375 = 3,0226 meses.
    assert ritmo["disponivel"] is True
    assert round(ritmo["ritmo"], 2) == Decimal("3021.00")
    (rotulo_sobra, sobra, _), (rotulo_resto, resto, _) = ritmo["fatores"]
    # Sobra de 3.000,00 (4.000 − 1.000) no período; o resto é a diferença.
    assert rotulo_sobra == "Receitas menos gastos" and round(sobra, 2) == Decimal("992.53")
    assert rotulo_resto == "Mercado, câmbio e ajustes" and round(resto, 2) == Decimal("2028.48")
    assert round(sobra + resto, 6) == round(ritmo["ritmo"], 6)


def test_periodo_curto_nao_vira_ritmo_mensal():
    variacao = {**VARIACAO, "desde": date(2026, 9, 1)}

    ritmo = contexto.ritmo_mensal_do_patrimonio(variacao, [], HOJE, "BRL")

    assert ritmo["disponivel"] is False and "curto" in ritmo["motivo"]


def test_gerencial_em_outra_moeda_recusa_em_vez_de_somar():
    ritmo = contexto.ritmo_mensal_do_patrimonio(VARIACAO, [fluxo(date(2026, 8, 1), "10", moeda="USD")], HOJE, "BRL")

    assert ritmo["disponivel"] is False and "USD" in ritmo["motivo"]


def test_sem_historico_de_patrimonio_diz_o_motivo():
    ritmo = contexto.ritmo_mensal_do_patrimonio(None, [], HOJE, "BRL")

    assert ritmo == {"disponivel": False, "motivo": "Sem histórico de patrimônio no período."}
