from dataclasses import replace
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
from consolidado.wealthfolio_compat.analytics import (
    AnalyticsSourceResult,
    IncomeRecord,
    PerformanceRecord,
)
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
    assert 'data-insights-widget="accounts"' in insights_body
    assert 'data-insights-widget="concentration"' in insights_body
    assert 'data-widget-edit' in insights_body
    assert "Portfolio Insights" not in insights_body

    income = logged_client.get("/insights/", {"tab": "income"})
    income_body = income.content.decode()
    assert income.status_code == 200
    assert 'aria-label="Filtros de rendimentos"' in income_body
    assert 'data-income-parity' in income_body
    assert 'data-income-coverage' in income_body

    performance = logged_client.get("/insights/", {"tab": "performance"})
    performance_body = performance.content.decode()
    assert performance.status_code == 200
    assert 'data-performance-details' in performance_body
    assert 'data-performance-metric="return_twr"' in performance_body


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


def test_insights_breakdown_link_is_canonical_and_keeps_filtered_data(logged_client):
    response = logged_client.get("/insights/", {"tab": "summary", "dimensao": "classe"})

    assert response.status_code == 200
    detail = response.context["wf_insights"]["summary"]["details"][0]
    assert detail["url"].startswith("/insights/?")

    filtered = logged_client.get(detail["url"])

    assert filtered.status_code == 200
    assert filtered.context["wf_insights"]["summary"]["available"] is True
    assert filtered.context["wf_insights"]["summary"]["details"]
    assert "Nenhuma exposição publicada." not in filtered.content.decode()


def test_insights_accepts_browser_escaped_ampersands_in_filter_url(logged_client):
    escaped = (
        r"/insights/?visao=insights\&periodo=1a\&data=2026-09-27"
        r"\&dimensao=classe\&filtro_dimensao=classe"
        r"\&filtro=insight-f33baa9cdcee07bdbc2e\&ordenar=valor\&direcao=desc"
    )

    response = logged_client.get(escaped)

    assert response.status_code == 200
    assert response.context["insights"]["filtro_nome"] == "Caixa"
    assert response.context["wf_insights"]["summary"]["details"]


def test_insights_filtered_state_exposes_clear_filter_and_active_block(logged_client):
    filter_id = wealthfolio_views.insights_builder.id_item("classe", "Caixa")
    response = logged_client.get(
        "/insights/",
        {
            "visao": "insights",
            "periodo": "1a",
            "data": "2026-09-27",
            "dimensao": "classe",
            "filtro_dimensao": "classe",
            "filtro": filter_id,
            "ordenar": "valor",
            "direcao": "desc",
        },
    )

    assert response.status_code == 200
    summary = response.context["wf_insights"]["summary"]
    assert summary["filter_name"] == "Caixa"
    assert summary["clear_filter_url"].startswith("/insights/?")
    assert any(item["selected"] for item in summary["items"])
    body = response.content.decode()
    assert 'class="wf-insights-v2__filterbar"' in body
    assert 'class="is-selected"' in body


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


def test_insights_account_picker_includes_child_accounts_and_scopes_rows(logged_client):
    initial = logged_client.get("/insights/", {"tab": "summary"})
    options = initial.context["wf_insights"]["account_options"]
    child = next(option for option in options if option["kind"] == "account")

    response = logged_client.get(
        "/insights/",
        {"tab": "summary", "grupo": child["id"], "periodo": "1a", "data": "2026-09-27"},
    )

    assert response.status_code == 200
    assert response.context["conta_selecionada"] == child["id"]
    selected = [option for option in response.context["wf_insights"]["account_options"] if option["selected"]]
    assert [option["id"] for option in selected] == [child["id"]]
    assert response.context["wf_insights"]["summary"]["details"]
    assert "conta" not in response.context["wf_insights"]["form_params"]
    assert child["id"] in response.content.decode()


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


def test_insights_summary_widgets_use_published_lines_and_keep_currency_scopes(logged_client, monkeypatch):
    snapshot = _snapshot()
    investment = replace(
        snapshot.leituras[1].linhas[0],
        classe="acao",
        ganho_nao_realizado=Decimal("6.25"),
        retorno=Decimal("0.125"),
    )
    usd_investment = replace(
        investment,
        descricao="ETF USD",
        moeda="USD",
        valor=Decimal("75.00"),
        ganho_nao_realizado=None,
        retorno=None,
        id_de_origem="usd-1",
    )
    investment_reading = replace(snapshot.leituras[1], linhas=[investment, usd_investment])
    snapshot = replace(snapshot, leituras=[snapshot.leituras[0], investment_reading])
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: snapshot)

    response = logged_client.get("/insights/", {"tab": "summary", "data": "2026-09-27"})

    assert response.status_code == 200
    widgets = response.context["wf_insights"]["summary"]["widgets"]
    assert widgets["accounts"]["available"] is True
    assert len(widgets["accounts"]["items"]) == 3
    assert any("R$ 100,00" in item["value"] for item in widgets["accounts"]["items"])
    assert {item["name"] for item in widgets["composition"]["items"]} == {"Caixa", "Investimentos"}
    investments_item = next(item for item in widgets["composition"]["items"] if item["name"] == "Investimentos")
    assert investments_item["percent_number"] is None
    assert investments_item["percent_by_currency"]["BRL"] == Decimal("33.33333333333333333333333333")
    assert investments_item["percent_by_currency"]["USD"] == Decimal("100")
    assert widgets["classes"]["available"] is True
    assert widgets["regions"]["available"] is False
    assert widgets["sectors"]["available"] is False
    assert widgets["dimensions"]["available"] is True
    assert widgets["dimensions"]["items"][0]["name"].startswith("Classe:")
    assert widgets["concentration"]["available"] is True
    assert widgets["movers"]["available"] is True
    mover = widgets["movers"]["items"][0]
    assert mover["value"] == "R$ 6,25"
    assert mover["percent_number"] == Decimal("12.500")
    assert "não representa o período selecionado" in mover["detail"]


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


def test_income_insights_expose_monthly_average_breakdowns_rankings_and_currency_scopes(
    logged_client, monkeypatch
):
    records = (
        IncomeRecord(
            "income-1", date(2026, 9, 3), "Dividendo ITUB4", Money(Decimal("120"), "BRL"),
            "CRV", kind="dividend", instrument="ITUB4", institution="Corretora A", category="Dividendos",
        ),
        IncomeRecord(
            "income-2", date(2026, 9, 10), "JCP ITUB4", Money(Decimal("30"), "BRL"),
            "CRV", kind="jcp", instrument="ITUB4", institution="Corretora A", category="Juros sobre capital",
        ),
        IncomeRecord(
            "income-3", date(2026, 8, 7), "Dividend ETF", Money(Decimal("20"), "USD"),
            "CRV", kind="dividend", instrument="ETF-US", institution="Corretora B", category="Dividendos",
        ),
    )
    composition = SimpleNamespace(
        items=records,
        status="partial",
        warnings=("Fonte secundária indisponível",),
        results=(
            AnalyticsSourceResult("CRV", "income", "ok", items=records, total=3),
            AnalyticsSourceResult("CRV secundária", "income", "unsupported", error="Não publica renda"),
        ),
    )
    monkeypatch.setattr(
        wealthfolio_views,
        "_analytics_context",
        lambda _context: {"income": composition},
    )

    response = logged_client.get("/insights/", {"tab": "income", "periodo": "1a", "data": "2026-09-27"})

    assert response.status_code == 200
    income = response.context["wf_insights"]["income"]
    assert income["available"] is True
    average = next(metric for metric in income["metrics"] if metric["key"] == "income_monthly_average")
    assert average["months"] == 13
    assert average["values"] == [
        {"currency": "BRL", "amount": Decimal("150") / 13, "value": wealthfolio_views.dinheiro(Decimal("150") / 13, "BRL")},
        {"currency": "USD", "amount": Decimal("20") / 13, "value": wealthfolio_views.dinheiro(Decimal("20") / 13, "USD")},
    ]
    assert income["coverage"]["status"] == "partial"
    assert income["coverage"]["complete"] is False
    assert income["coverage"]["expected_sources"] == 2
    assert income["coverage"]["responded_sources"] == 2
    assert income["coverage"]["publishing_sources"] == 1
    assert {item["name"] for item in income["by_type"]["items"]} == {"dividend", "jcp"}
    assert {item["name"] for item in income["by_category"]["items"]} == {
        "Dividendos", "Juros sobre capital"
    }
    assert {item["name"] for item in income["by_instrument"]["items"]} == {"ITUB4", "ETF-US"}
    source_rankings = income["rankings"]["source"]["currencies"]
    assert {item["currency"] for item in source_rankings} == {"BRL", "USD"}
    brl_ranking = next(item["items"] for item in source_rankings if item["currency"] == "BRL")
    assert brl_ranking[0]["value"] == "R$ 150,00"
    institution_rankings = income["rankings"]["institution"]["currencies"]
    assert len(institution_rankings) == 2


def test_income_insights_without_records_marks_all_derived_values_unavailable(logged_client, monkeypatch):
    composition = SimpleNamespace(
        items=(),
        status="error",
        warnings=("A fonte não respondeu",),
        results=(AnalyticsSourceResult("CRV", "income", "error", error="A fonte não respondeu"),),
    )
    monkeypatch.setattr(
        wealthfolio_views,
        "_analytics_context",
        lambda _context: {"income": composition},
    )

    response = logged_client.get("/insights/", {"tab": "income"})

    assert response.status_code == 200
    income = response.context["wf_insights"]["income"]
    assert income["available"] is False
    assert income["history_available"] is False
    assert income["history"] == []
    assert income["by_type"]["available"] is False
    assert income["by_category"]["items"] == []
    assert income["rankings"]["institution"]["available"] is False
    assert income["coverage"]["status"] == "error"
    assert income["coverage"]["responded_sources"] == 0
    assert income["coverage"]["publishing_sources"] == 0


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
    statistics = response.context["wf_insights"]["performance"]["items"][0]["statistics"]
    assert all(item["state"] == "unavailable" for item in statistics)


def test_performance_insights_derive_risk_and_return_metrics_from_published_twr(logged_client, monkeypatch):
    record = PerformanceRecord(
        "series-brl",
        "BRL",
        "TWR",
        "CRV",
        start=date(2026, 1, 1),
        end=date(2026, 4, 1),
        points=(
            (date(2026, 4, 1), Decimal("0.20")),
            (date(2026, 1, 1), Decimal("0")),
            (date(2026, 2, 1), Decimal("0.10")),
            (date(2026, 3, 1), Decimal("0")),
        ),
        link="/performance",
    )
    composition = SimpleNamespace(
        items=(record,),
        status="partial",
        warnings=("Outra fonte sem série",),
        results=(
            AnalyticsSourceResult("CRV", "performance", "ok", items=(record,), total=1),
            AnalyticsSourceResult("CRV secundária", "performance", "unsupported", error="Não publica TWR"),
        ),
    )
    monkeypatch.setattr(
        wealthfolio_views,
        "_analytics_context",
        lambda _context: {"performance": composition},
    )

    response = logged_client.get("/insights/", {"tab": "performance", "periodo": "1a", "data": "2026-04-01"})

    assert response.status_code == 200
    performance = response.context["wf_insights"]["performance"]
    assert performance["available"] is True
    assert performance["coverage"]["status"] == "partial"
    assert performance["coverage"]["complete"] is False
    assert performance["coverage"]["responded_sources"] == 2
    assert performance["coverage"]["publishing_sources"] == 1
    item = performance["items"][0]
    assert item["return"] == "20,00%"
    assert item["source"] == "CRV"
    assert item["link"] == "/performance"
    metrics = {metric["key"]: metric for metric in item["statistics"]}
    assert metrics["return_twr"]["value"] == "20,00%"
    assert metrics["annualized_return"]["state"] == "ok"
    assert metrics["annualized_volatility"]["state"] == "ok"
    assert metrics["max_drawdown"]["number"] == Decimal("1") / Decimal("1.1") - Decimal("1")
    assert metrics["best_month"]["value"] == "20,00%"
    assert metrics["best_month"]["detail"] == "04/2026"
    assert metrics["worst_month"]["detail"] == "03/2026"


def test_legacy_performance_series_accepts_actual_data_valor_contract():
    points = wealthfolio_views._legacy_performance_points(
        '[{"data":"2026-01-31","valor":"0"},{"data":"2026-02-28","valor":"0.04"}]'
    )

    assert points == [
        (date(2026, 1, 31), Decimal("0")),
        (date(2026, 2, 28), Decimal("0.04")),
    ]


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
