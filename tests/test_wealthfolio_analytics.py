from decimal import Decimal

from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.analytics import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_PARTIAL,
    STATUS_UNSUPPORTED,
    AnalyticsSourceResult,
    compose_analytics,
    fetch_all_analytics,
    fetch_analytics,
    normalize_analytics_payload,
)
from consolidado.wealthfolio_compat.transport import TransportResponse


def fonte(apelido="CRV"):
    return Fonte(apelido, "Fonte", "investimento", "http://fonte", "segredo")


def income_payload(source="controle-renda-variavel", **changes):
    payload = {
        "contrato": "patrimonio/v3",
        "recurso": "income",
        "sistema": source,
        "itens": [
            {
                "id": f"{source}:renda:1",
                "data": "2026-09-20",
                "descricao": "Dividendo de ABC3",
                "tipo": "dividendo",
                "moeda": "BRL",
                "valor": "12.34",
                "instrumento": "ABC3",
                "categoria": {"nome": "dividendo", "natureza": "renda"},
                "deep_link": "/portfolio/dividends/1",
            }
        ],
        "paginacao": {"total": 1, "pagina": 1, "tamanho": 100, "paginas": 1},
    }
    payload.update(changes)
    return payload


def test_income_payload_is_typed_without_mutating_external_mapping():
    original = income_payload()
    result = normalize_analytics_payload(original, resource="income", expected_source="crv")

    assert result.status == "ok"
    assert result.items[0].value.amount == Decimal("12.34")
    assert result.items[0].category == "dividendo"
    original["itens"][0]["descricao"] = "alterado"
    assert result.items[0].description == "Dividendo de ABC3"


def test_performance_and_events_keep_series_and_quantities_separate():
    performance = normalize_analytics_payload(
        {
            "contrato": "patrimonio/v3",
            "recurso": "performance",
            "sistema": "crv",
            "itens": [
                {
                    "id": "series-1",
                    "moeda": "USD",
                    "metodo": "TWR",
                    "inicio": "2026-01-01",
                    "fim": "2026-09-20",
                    "pontos": [{"data": "2026-09-01", "retorno_acumulado": "0.08"}],
                }
            ],
        },
        resource="performance",
    )
    events = normalize_analytics_payload(
        {
            "contrato": "patrimonio/v3",
            "recurso": "events",
            "sistema": "crv",
            "itens": [
                {
                    "id": "event-1",
                    "data": "2026-09-02",
                    "tipo": "movimentacao",
                    "instrumento": "ABC3",
                    "moeda": "BRL",
                    "quantidade_resultante": "10",
                }
            ],
        },
        resource="events",
    )

    assert performance.items[0].points[0][1] == Decimal("0.08")
    assert events.items[0].quantity == Decimal("10")


def test_unsupported_resource_is_visible_and_does_not_create_rows():
    class FakeTransport:
        def get(self, path, *, timeout, headers=None):
            assert path.startswith("/patrimonio/v3/income?")
            return TransportResponse(404, error="not found")

    result = fetch_analytics(fonte("CB"), resource="income", transport=FakeTransport())
    assert result.status == STATUS_UNSUPPORTED
    assert not result.items


def test_composition_marks_partial_when_only_one_source_publishes():
    available = normalize_analytics_payload(income_payload(), resource="income")
    unsupported = fetch_analytics(
        fonte("CB"), resource="income", transport=type("T", (), {"get": lambda *_a, **_k: TransportResponse(404)})()
    )
    result = compose_analytics("income", (available, unsupported))
    assert result.status == STATUS_PARTIAL
    assert result.available
    assert "não publica" in result.warnings[0]


def test_empty_payload_has_explicit_empty_state():
    result = normalize_analytics_payload(
        income_payload(itens=[], paginacao={"total": 0, "pagina": 1, "tamanho": 100, "paginas": 0}),
        resource="income",
    )
    assert result.status == STATUS_EMPTY


def _pagina_de_renda(total, tamanho, pagina):
    inicio = (pagina - 1) * tamanho
    itens = [
        {**income_payload()["itens"][0], "id": f"controle-renda-variavel:renda:{n}"}
        for n in range(inicio, min(total, inicio + tamanho))
    ]
    return income_payload(itens=itens, paginacao={"total": total, "pagina": pagina, "tamanho": tamanho})


def test_fetch_all_analytics_pede_paginas_de_100_ate_o_total():
    pedidos = []

    def fetch(fonte_, *, resource, inicio, fim, page, page_size):
        pedidos.append((page, page_size))
        return normalize_analytics_payload(_pagina_de_renda(230, page_size, page), resource=resource)

    result = fetch_all_analytics(fonte(), resource="income", fetch=fetch)

    assert pedidos == [(1, 100), (2, 100), (3, 100)]
    assert len(result.items) == result.total == 230


def test_fetch_all_analytics_devolve_erro_quando_uma_pagina_falha():
    def fetch(fonte_, *, resource, inicio, fim, page, page_size):
        if page == 2:
            return AnalyticsSourceResult("controle-renda-variavel", resource, STATUS_ERROR, error="a fonte respondeu HTTP 400")
        return normalize_analytics_payload(_pagina_de_renda(150, page_size, page), resource=resource)

    result = fetch_all_analytics(fonte(), resource="income", fetch=fetch)

    assert result.status == STATUS_ERROR and not result.items
