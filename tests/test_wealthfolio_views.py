from datetime import date
from decimal import Decimal
from pathlib import Path
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


def test_insights_account_picker_marks_current_group_and_preserves_valid_get_filters(logged_client):
    filter_id = wealthfolio_views.insights_builder.id_item("classe", "Não classificado")
    params = {
        "tab": "performance",
        "periodo": "5a",
        "data": "2026-09-20",
        "dimensao": "classe",
        "filtro_dimensao": "classe",
        "filtro": filter_id,
        "busca": "ETF",
        "ordenar": "nome",
        "direcao": "asc",
    }
    initial = logged_client.get("/insights/", params)
    group_id = initial.context["wf_insights"]["account_options"][0]["id"]
    params["grupo"] = group_id
    response = logged_client.get("/insights/", params)

    assert response.status_code == 200
    options = response.context["wf_insights"]["account_options"]
    assert [option["id"] for option in options if option["selected"]] == [group_id]
    form_params = response.context["wf_insights"]["form_params"]
    assert form_params == {
        "tab": "performance",
        "periodo": "5a",
        "data": "2026-09-20",
        "dimensao": "classe",
        "busca": "ETF",
        "ordenar": "nome",
        "direcao": "asc",
        "filtro_dimensao": "classe",
        "filtro": filter_id,
    }
    assert f'value="{group_id}" selected' in response.content.decode()
    assert 'name="filtro_dimensao"' in response.content.decode()


def test_insights_account_form_drops_invalid_filter_pair(logged_client):
    response = logged_client.get(
        "/insights/",
        {
            "tab": "income",
            "periodo": "3a",
            "data": "2026-09-20",
            "dimensao": "moeda",
            "filtro_dimensao": "classe",
            "filtro": "unknown-id",
            "ordenar": "unknown-sort",
            "direcao": "sideways",
            "stage": "unsupported-here",
        },
    )

    assert response.status_code == 200
    form_params = response.context["wf_insights"]["form_params"]
    assert form_params["tab"] == "income"
    assert form_params["periodo"] == "1a"
    assert form_params["data"] == "2026-09-20"
    assert form_params["dimensao"] == "moeda"
    assert form_params["ordenar"] == "valor"
    assert form_params["direcao"] == "desc"
    assert "filtro" not in form_params and "filtro_dimensao" not in form_params
    assert "stage" not in form_params


def test_insights_unsupported_controls_are_disabled(logged_client):
    insights_response = logged_client.get("/insights/", {"tab": "performance"})
    accounts_response = logged_client.get("/accounts/")

    assert insights_response.status_code == 200
    insights_body = insights_response.content.decode()
    assert '<span class="is-disabled" aria-disabled="true">Conta</span>' in insights_body
    assert '<span class="is-disabled" aria-disabled="true">Benchmark</span>' in insights_body
    assert accounts_response.status_code == 200
    assert 'aria-label="Filtrar contas" disabled aria-disabled="true"' in accounts_response.content.decode()


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


def test_empty_performance_resource_renders_one_unavailable_message(logged_client):
    response = logged_client.get("/insights/", {"tab": "performance"})

    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("Desempenho indisponível: nenhuma série TWR foi publicada pelas fontes.") == 1


def test_performance_item_without_points_renders_its_own_unavailable_state(logged_client, monkeypatch):
    record = PerformanceRecord("empty-series", "BRL", "TWR", "CRV", points=())
    monkeypatch.setattr(
        wealthfolio_views,
        "_analytics_context",
        lambda _context: {"performance": SimpleNamespace(items=(record,), status="ok", warnings=())},
    )

    response = logged_client.get("/insights/", {"tab": "performance"})

    assert response.status_code == 200
    body = response.content.decode()
    assert "Série TWR indisponível: a origem não publicou pontos." in body
    assert "Desempenho indisponível: nenhuma série TWR foi publicada pelas fontes." not in body


@pytest.mark.sentinela_front
def test_single_point_performance_series_keeps_accessible_insufficient_state():
    """A one-point series must not disappear and imply that no data was published."""
    script = (Path(__file__).parents[1] / "static" / "js" / "wealthfolio-insights-v2.js").read_text(
        encoding="utf-8"
    )

    assert "series.length < 2" in script
    assert "Série insuficiente para desenhar o gráfico" in script
    assert 'message.setAttribute("role", "status")' in script
    assert "frame.appendChild(message)" in script


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
    assert "account_id=conta-" in child["url"]
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
    assert parse_qs(parsed_url.query)["account_id"][0].startswith("conta-")

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


def test_dashboard_account_identity_keeps_homonyms_separate_by_owner_and_source(logged_client, monkeypatch):
    other_source = Fonte(
        apelido="CB2",
        nome="Banco Espelho",
        papel="caixa",
        url="http://cb2.teste",
        token="t",
    )
    snapshot = leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(CB.nome, "caixa", "Ana", "Mercado Pago", "Conta conjunta", "BRL", Decimal("100")),
                ],
            ),
            leitor.Leitura(
                fonte=other_source,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(other_source.nome, "caixa", "Bia", "Mercado Pago", "Conta conjunta", "BRL", Decimal("200")),
                ],
            ),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: snapshot)

    dashboard = logged_client.get("/dashboard/", {"tab": "investments"})
    accounts = dashboard.context["wf_dashboard"]["investments"]["accounts"]
    child_urls = {
        owner: next(child["url"] for child in group["children"])
        for owner in ("Ana", "Bia")
        for group in accounts
        if group["name"] == owner
    }
    opaque_ids = {
        owner: parse_qs(urlsplit(url).query)["account_id"][0]
        for owner, url in child_urls.items()
    }

    assert opaque_ids["Ana"] != opaque_ids["Bia"]
    for owner, url in child_urls.items():
        detail = logged_client.get(url)
        assert detail.status_code == 200
        account = detail.context["wf_page"]["account"]
        assert account["owner"] == owner
        assert [row["source"] for row in detail.context["wf_page"]["rows"]] == [
            CB.nome if owner == "Ana" else other_source.nome
        ]
        assert len(detail.context["wf_page"]["rows"]) == 1

    legacy = logged_client.get("/accounts/Mercado%20Pago/", {"account": "Conta conjunta"})
    assert legacy.context["wf_page"]["state"] == "empty"


def test_investment_account_detail_uses_published_owner_instead_of_synthetic_group(logged_client):
    dashboard = logged_client.get("/dashboard/", {"tab": "investments"})
    investment_group = next(
        item
        for item in dashboard.context["wf_dashboard"]["investments"]["accounts"]
        if item["name"] == "Renda variável"
    )

    detail = logged_client.get(investment_group["children"][0]["url"])

    assert detail.status_code == 200
    assert detail.context["wf_page"]["account"]["owner"] == "Pessoa"
    assert detail.context["wf_page"]["account"]["owner"] != investment_group["name"]


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
