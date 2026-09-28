from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.analytics import IncomeRecord, PerformanceRecord
from consolidado.wealthfolio_compat.models import Money

pytestmark = pytest.mark.django_db


CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def _snapshot():
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(
                        fonte=CB.nome,
                        papel="caixa",
                        titular="Pessoa",
                        instituicao="Banco",
                        descricao="Conta",
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
                        descricao="ETF",
                        moeda="BRL",
                        valor=Decimal("50.00"),
                        quantidade=Decimal("2"),
                    )
                ],
            ),
        ]
    )


@pytest.fixture
def logged_client(monkeypatch):
    user = get_user_model().objects.create_user("wealthfolio", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(user)
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: _snapshot())
    return client


def test_dashboard_and_insights_routes_render(logged_client):
    dashboard = logged_client.get("/dashboard/", {"tab": "investments"})
    insights = logged_client.get("/insights/", {"tab": "summary"})

    assert dashboard.status_code == 200
    assert dashboard.context["periodo"] == "3m"
    # O rótulo vem de PERIODOS_DASHBOARD, que chama este período de "3 meses".
    assert "3 meses" in dashboard.content.decode().lower()
    assert "Dashboard" in dashboard.content.decode()
    assert insights.status_code == 200
    insights_body = insights.content.decode()
    assert "Exposição da carteira" in insights_body
    assert "Portfolio Insights" not in insights_body


def test_insights_performance_periods_match_supported_periods_and_selection(logged_client):
    response = logged_client.get(
        "/insights/",
        {
            "tab": "performance",
            "periodo": "5a",
            "data": "2026-09-20",
            "dimensao": "moeda",
            "busca": "dólar",
            "ordenar": "nome",
            "direcao": "asc",
            "categoria": "unsupported-here",
        },
    )

    assert response.status_code == 200
    periods = response.context["wf_insights"]["periods"]
    assert "3a" not in {item["key"] for item in periods}
    assert [item["key"] for item in periods if item["active"]] == ["5a"]
    assert all("periodo=3a" not in item["url"] for item in periods)
    period_query = parse_qs(urlsplit(next(item["url"] for item in periods if item["active"])).query)
    assert period_query["periodo"] == ["5a"]
    assert period_query["data"] == ["2026-09-20"]
    assert period_query["dimensao"] == ["moeda"]
    assert period_query["busca"] == ["dólar"]
    assert period_query["ordenar"] == ["nome"]
    assert period_query["direcao"] == ["asc"]
    assert "categoria" not in period_query


def test_insights_tab_links_drop_unknown_filter_id(logged_client):
    response = logged_client.get(
        "/insights/",
        {"tab": "summary", "dimensao": "classe", "filtro_dimensao": "classe", "filtro": "unknown-id"},
    )

    assert response.status_code == 200
    tab_url = response.context["wf_insights"]["tab_urls"]["performance"]
    tab_query = parse_qs(urlsplit(tab_url).query)
    assert "filtro" not in tab_query
    assert "filtro_dimensao" not in tab_query


@pytest.mark.parametrize("tab", ["performance", "income"])
def test_published_insight_resource_is_not_hidden_by_empty_summary(logged_client, monkeypatch, tab):
    monkeypatch.setattr(wealthfolio_views.insights_builder, "montar", lambda *_args, **_kwargs: {})
    record = (
        PerformanceRecord("series", "BRL", "TWR", "CRV", points=((date(2026, 9, 1), Decimal("0.02")),))
        if tab == "performance"
        else IncomeRecord("income", date(2026, 9, 1), "Dividendo", Money(Decimal("12"), "BRL"), "CRV")
    )
    resource = "performance" if tab == "performance" else "income"
    monkeypatch.setattr(
        wealthfolio_views,
        "_analytics_context",
        lambda _context: {resource: SimpleNamespace(items=(record,), status="ok", warnings=())},
    )

    response = logged_client.get("/insights/", {"tab": tab})

    assert response.status_code == 200
    assert response.context["wf_insights"]["summary"]["available"] is False
    assert response.context["wf_insights"][resource]["available"] is True
    assert "Insights indisponíveis" not in response.content.decode()


def test_account_detail_resolves_groups_and_published_account_filter(logged_client):
    client = logged_client

    group = client.get("/accounts/Pessoa/")
    filtered = client.get("/accounts/Banco/", {"account": "Conta"})

    assert group.status_code == 200
    assert group.context["wf_page"]["account"]["name"] == "Pessoa"
    assert "Conta" in group.content.decode()
    assert filtered.status_code == 200
    assert filtered.context["wf_page"]["account"]["name"] == "Banco · Conta"
    assert len(filtered.context["wf_page"]["rows"]) == 1


def test_dashboard_account_children_keep_account_drilldown(logged_client):
    response = logged_client.get("/dashboard/", {"tab": "investments"})

    assert response.status_code == 200
    groups = response.context["wf_dashboard"]["investments"]["accounts"]
    child = groups[0]["children"][0]
    assert child["url"].startswith("/accounts/Banco/")
    assert "account=Conta" in child["url"]
    detail = logged_client.get(child["url"])
    assert detail.status_code == 200
    assert detail.context["wf_page"]["account"]["name"] == "Banco · Conta"
    assert len(detail.context["wf_page"]["rows"]) == 1


def test_dashboard_group_account_drilldown_targets_exact_published_lines(logged_client, monkeypatch):
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(CB.nome, "caixa", "Esposita", "Mercado Pago", "Conta Á 01", "BRL", Decimal("100")),
                    leitor.Linha(CB.nome, "caixa", "Esposita", "Mercado Pago", "Conta Á 010", "BRL", Decimal("200")),
                ],
            )
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: snapshot)

    dashboard = logged_client.get("/dashboard/", {"tab": "investments"})
    group = next(item for item in dashboard.context["wf_dashboard"]["investments"]["accounts"] if item["name"] == "Esposita")
    child = next(item for item in group["children"] if item["name"] == "Mercado Pago · Conta Á 01")
    parsed_url = urlsplit(child["url"])

    assert parsed_url.path == "/accounts/Mercado%20Pago/"
    assert parse_qs(parsed_url.query)["account"] == ["Conta Á 01"]

    group_id = parse_qs(urlsplit(group["url"]).query)["grupo"][0]
    expanded = logged_client.get("/dashboard/", {"tab": "investments", "grupo": group_id})
    expanded_group = next(item for item in expanded.context["wf_dashboard"]["investments"]["accounts"] if item["name"] == "Esposita")
    assert expanded_group["expanded"] is True
    assert next(item for item in expanded_group["children"] if item["name"] == "Mercado Pago · Conta Á 01")["url"] == child["url"]

    detail = logged_client.get(child["url"])
    assert detail.status_code == 200
    assert detail.context["wf_page"]["account"]["name"] == "Mercado Pago · Conta Á 01"
    assert [row["name"] for row in detail.context["wf_page"]["rows"]] == ["Conta Á 01"]

    for filter_key in ("account", "conta"):
        invalid = logged_client.get("/accounts/Mercado%20Pago/", {filter_key: "Conta 02"})
        assert invalid.context["wf_page"]["state"] == "empty"
        assert invalid.context["wf_page"]["rows"] == ()


def test_dashboard_api_keeps_currency_and_coverage(logged_client):
    response = logged_client.get("/api/wealthfolio/dashboard/")

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"]["complete"] is True
    assert body["base_currency"] == "BRL"
    assert body["total_base"] == "150.00"
    assert "metrics" in body
    assert "chart" in body
    assert body["accounts"]


def test_goal_route_is_read_only_and_has_no_local_model(logged_client):
    response = logged_client.post(
        "/goals/new/",
        {"name": "Reserva", "kind": "savings", "target": "1000,00", "target_date": "2027-01-01"},
    )

    assert response.status_code == 405
    assert response["Allow"] == "GET"
    assert not hasattr(get_user_model().objects.get(username="wealthfolio"), "wealthfolio_goals")


def test_logout_post_is_allowed(logged_client):
    response = logged_client.post("/logout")

    assert response.status_code in {302, 303}


def test_no_local_goal_or_budget_models_are_registered():
    local_model_names = {
        model._meta.model_name
        for model in apps.get_models()
        if model.__module__.startswith("consolidado.")
    }

    assert not local_model_names.intersection({"goal", "goals", "meta", "metas", "budget", "orcamento"})
