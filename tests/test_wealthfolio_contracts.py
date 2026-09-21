from decimal import Decimal

import pytest

from consolidado.wealthfolio_compat import (
    DeepLink,
    SourceStatus,
    TransportResponse,
    adapt_controle_bancario,
    adapt_payload,
    adapt_response,
    compose_results,
)


def cb_payload(**changes):
    payload = {
        "contrato": "patrimonio/v1",
        "sistema": "controle-bancario",
        "papel": "caixa",
        "data_de_referencia": "2026-09-19",
        "contas": [{"id": "7", "nome": "Principal", "moeda": "BRL", "saldo": "10"}],
        "totais_por_moeda": [{"moeda": "BRL", "total": "10", "linhas": 1}],
    }
    payload.update(changes)
    return payload


def test_adapter_success_preserva_decimal_e_deep_link_local():
    result = adapt_controle_bancario(cb_payload(contas=[{
        "id": "7",
        "nome": "Principal",
        "moeda": "BRL",
        "saldo": "10.25",
        "endereco": "/contas/7",
    }], totais_por_moeda=[{"moeda": "BRL", "total": "10.25", "linhas": 1}]))

    assert result.status == SourceStatus.OK
    assert result.snapshot is not None
    assert result.snapshot.accounts[0].balance.amount == Decimal("10.25")
    assert DeepLink("controle-bancario", result.snapshot.accounts[0].link).href == "/contas/7"


def test_adapter_explicit_states_do_not_become_zero_or_complete():
    stale = adapt_payload(cb_payload(status="stale"))
    assert stale.status == SourceStatus.STALE
    assert stale.snapshot is not None
    assert not stale.snapshot.complete

    empty = adapt_payload(cb_payload(contas=[], totais_por_moeda=[], status="empty"))
    assert empty.status == SourceStatus.EMPTY
    assert empty.snapshot is not None
    assert not empty.snapshot.accounts

    error = adapt_payload(cb_payload(status="error", erro="fonte indisponível"))
    assert error.status == SourceStatus.ERROR
    assert error.snapshot is None
    assert "indisponível" in error.error


def test_multiplas_moedas_sinalizam_fx_sem_somar():
    result = adapt_controle_bancario(cb_payload(
        contas=[
            {"id": "brl", "nome": "BRL", "moeda": "BRL", "saldo": "10"},
            {"id": "usd", "nome": "USD", "moeda": "USD", "saldo": "5"},
        ],
        totais_por_moeda=[
            {"moeda": "BRL", "total": "10", "linhas": 1},
            {"moeda": "USD", "total": "5", "linhas": 1},
        ],
    ))
    assert result.status == SourceStatus.FX_MISSING
    assert result.snapshot is not None
    assert result.snapshot.currencies == ("BRL", "USD")
    assert result.snapshot.coverage.fx_missing


def test_transport_error_e_composicao_parcial_sao_explicitos():
    result = adapt_response(TransportResponse(503, error="timeout"), expected_source="crv")
    assert result.status == SourceStatus.ERROR
    assert result.snapshot is None
    composed = compose_results((result, adapt_controle_bancario(cb_payload())))
    assert composed.coverage.status == SourceStatus.PARTIAL
    assert composed.coverage.responded_sources == 1


def test_deep_link_recusa_url_externa():
    with pytest.raises(ValueError):
        DeepLink("crv", "https://example.test/posicoes/1")
