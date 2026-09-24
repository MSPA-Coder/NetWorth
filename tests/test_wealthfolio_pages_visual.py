"""Páginas derivadas do shell Wealthfolio: drill-down, estados e abas.

Rótulos, cabeçalhos e a estrutura visual copiada do Wealthfolio não são
testados aqui (docs/TESTES.md, T5): mudam por decisão de tela e se conferem
olhando a tela. Fica o que o servidor decide -- o nome com espaço e acento
chega ao detalhe, a posição inexistente tem estado próprio, a aba vem da
query. Somente leitura (405) está em `test_wealthfolio_parity_matrix.py`.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte

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
