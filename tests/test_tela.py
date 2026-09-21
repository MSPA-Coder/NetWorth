"""Contrato de navegação: o shell Wealthfolio é a única interface pública."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def logado():
    user = get_user_model().objects.create_user(
        username="navegacao",
        password="senha-longa-o-suficiente",
    )
    client = Client()
    client.force_login(user)
    return client


def _query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


def test_dashboard_e_shell_exigem_sessao():
    response = Client().get("/dashboard/")

    assert response.status_code == 302
    assert response["Location"].startswith("/login?next=")


def test_patrimonio_legado_redireciona_para_aba_de_investimentos(logado):
    response = logado.get("/patrimonio/", {"visao": "investimentos", "periodo": "3m"})

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/dashboard/"
    assert _query(response["Location"]) == {"periodo": ["3m"], "tab": ["investments"]}


def test_patrimonio_legado_redireciona_para_aba_de_patrimonio(logado):
    response = logado.get("/patrimonio/", {"visao": "patrimonio", "data": "2026-09-20"})

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/dashboard/"
    assert _query(response["Location"]) == {"data": ["2026-09-20"], "tab": ["net-worth"]}


def test_patrimonio_legado_redireciona_para_aba_de_gastos(logado):
    response = logado.get("/patrimonio/", {"visao": "gastos"})

    assert response.status_code == 302
    assert _query(response["Location"]) == {"tab": ["spending"]}


def test_patrimonio_legado_redireciona_insights_para_rota_de_insights(logado):
    response = logado.get("/patrimonio/", {"visao": "insights"})

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/insights/"
    assert _query(response["Location"]) == {"tab": ["summary"]}


def test_historico_legado_redireciona_para_patrimonio_do_shell(logado):
    response = logado.get("/patrimonio/historico/", {"periodo": "desde-2026"})

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == "/dashboard/"
    assert _query(response["Location"]) == {"periodo": ["tudo"], "tab": ["net-worth"]}


def test_historico_legado_exige_sessao():
    response = Client().get("/patrimonio/historico/")

    assert response.status_code == 302
    assert response["Location"].startswith("/login?next=")
