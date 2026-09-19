"""Regras puras da composição da página Insights."""

from datetime import date
from decimal import Decimal

import pytest

from consolidado import insights, leitor
from consolidado.fontes import Fonte

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def linha(
    *,
    fonte=CRV.nome,
    papel="investimento",
    titular="Mariano",
    instituicao="Genial",
    descricao="WEGE3",
    moeda="BRL",
    valor="100.00",
    classe="acao",
    mercado="B3",
):
    return leitor.Linha(
        fonte=fonte,
        papel=papel,
        titular=titular,
        instituicao=instituicao,
        descricao=descricao,
        moeda=moeda,
        valor=Decimal(valor),
        classe=classe,
        mercado=mercado,
    )


def test_montar_separa_dimensoes_e_moedas_sem_inferir_setor_ou_regiao():
    dados = [
        linha(valor="100.00"),
        linha(instituicao="XP", descricao="IVVB11", valor="50.00"),
        linha(
            fonte=CB.nome,
            papel="caixa",
            instituicao="C6",
            descricao="Conta corrente",
            moeda="USD",
            valor="20.00",
            classe="",
            mercado="",
        ),
    ]

    resultado = insights.montar(
        dados,
        referencia=date(2026, 9, 18),
        periodo="1a",
        data="2026-09-18",
        dimensao="classe",
        fonte_completa=True,
        quantidade_fontes=2,
        quantidade_fontes_esperadas=2,
    )

    nomes = {item["nome"] for item in resultado["itens"]}
    assert nomes == {"Caixa", "acao"}
    assert resultado["total"].possivel is False
    assert resultado["nao_classificadas"] == 0

    setores = insights.montar(
        dados,
        referencia=date(2026, 9, 18),
        periodo="1a",
        data="2026-09-18",
        dimensao="setor",
    )
    assert [item["nome"] for item in setores["itens"]] == [insights.NAO_CLASSIFICADO]
    assert setores["nao_classificadas"] == 3


def test_montar_aplica_filtro_por_id_opaco_e_mantem_linhas_de_origem():
    dados = [linha(), linha(instituicao="XP", descricao="IVVB11", valor="50.00")]
    base = insights.montar(
        dados,
        referencia=date(2026, 9, 18),
        periodo="1a",
        data="2026-09-18",
        dimensao="instituicao",
    )
    genial = next(item for item in base["itens"] if item["nome"] == "Genial")

    filtrado = insights.montar(
        dados,
        referencia=date(2026, 9, 18),
        periodo="1a",
        data="2026-09-18",
        dimensao="mercado",
        filtro_dimensao="instituicao",
        filtro=genial["id"],
    )

    assert filtrado["filtro_nome"] == "Genial"
    assert filtrado["linhas"] == 1
    assert filtrado["detalhe_linhas"][0]["nome"] == "WEGE3"
    assert filtrado["itens"][0]["nome"] == "B3"


def test_id_inexistente_nao_restringe_o_escopo():
    resultado = insights.montar(
        [linha()],
        referencia=date(2026, 9, 18),
        periodo="1a",
        data="2026-09-18",
        dimensao="classe",
        filtro_dimensao="classe",
        filtro="insight-inexistente",
    )

    assert resultado["filtro"] == ""
    assert resultado["linhas"] == 1
    assert resultado["filtro_nome"] == ""
