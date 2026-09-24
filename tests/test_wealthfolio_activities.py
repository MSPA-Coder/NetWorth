from decimal import Decimal

import pytest

from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.activities import (
    compose_activities,
    fetch_activities,
    normalize_activities_payload,
)
from consolidado.wealthfolio_compat.models import DTOError
from consolidado.wealthfolio_compat.transport import TransportResponse


def payload(source="controle-bancario", **changes):
    value = {
        "contrato": "patrimonio/v3",
        "recurso": "atividades",
        "sistema": source,
        "itens": [
            {
                "id": f"{source}:atividade:1",
                "data": "2026-09-20",
                "descricao": "Salário",
                "tipo": "entrada",
                "status": "realizado",
                "moeda": "BRL",
                "valor": "1250.40",
                "categoria": {"nome": "Renda", "natureza": "gerencial"},
                "deep_link": "/transactions/1",
            }
        ],
        "paginacao": {"pagina": 1, "tamanho": 100, "total": 1, "paginas": 1},
    }
    value.update(changes)
    return value


def test_normalize_activities_is_immutable_and_prefixes_untrusted_ids():
    original = payload()
    result = normalize_activities_payload(original, expected_source="cb")

    assert result.status == "ok"
    assert result.activities[0].value.amount == Decimal("1250.40")
    assert result.activities[0].category_kind == "gerencial"
    original["itens"][0]["descricao"] = "alterado"
    assert result.activities[0].description == "Salário"


def test_descricao_vazia_vira_rotulo_e_descricao_ausente_continua_erro():
    vazia = payload()
    vazia["itens"][0]["descricao"] = "  "
    ausente = payload()
    del ausente["itens"][0]["descricao"]

    assert normalize_activities_payload(vazia).activities[0].description == "(sem descrição)"
    with pytest.raises(DTOError):
        normalize_activities_payload(ausente)


def test_compose_activities_orders_sources_without_mixing_currencies():
    newer = normalize_activities_payload(payload("controle-bancario"))
    older = normalize_activities_payload(payload("controle-renda-variavel", itens=[{
        "id": "raw-id",
        "data": "2026-09-19",
        "descricao": "Dividendo",
        "tipo": "dividendo",
        "status": "realizado",
        "moeda": "USD",
        "valor": "5.00",
    }]))
    combined = compose_activities((older, newer))

    assert [item.source for item in combined.activities] == ["controle-bancario", "controle-renda-variavel"]
    assert combined.coverage.complete is True
    assert combined.activities[1].id.startswith("controle-renda-variavel:atividade:")


def test_fetch_activities_is_get_only_and_exposes_http_failure():
    class FakeTransport:
        def __init__(self):
            self.calls = []

        def get(self, path, *, timeout, headers=None):
            self.calls.append((path, timeout, headers))
            return TransportResponse(503, error="offline")

    transport = FakeTransport()
    result = fetch_activities(
        Fonte("CB", "Controle Bancário", "caixa", "http://cb", "secret"),
        filters={"status": "realizado", "natureza": "gerencial"},
        transport=transport,
    )

    assert result.status == "error"
    assert result.activities == ()
    assert transport.calls[0][0].startswith("/patrimonio/v3/activities?")
    assert "status=realizado" in transport.calls[0][0]
    assert "natureza=gerencial" in transport.calls[0][0]
    assert transport.calls[0][2]["Authorization"] == "Bearer secret"


def test_empty_payload_has_explicit_empty_state():
    result = normalize_activities_payload(payload(itens=[], paginacao={"total": 0, "pagina": 1, "tamanho": 100, "paginas": 0}))
    assert result.status == "empty"
    assert result.activities == ()
