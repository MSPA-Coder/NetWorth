"""O cartão de crédito como passivo.

O Controle Bancário publica o tipo de cada conta. O saldo de um cartão é dívida
e já entra no caixa publicado, então o patrimônio não muda; o que muda é que a
tela passa a mostrar essa dívida separada do dinheiro em conta, e a projeção
deixa de tratar cartão negativo como falta de caixa.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, wealthfolio_views
from consolidado.fontes import Fonte

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def _resumo_v1(**conta_extra):
    return {
        "contrato": "patrimonio/v1",
        "sistema": "controle-bancario",
        "papel": "caixa",
        "data_de_referencia": "2026-09-16",
        "titulares": [{"id": "mariano", "nome": "Mariano"}],
        "instituicoes": [{"id": "c6", "nome": "C6", "tipo": "Banco"}],
        "contas": [
            {
                "id": "controle-bancario:conta:9", "titular": "mariano", "instituicao": "c6",
                "nome": "Cartão C6", "moeda": "BRL", "saldo": "-300.00", "saldo_inicial_em": "2025-12-31",
                **conta_extra,
            }
        ],
        "totais_por_moeda": [{"moeda": "BRL", "saldo": "-300.00", "contas": 1}],
        "posicoes": [],
        "proventos": [],
        "ativos_alternativos": [],
    }


def test_v1_le_o_tipo_da_conta():
    leitura = leitor.interpretar(CB, _resumo_v1(tipo="cartao_credito"))

    assert leitura.linhas[0].e_cartao_de_credito


def test_publicador_antigo_sem_tipo_continua_valendo_como_conta_comum():
    leitura = leitor.interpretar(CB, _resumo_v1())

    assert not leitura.linhas[0].e_cartao_de_credito


def _linha(papel, descricao, valor, tipo=""):
    fonte = CB if papel == "caixa" else CRV
    return leitor.Linha(
        fonte=fonte.nome, papel=papel, titular="Mariano", instituicao="C6",
        descricao=descricao, moeda="BRL", valor=Decimal(valor), tipo=tipo,
    )


def _consolidado():
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[
                _linha("caixa", "Conta corrente", "1000.00"),
                _linha("caixa", "Cartão C6", "-300.00", tipo="cartao_credito"),
            ]),
            leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[_linha("investimento", "ETF", "5000.00")]),
        ]
    )


@pytest.mark.django_db
def test_patrimonio_liquido_separa_a_divida_dos_cartoes(monkeypatch):
    usuario = get_user_model().objects.create_user("cartao", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(usuario)
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: _consolidado())

    resposta = client.get("/dashboard/", {"tab": "net-worth"})

    linhas = {item["label"]: item for item in resposta.context["wf_dashboard"]["net_worth"]["details"]}
    assert linhas["Caixa"]["value"] == "R$ 1.000,00"
    assert linhas["Cartões de crédito"]["value"] == "R$ -300,00"
    assert linhas["Cartões de crédito"]["percent_number"] == Decimal("0")
    # O total não muda: a dívida já estava no caixa publicado.
    assert linhas["Patrimônio líquido"]["value"] == "R$ 5.700,00"


@pytest.mark.django_db
def test_sem_cartao_o_detalhamento_fica_como_antes(monkeypatch):
    usuario = get_user_model().objects.create_user("sem-cartao", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(usuario)
    sem_cartao = leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[_linha("caixa", "Conta corrente", "1000.00")]),
            leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[_linha("investimento", "ETF", "5000.00")]),
        ]
    )
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: sem_cartao)

    resposta = client.get("/dashboard/", {"tab": "net-worth"})

    rotulos = [item["label"] for item in resposta.context["wf_dashboard"]["net_worth"]["details"]]
    assert rotulos == ["Investimentos", "Caixa", "Patrimônio líquido"]
