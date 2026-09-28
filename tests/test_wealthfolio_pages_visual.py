"""Páginas derivadas do shell Wealthfolio: drill-down, estados e abas.

Rótulos, cabeçalhos e a estrutura visual copiada do Wealthfolio não são
testados aqui (docs/TESTES.md, T5): mudam por decisão de tela e se conferem
olhando a tela. Fica o que o servidor decide -- o nome com espaço e acento
chega ao detalhe, a posição inexistente tem estado próprio, a aba vem da
query. Somente leitura (405) está em `test_wealthfolio_parity_matrix.py`.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.analytics import AnalyticsSourceResult

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


@pytest.fixture
def pages_client(monkeypatch):
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(
                        fonte=CB.nome,
                        papel="caixa",
                        titular="Espósito",
                        instituicao="Mercado Pago",
                        descricao="conta 01",
                        moeda="BRL",
                        valor=Decimal("100.00"),
                    )
                ],
            ),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(
                        fonte=CRV.nome,
                        papel="investimento",
                        titular="Pessoa",
                        instituicao="Corretora",
                        descricao="ETF Brasil",
                        moeda="BRL",
                        valor=Decimal("50.00"),
                        quantidade=Decimal("2"),
                        link="/positions/4",
                    )
                ],
            ),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: snapshot)
    user = get_user_model().objects.create_user("visual-owner", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(user)
    return client


def test_account_detail_keeps_unicode_drilldown_and_read_only(pages_client):
    response = pages_client.get("/accounts/Mercado%20Pago/")
    assert response.status_code == 200
    assert "conta 01" in response.content.decode()
    assert pages_client.post("/accounts/Mercado%20Pago/").status_code == 405


def test_accounts_list_uses_opaque_child_links_search_and_map_mode(pages_client):
    response = pages_client.get("/accounts/", {"periodo": "1a", "data": "2026-09-27"})
    assert response.status_code == 200
    rows = response.context["wf_page"]["rows"]
    assert {row["name"] for row in rows} == {"Mercado Pago · conta 01", "Corretora"}
    assert all("account_id=conta-" in row["url"] for row in rows)
    assert "Mercado%20Pago" in next(row["url"] for row in rows if row["institution"] == "Mercado Pago")

    filtered = pages_client.get("/accounts/", {"busca": "mercado", "periodo": "1a"})
    assert [row["institution"] for row in filtered.context["wf_page"]["rows"]] == ["Mercado Pago"]
    assert "Corretora" not in filtered.content.decode()

    mapa = pages_client.get("/accounts/", {"modo": "mapa", "periodo": "1a"})
    assert mapa.context["wf_page"]["accounts_mode"] == "mapa"
    assert "wf2p-account-map" in mapa.content.decode()


def test_holding_detail_and_state_contracts_remain_present(pages_client):
    detail = pages_client.get("/holdings/ETF%20Brasil/")
    assert detail.status_code == 200
    missing = pages_client.get("/holdings/Não%20existe/")
    assert missing.status_code == 200
    assert "Posição não encontrada" in missing.content.decode()


def test_holding_detail_shows_only_published_quantity_events_for_exact_position(pages_client, monkeypatch):
    published = (
        SimpleNamespace(
            id="event-match", date=date(2026, 8, 12), kind="movimentacao",
            instrument="ETF Brasil", currency="BRL", source="controle-renda-variavel",
            quantity=Decimal("7.5"), link="/positions/4",
        ),
        SimpleNamespace(
            id="event-other-position", date=date(2026, 8, 13), kind="movimentacao",
            instrument="ETF Brasil", currency="BRL", source="controle-renda-variavel",
            quantity=Decimal("99"), link="/positions/5",
        ),
        SimpleNamespace(
            id="event-other-ticker", date=date(2026, 8, 14), kind="movimentacao",
            instrument="OUTRO3", currency="BRL", source="controle-renda-variavel",
            quantity=Decimal("100"), link="/positions/4",
        ),
    )
    calls = []

    def fetch_all(source, *, resource, inicio, fim, extra=None):
        calls.append((source.apelido, resource, inicio, fim, extra))
        status = "unsupported" if resource == "holding-history" else "ok"
        return AnalyticsSourceResult(source="controle-renda-variavel", resource=resource, status=status, items=published if status == "ok" else (), total=len(published) if status == "ok" else 0)

    monkeypatch.setattr(wealthfolio_views, "fetch_all_analytics", fetch_all)
    response = pages_client.get("/holdings/ETF%20Brasil/?periodo=3m&data=2026-09-27")

    assert response.status_code == 200
    section = response.context["wf_page"]["sections"][0]
    assert [(event["date"], event["kind"], event["quantity"]) for event in section["events"]] == [
        ("12/08/2026", "movimentacao", "7,5"),
    ]
    source = next(item for item in wealthfolio_views.fontes_configuradas() if item.apelido.casefold() == "crv")
    assert section["events"][0]["source_url"] == source.link("/positions/4")
    body = response.content.decode()
    assert "Histórico publicado da posição" in body
    assert "Histórico de preços/valores não publicado" in body
    assert {event["quantity"] for event in section["events"]}.isdisjoint({"99", "100"})
    assert calls == [
        ("CRV", "holding-history", date(2026, 6, 27), date(2026, 9, 27), {"ticker": "ETF Brasil"}),
        ("CRV", "events", date(2026, 6, 27), date(2026, 9, 27), None),
    ]


def test_holding_detail_prefers_source_price_history_when_published(pages_client, monkeypatch):
    point = SimpleNamespace(
        id="point-1",
        date=date(2026, 9, 1),
        currency="BRL",
        price=Decimal("25.00"),
        quantity=Decimal("2"),
        value=Decimal("50.00"),
        price_date=date(2026, 9, 1),
    )
    previous_point = SimpleNamespace(
        id="point-0",
        date=date(2026, 8, 1),
        currency="BRL",
        price=Decimal("20.00"),
        quantity=Decimal("2"),
        value=Decimal("40.00"),
        price_date=date(2026, 8, 1),
    )

    def fetch_all(source, *, resource, inicio, fim, extra=None):
        items = (previous_point, point) if resource == "holding-history" else ()
        return AnalyticsSourceResult(
            source="controle-renda-variavel", resource=resource, status="ok", items=items, total=len(items)
        )

    monkeypatch.setattr(wealthfolio_views, "fetch_all_analytics", fetch_all)
    response = pages_client.get("/holdings/ETF%20Brasil/?periodo=3m&data=2026-09-27")

    assert response.status_code == 200
    section = response.context["wf_page"]["sections"][0]
    assert section["title"] == "Evolução de mercado"
    assert section["points"][0]["price"] == "R$ 25,00"
    assert section["points"][0]["value"] == "R$ 50,00"
    assert section["chart"] is not None
    assert "wf2p-history-chart" in response.content.decode()
    assert "Evolução de mercado" in response.content.decode()


def test_holding_detail_keeps_fallback_when_source_has_no_matching_events(pages_client, monkeypatch):
    monkeypatch.setattr(
        wealthfolio_views,
        "fetch_all_analytics",
        lambda source, *, resource, inicio, fim, extra=None: AnalyticsSourceResult(
            source="controle-renda-variavel", resource=resource, status="empty", total=0
        ),
    )
    response = pages_client.get("/holdings/ETF%20Brasil/?periodo=3m&data=2026-09-27")

    assert response.status_code == 200
    section = response.context["wf_page"]["sections"][0]
    assert section["events"] == ()
    assert section["empty_title"] == "Nenhuma movimentação publicada"
    assert "no período selecionado" in section["empty_message"]


def test_settings_section_and_holdings_tab_are_query_driven(pages_client):
    settings = pages_client.get("/settings/", {"secao": "sobre", "periodo": "1a"})
    assert settings.status_code == 200
    settings_page = settings.context["wf_page"]
    assert settings_page["settings_section"] == "sobre"
    active_settings = [
        item
        for group in settings_page["settings_nav_groups"]
        for item in group["items"]
        if item["active"]
    ]
    assert [(item["key"], item["url"]) for item in active_settings] == [("sobre", "/settings/?secao=sobre&periodo=1a")]

    holdings = pages_client.get("/holdings/", {"tipo": "ativos", "periodo": "1a"})
    assert holdings.status_code == 200
    tabs = {tab["key"]: tab for tab in holdings.context["wf_page"]["holding_tabs"]}
    assert tabs["ativos"]["active"] is True
    assert tabs["investimentos"]["active"] is False
    assert "periodo=1a" in tabs["passivos"]["url"]


def test_holdings_filters_apply_to_published_positions_and_keep_currency_boundaries(pages_client, monkeypatch):
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[]),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(
                        CRV.nome, "investimento", "Mariano", "XP", "PETR4", "BRL", Decimal("100"),
                        quantidade=Decimal("10"), classe="ação", custo=Decimal("90"),
                        ganho_nao_realizado=Decimal("10"),
                    ),
                    leitor.Linha(
                        CRV.nome, "investimento", "Cláudia", "XP", "MSFT", "USD", Decimal("80"),
                        quantidade=Decimal("2"), classe="ação", custo=Decimal("70"),
                        ganho_nao_realizado=Decimal("10"),
                    ),
                    leitor.Linha(
                        CRV.nome, "investimento", "Cláudia", "Rico", "BOVA11", "BRL", Decimal("300"),
                        quantidade=Decimal("3"), classe="ETF", custo=Decimal("250"),
                        ganho_nao_realizado=Decimal("50"),
                    ),
                    leitor.Linha(
                        CRV.nome, "investimento", "Mariano", "Rico", "VALE3", "BRL", Decimal("50"),
                        quantidade=Decimal("5"), classe="ação", custo=Decimal("55"),
                        ganho_nao_realizado=Decimal("-5"),
                    ),
                ],
            ),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: snapshot)

    response = pages_client.get(
        "/holdings/",
        {
            "periodo": "1a",
            "data": "2026-09-27",
            "titular": "Mariano",
            "moeda": "BRL",
            "classe": "ação",
            "instituicao": "Rico",
            "ordenar": "nome",
            "direcao": "asc",
        },
    )

    assert response.status_code == 200
    page = response.context["wf_page"]
    assert [(row["name"], row["currency"]) for row in page["rows"]] == [("VALE3", "BRL")]
    assert page["rows"][0]["return_percent"] == "-9,09%"
    assert "periodo=1a" in response.content.decode()
    assert 'name="titular"' in response.content.decode()
    assert "Titular" in response.content.decode()
    assert 'name="moeda"' in response.content.decode()
    assert 'name="classe"' in response.content.decode()
    assert 'name="instituicao"' in response.content.decode()
    assert 'name="ordenar"' in response.content.decode()
    assert 'name="direcao"' in response.content.decode()
    assert '<input type="checkbox" name="fechada" value="1" disabled>' in response.content.decode()


def test_holdings_value_order_groups_mixed_currencies_instead_of_comparing_them(pages_client, monkeypatch):
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[]),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(CRV.nome, "investimento", "Pessoa", "A", "BRL grande", "BRL", Decimal("500")),
                    leitor.Linha(CRV.nome, "investimento", "Pessoa", "A", "BRL pequeno", "BRL", Decimal("10")),
                    leitor.Linha(CRV.nome, "investimento", "Pessoa", "B", "USD", "USD", Decimal("100")),
                ],
            ),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: snapshot)

    response = pages_client.get("/holdings/", {"ordenar": "valor", "direcao": "desc"})

    assert response.status_code == 200
    rows = response.context["wf_page"]["rows"]
    assert [(row["currency"], row["name"]) for row in rows] == [
        ("BRL", "BRL grande"),
        ("BRL", "BRL pequeno"),
        ("USD", "USD"),
    ]
    assert "dentro de cada moeda" in response.context["wf_page"]["sort_notice"]


def test_holding_link_selects_the_published_description_and_currency(pages_client, monkeypatch):
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[]),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(CRV.nome, "investimento", "Pessoa", "A", "DUAL", "BRL", Decimal("100")),
                    leitor.Linha(CRV.nome, "investimento", "Pessoa", "B", "DUAL", "USD", Decimal("25")),
                ],
            ),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: snapshot)

    listing = pages_client.get("/holdings/")
    usd = next(row for row in listing.context["wf_page"]["rows"] if row["currency"] == "USD")
    detail = pages_client.get(usd["url"])

    assert detail.status_code == 200
    assert detail.context["wf_page"]["item"]["currency"] == "USD"
    assert detail.context["wf_page"]["item"]["value"] == "US$ 25,00"


def test_spending_category_filters_are_functional_and_preserve_scope(pages_client, monkeypatch):
    analysis = {
        "disponivel": True,
        "motivo": "",
        "categorias": [
            {
                "label": "Saúde",
                "value": "R$ 120,00",
                "lines": 2,
                "percent": "60,0%",
                "percent_number": Decimal("60"),
                "delta": {"label": "+R$ 20,00", "percent": "+20,0%", "direction": "up", "amount": Decimal("20")},
            },
            {
                "label": "Lazer",
                "value": "R$ 80,00",
                "lines": 1,
                "percent": "40,0%",
                "percent_number": Decimal("40"),
                "delta": {"label": "−R$ 10,00", "percent": "−11,1%", "direction": "down", "amount": Decimal("-10")},
            },
        ],
        "meses": [],
        "lancamentos": 3,
        "anterior": None,
        "comparavel": True,
    }
    monkeypatch.setattr(wealthfolio_views.gastos, "analise", lambda *args, **kwargs: analysis)

    response = pages_client.get(
        "/spending/insights/",
        {"periodo": "1a", "data": "2026-09-27", "stage": "where", "filtro": "up"},
    )

    assert response.status_code == 200
    page = response.context["wf_page"]
    assert [row["label"] for row in page["categories"]] == ["Saúde"]
    assert page["category_counts"] == {"all": 2, "up": 1, "down": 1}
    assert page["category_filter"] == "up"
    body = response.content.decode()
    assert "Subiram" in body and 'aria-current="page"' in body
    assert "disabled>Todas" not in body
    assert "periodo=1a" in next(item["url"] for item in page["category_filters"] if item["key"] == "down")
