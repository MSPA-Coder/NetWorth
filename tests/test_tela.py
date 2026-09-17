"""A tela do patrimônio.

O leitor garante que o total nunca existe sem o estado das fontes; estes testes
garantem que a **tela** não esconde esse estado. As duas metades são
necessárias: um objeto honesto renderizado por um template desatento produz
exatamente o número enganoso que este aplicativo não pode mostrar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client

from consolidado import leitor, views
from consolidado.fontes import Fonte

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def linha(valor="100.00", moeda="BRL") -> leitor.Linha:
    return leitor.Linha(
        fonte=CB.nome,
        papel="caixa",
        titular="Mariano",
        instituicao="C6",
        descricao="Conta corrente",
        moeda=moeda,
        valor=Decimal(valor),
    )


@pytest.fixture
def logado():
    User.objects.create_user(username="quem", password="senha-longa-o-suficiente")
    cliente = Client()
    cliente.force_login(User.objects.get(username="quem"))
    return cliente


def com_consolidado(monkeypatch, consolidado):
    monkeypatch.setattr(views, "consolidar", lambda *_args, **_kwargs: consolidado)


def test_sem_sessao_a_tela_nao_abre():
    resposta = Client().get("/patrimonio/")

    assert resposta.status_code == 302
    assert "/login" in resposta["Location"]


def test_total_incompleto_e_dito_na_tela(logado, monkeypatch):
    """O teste que importa.

    Uma fonte fora do ar produz um total menor. Ele pode ser mostrado -- o que
    ele não pode é ser mostrado calado.
    """
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()]),
                leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="não respondeu"),
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()
    # O template quebra linha entre os números; o que importa é a frase, não a
    # formatação dela.
    corrido = " ".join(corpo.split())

    assert "não é o patrimônio inteiro" in corrido
    assert "feito de 1 de 2 fontes" in corrido
    assert "não respondeu" in corrido


def test_com_todas_as_fontes_o_aviso_some(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()]),
                leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[linha("50.00")]),
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "não é o patrimônio inteiro" not in corpo


def test_moedas_aparecem_separadas_e_com_o_simbolo_de_cada_uma(logado, monkeypatch):
    """Um "R$" carimbado num valor em dólar é erro que ninguém revisa."""
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(
                    fonte=CB,
                    estado=leitor.OK,
                    linhas=[linha("1234.56"), linha("7641.72", moeda="USD")],
                )
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "R$ 1.234,56" in corpo
    assert "US$ 7.641,72" in corpo
    assert "R$ 7.641,72" not in corpo


def test_fonte_configurada_pela_metade_aparece(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])],
            incompletas=["Renda Variável"],
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "Configurada pela metade" in corpo
    assert "Renda Variável" in corpo


def test_data_invalida_avisa_em_vez_de_estourar(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    resposta = logado.get("/patrimonio/", {"data": "30/06/2026"})

    assert resposta.status_code == 200
    assert "Data inválida" in resposta.content.decode()


def test_a_tela_traz_os_cabecalhos_defensivos(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    resposta = logado.get("/patrimonio/")

    assert "default-src 'self'" in resposta["Content-Security-Policy"]
    assert resposta["X-Frame-Options"] == "DENY"


def test_saude_responde_sem_sessao():
    resposta = Client().get("/health/")

    assert resposta.status_code == 200
    assert resposta.json() == {"servico": "networth", "status": "ok"}
