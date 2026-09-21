"""Contratos visuais das páginas derivadas do shell Wealthfolio.

Estes testes não validam valores financeiros. Eles travam a arquitetura visual:
controles de navegação, tabelas, estados somente leitura e links de drill-down.
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
    monkeypatch.setattr(wealthfolio_views.legacy_views, "consolidar_v2", lambda **_: snapshot)
    user = get_user_model().objects.create_user("visual-owner", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.parametrize(
    ("path", "markers"),
    [
        ("/accounts/", ("Contas conectadas", "Lista", "Mapa", "Buscar")),
        ("/holdings/", ("Todas as posições", "Investimentos", "Ativos", "Passivos", "Colunas")),
        ("/activities/", ("Atividades publicadas", "Adicionar", "Modo de visualização")),
        ("/goals/", ("Metas financeiras", "Ainda não há metas", "Criar primeira meta")),
        ("/assistant/", ("Assistente financeiro", "Assistente indisponível")),
        ("/settings/", ("Configurações", "PREFERÊNCIAS", "FINANÇAS", "CONEXÕES")),
    ],
)
def test_derived_pages_expose_wealthfolio_structure(pages_client, path, markers):
    response = pages_client.get(path)
    body = response.content.decode()
    assert response.status_code == 200
    for marker in markers:
        assert marker in body


def test_account_detail_keeps_unicode_drilldown_and_read_only(pages_client):
    response = pages_client.get("/accounts/Mercado%20Pago/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Linhas publicadas" in body
    assert "Somente leitura" in body
    assert pages_client.post("/accounts/Mercado%20Pago/").status_code == 405


def test_holding_detail_and_state_contracts_remain_present(pages_client):
    detail = pages_client.get("/holdings/ETF%20Brasil/")
    assert detail.status_code == 200
    assert "Indicadores da posição" in detail.content.decode()
    missing = pages_client.get("/holdings/Não%20existe/")
    assert missing.status_code == 200
    assert "Posição não encontrada" in missing.content.decode()

