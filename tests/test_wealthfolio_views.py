from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte

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
    monkeypatch.setattr(wealthfolio_views.legacy_views, "consolidar_v2", lambda **_kwargs: _snapshot())
    return client


def test_dashboard_and_insights_routes_render(logged_client):
    dashboard = logged_client.get("/dashboard/", {"tab": "investments"})
    insights = logged_client.get("/insights/", {"tab": "summary"})

    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.content.decode()
    assert insights.status_code == 200
    insights_body = insights.content.decode()
    assert "Exposição da carteira" in insights_body
    assert "Portfolio Insights" not in insights_body


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
