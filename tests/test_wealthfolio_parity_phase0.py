"""Abas, estados vazios e controles de escrita das telas do shell.

Os rótulos de cada aba não são conferidos (docs/TESTES.md, T5); a aba
escolhida é, pelo contexto que o servidor monta."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def snapshot_completo() -> leitor.Consolidado:
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[leitor.Linha(fonte=CB.nome, papel="caixa", titular="Pessoa", instituicao="Banco", descricao="Conta principal", moeda="BRL", valor=Decimal("1000.00"))],
            ),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[leitor.Linha(fonte=CRV.nome, papel="investimento", titular="Pessoa", instituicao="Corretora", descricao="ETF Brasil", moeda="BRL", valor=Decimal("500.00"), quantidade=Decimal("2"), classe="Ações")],
            ),
        ]
    )


@pytest.fixture
def logged_client(monkeypatch):
    user = get_user_model().objects.create_user("parity-owner", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(user)
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: snapshot_completo())
    return client


def test_dashboard_matrix_has_three_tabs_and_consistent_context(logged_client):
    for tab in ("investments", "net-worth", "spending"):
        response = logged_client.get("/dashboard/", {"tab": tab, "periodo": "3m"})
        assert response.status_code == 200
        assert response.context["dashboard_tab"] == tab
    body = logged_client.get("/dashboard/", {"tab": "net-worth", "periodo": "3m"}).content.decode()
    assert "periodo=3m" in body or "periodo=3m&amp;" in body


@pytest.mark.parametrize("tab", ["summary", "performance", "income"])
def test_insights_matrix_has_three_tabs(logged_client, tab):
    response = logged_client.get("/insights/", {"tab": tab, "periodo": "1a"})
    assert response.status_code == 200
    assert response.context["insights_tab"] == tab


@pytest.mark.parametrize("path", ["/dashboard/", "/insights/", "/holdings/", "/accounts/", "/activities/", "/goals/", "/spending/insights/", "/spending/budget/"])
def test_wealthfolio_surface_requires_authentication(path):
    response = Client().get(path)
    assert response.status_code == 302
    assert response["Location"].startswith("/login?next=")


def test_query_params_are_exposed_without_mutating_source_scope(logged_client):
    response = logged_client.get("/insights/", {"tab": "performance", "periodo": "3m", "dimensao": "classe", "busca": "ETF"})
    body = response.content.decode()
    assert response.status_code == 200
    assert response.context["periodo"] == "3m"
    assert response.context["insights_tab"] == "performance"
    assert body.lower().count('method="post"') == 1
    assert 'action="/logout"' in body.lower()
    assert 'method="get"' in body.lower()


def test_partial_source_keeps_warning_and_available_rows(logged_client, monkeypatch):
    snapshot = snapshot_completo()
    snapshot.leituras[1].estado = leitor.FALHOU
    snapshot.leituras[1].linhas = []
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: snapshot)
    dashboard = logged_client.get("/dashboard/", {"tab": "net-worth"})
    insights = logged_client.get("/insights/", {"tab": "summary"})
    assert dashboard.status_code == 200
    assert insights.status_code == 200
    assert "parcial" in dashboard.content.decode().lower()
    assert "parcial" in insights.content.decode().lower()
    assert "Dados parciais" in dashboard.content.decode()
    assert "1/2 fontes" in dashboard.content.decode()


def test_empty_snapshot_has_explicit_empty_states(logged_client, monkeypatch):
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_: leitor.Consolidado())
    dashboard = logged_client.get("/dashboard/", {"tab": "spending"})
    insights = logged_client.get("/insights/", {"tab": "summary"})
    assert dashboard.status_code == 200
    assert insights.status_code == 200
    dashboard_body = dashboard.content.decode().lower()
    insights_body = insights.content.decode().lower()
    assert "ainda não há dados para esta visão" in dashboard_body
    assert "insights indisponíveis" in insights_body or "nenhuma exposição publicada" in insights_body


def test_write_controls_are_explicitly_local_and_not_misleading(logged_client):
    dashboard = logged_client.get("/dashboard/", {"tab": "net-worth"}).content.decode()
    insights = logged_client.get("/insights/", {"tab": "summary"}).content.decode()
    assert 'action="target-allocation"' not in dashboard
    assert "Alocação-alvo indisponível" in insights
    assert 'method="post" action="/logout"' in insights


@pytest.mark.parametrize("path", ["/goals/", "/spending/budget/"])
def test_unavailable_goal_and_budget_states_are_explicit(logged_client, path):
    response = logged_client.get(path)
    body = response.content.decode()

    assert response.status_code == 200
    assert "Ainda não disponível nesta fase" in body


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/",
        "/insights/",
        "/holdings/",
        "/holdings/ETF/",
        "/accounts/",
        "/accounts/Banco/",
        "/activities/",
        "/goals/",
        "/goals/new/",
        "/spending/insights/",
        "/spending/budget/",
        "/assistant/",
        "/settings/",
    ],
)
def test_read_only_routes_reject_post(logged_client, path):
    response = logged_client.post(path)

    assert response.status_code == 405
    assert response["Allow"] == "GET"


@pytest.mark.parametrize(
    "path",
    [
        "/holdings/",
        "/holdings/ETF Brasil/",
        "/accounts/",
        "/accounts/Banco/",
        "/activities/",
        "/goals/",
        "/spending/insights/",
        "/spending/budget/",
        "/assistant/",
        "/settings/",
    ],
)
def test_authenticated_derived_routes_render_without_server_error(logged_client, path):
    response = logged_client.get(path)

    assert response.status_code == 200
