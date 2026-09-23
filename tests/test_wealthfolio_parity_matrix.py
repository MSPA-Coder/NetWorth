"""Matriz determinística dos contratos da superfície Wealthfolio.

Estes testes não carregam uma base de dados de exemplo nem acessam as fontes.
Cada cenário representa apenas o envelope já normalizado pelo leitor: completo,
parcial ou vazio. O objetivo é verificar estados, navegação e read-only sem
confundir ``HTTP 200`` com paridade visual.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte

pytestmark = pytest.mark.django_db


CB = Fonte(
    apelido="CB",
    nome="Controle Bancário",
    papel="caixa",
    url="http://cb.teste",
    token="token-cb",
)
CRV = Fonte(
    apelido="CRV",
    nome="Controle de Renda Variável",
    papel="investimento",
    url="http://crv.teste",
    token="token-crv",
)


def _complete_snapshot() -> leitor.Consolidado:
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                data_de_referencia=date(2026, 9, 20),
                linhas=[
                    leitor.Linha(
                        fonte=CB.nome,
                        papel="caixa",
                        titular="Pessoa",
                        instituicao="Banco",
                        descricao="Conta principal",
                        moeda="BRL",
                        valor=Decimal("1000.00"),
                        id_de_origem="cb-conta-1",
                        link="/contas/cb-conta-1",
                    )
                ],
                fluxos=[
                    {
                        "data": date(2026, 9, 18),
                        "moeda": "BRL",
                        "natureza": "gerencial",
                        "entradas": Decimal("100.00"),
                        "saidas": Decimal("40.00"),
                        "liquido": Decimal("60.00"),
                        "linhas": 2,
                    }
                ],
            ),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                data_de_referencia=date(2026, 9, 20),
                linhas=[
                    leitor.Linha(
                        fonte=CRV.nome,
                        papel="investimento",
                        titular="Pessoa",
                        instituicao="Corretora",
                        descricao="ETF Brasil",
                        moeda="BRL",
                        valor=Decimal("500.00"),
                        id_de_origem="crv-pos-1",
                        link="/posicoes/crv-pos-1",
                        classe="ETF",
                        mercado="B3",
                        quantidade=Decimal("2"),
                    )
                ],
            ),
        ]
    )


@pytest.fixture
def logged_client(monkeypatch):
    get_user_model().objects.create_user(
        username="parity-matrix",
        password="senha-longa-o-suficiente",
    )
    client = Client()
    client.force_login(get_user_model().objects.get(username="parity-matrix"))
    snapshot = _complete_snapshot()
    monkeypatch.setattr(
        wealthfolio_views.leitor,
        "consolidar_v2",
        lambda **_kwargs: snapshot,
    )
    return client, snapshot


def test_dashboard_matrix_has_one_active_tab_and_preserves_period(logged_client):
    client, _snapshot = logged_client

    for tab in ("investments", "net-worth", "spending"):
        response = client.get(
            "/dashboard/",
            {"tab": tab, "periodo": "3m", "data": "2026-09-20"},
        )

        assert response.status_code == 200
        view = response.context["wf_dashboard"]
        assert view["tab"] == tab
        assert sum(item["active"] for item in view["tabs"]) == 1
        assert view["meta"] == {
            "complete": True,
            "sources_responded": 2,
            "sources_expected": 2,
        }
        assert all("periodo=3m" in item["url"] for item in view["tabs"])
        assert all("data=2026-09-20" in item["url"] for item in view["tabs"])


@pytest.mark.parametrize("tab", ("summary", "performance", "income"))
def test_insights_tabs_expose_the_same_read_only_filter_contract(logged_client, tab):
    client, _snapshot = logged_client
    response = client.get(
        "/insights/",
        {
            "tab": tab,
            "periodo": "3m",
            "data": "2026-09-20",
            "dimensao": "classe",
            "busca": "ETF",
            "ordenar": "nome",
            "direcao": "asc",
        },
    )

    assert response.status_code == 200
    assert response.context["insights_tab"] == tab
    assert response.context["wf_shell"]["active_route"] == "insights"
    insight = response.context["insights"]
    assert insight["dimensao"] == "classe"
    assert insight["busca"] == "ETF"
    assert insight["ordenar"] == "nome"
    assert insight["direcao"] == "asc"
    assert insight["itens"]
    assert all("busca=ETF" in item["url"] for item in insight["itens"])
    assert all("ordenar=nome" in item["url"] for item in insight["itens"])


@pytest.mark.parametrize(
    ("path", "kind"),
    (
        ("/holdings/", "holdings"),
        ("/holdings/ETF%20Brasil/", "holding-detail"),
        ("/accounts/", "accounts"),
        ("/accounts/Banco/", "account-detail"),
        ("/activities/", "activities"),
        ("/spending/insights/", "spending-insights"),
        ("/spending/budget/", "budget"),
        ("/goals/", "goals"),
        ("/goals/new/", "goal-new"),
        ("/assistant/", "assistant"),
        ("/settings/", "settings"),
    ),
)
def test_derived_route_matrix_has_explicit_kind_and_rejects_writes(logged_client, path, kind):
    client, _snapshot = logged_client

    response = client.get(path, {"periodo": "3m", "data": "2026-09-20"})
    assert response.status_code == 200
    assert response.context["wf_page"]["kind"] == kind
    assert response.context["wf_page"]["meta"]["complete"] is True

    write = client.post(path)
    assert write.status_code == 405
    assert write["Allow"] == "GET"


def test_partial_snapshot_is_never_presented_as_complete_across_surface(logged_client, monkeypatch):
    client, complete = logged_client
    partial = deepcopy(complete)
    partial.leituras[1].estado = leitor.FALHOU
    partial.leituras[1].motivo = "fonte indisponível no teste"
    partial.leituras[1].linhas.clear()
    monkeypatch.setattr(
        wealthfolio_views.leitor,
        "consolidar_v2",
        lambda **_kwargs: partial,
    )

    for path in ("/dashboard/", "/insights/", "/holdings/", "/accounts/", "/activities/"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.context["wf_shell"]["partial"] is True
        assert response.context["wf_shell"]["sources_responded"] == 1
        assert response.context["wf_shell"]["sources_expected"] == 2

    api = client.get("/api/wealthfolio/dashboard/")
    assert api.status_code == 200
    assert api.json()["coverage"] == {"complete": False, "responded": 1, "expected": 2}


def test_empty_snapshot_has_explicit_empty_or_unavailable_states(logged_client, monkeypatch):
    client, _snapshot = logged_client
    empty = leitor.Consolidado()
    monkeypatch.setattr(
        wealthfolio_views.leitor,
        "consolidar_v2",
        lambda **_kwargs: empty,
    )

    dashboard = client.get("/dashboard/", {"tab": "investments"})
    insights = client.get("/insights/", {"tab": "summary"})
    holdings = client.get("/holdings/")

    assert dashboard.status_code == 200
    assert dashboard.context["wf_dashboard"]["state"] == "empty"
    assert insights.status_code == 200
    insights_body = insights.content.decode()
    assert any(
        marker in insights_body
        for marker in ("Insights indisponíveis", "Nenhuma exposição publicada.")
    )
    assert holdings.status_code == 200
    assert holdings.context["wf_page"]["state"] == "empty"


def test_json_contract_is_serializable_and_keeps_currency_boundaries(logged_client):
    client, _snapshot = logged_client

    dashboard = client.get(
        "/api/wealthfolio/dashboard/",
        {"data": "2026-09-20", "periodo": "3m"},
    )
    insights = client.get(
        "/api/wealthfolio/insights/",
        {"data": "2026-09-20", "periodo": "3m", "dimensao": "classe"},
    )

    assert dashboard.status_code == 200
    dashboard_body = dashboard.json()
    assert dashboard_body["coverage"]["complete"] is True
    assert dashboard_body["base_currency"] == "BRL"
    assert dashboard_body["total_base"] == "1500.00"
    assert all(isinstance(item["total"], str) for item in dashboard_body["totals"])

    assert insights.status_code == 200
    insights_body = insights.json()
    assert insights_body["dimension"] == "classe"
    assert insights_body["items"]
    assert all(isinstance(total["total"], str) for item in insights_body["items"] for total in item["totals"])


def test_read_only_surface_does_not_mutate_published_snapshot(logged_client):
    client, snapshot = logged_client
    before = deepcopy(snapshot)

    for path in ("/dashboard/", "/insights/", "/holdings/", "/accounts/", "/activities/"):
        assert client.get(path).status_code == 200

    assert snapshot == before
