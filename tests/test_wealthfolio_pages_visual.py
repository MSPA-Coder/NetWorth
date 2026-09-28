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

    def fetch_all(source, *, resource, inicio, fim, extra=None):
        items = (point,) if resource == "holding-history" else ()
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
