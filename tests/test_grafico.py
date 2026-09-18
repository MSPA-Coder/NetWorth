"""O gráfico do histórico: geometria pura, sem banco nem Django."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from consolidado import grafico
from consolidado.fotos import Ponto

SERIES = (
    ("patrimonio", "Patrimônio", "curva-patrimonio"),
    ("investimentos", "Investimentos", "curva-investimentos"),
)


def ponto(dia: str, investimentos, patrimonio=None) -> Ponto:
    return Ponto(
        data=date.fromisoformat(dia),
        investimentos=None if investimentos is None else Decimal(investimentos),
        patrimonio=None if patrimonio is None else Decimal(patrimonio),
    )


def test_sem_pontos_nao_ha_grafico():
    assert grafico.montar([], SERIES) is None
    assert grafico.montar([ponto("2026-01-01", None)], SERIES) is None


def test_dia_sem_numero_interrompe_a_linha():
    """Uma reta ligando os dois lados do buraco desenharia dias que não têm
    número honesto."""
    pontos = [
        ponto("2026-01-01", "100"),
        ponto("2026-01-02", None),
        ponto("2026-01-03", "120"),
    ]

    desenho = grafico.montar(pontos, SERIES)

    (curva,) = desenho.curvas
    assert curva.classe == "curva-investimentos"
    assert curva.caminho.count("M") == 2
    assert "L" not in curva.caminho


def test_curva_sem_nenhum_valor_nao_e_desenhada():
    desenho = grafico.montar([ponto("2025-06-30", "100"), ponto("2025-07-31", "110")], SERIES)

    assert [curva.rotulo for curva in desenho.curvas] == ["Investimentos"]


def test_o_eixo_comeca_no_zero_e_cobre_o_maior_valor():
    """Eixo que não começa no zero faz uma variação de 2% parecer um salto."""
    pontos = [ponto("2026-01-01", "90000", "310000"), ponto("2026-06-30", "95000", "325000")]

    desenho = grafico.montar(pontos, SERIES)

    rotulos = [marca.rotulo for marca in desenho.eixo_y]
    assert rotulos[0] == "0"
    assert rotulos[-1] == "400 mil"
    posicoes = [marca.posicao for marca in desenho.eixo_y]
    assert posicoes == sorted(posicoes, reverse=True)
    assert posicoes[0] == desenho.base
    assert posicoes[-1] == desenho.topo


def test_serie_longa_marca_os_anos():
    desenho = grafico.montar([ponto("2022-05-09", "1"), ponto("2026-09-16", "2")], SERIES)

    assert [marca.rotulo for marca in desenho.eixo_x] == ["2023", "2024", "2025", "2026"]


def test_serie_curta_marca_os_meses():
    desenho = grafico.montar([ponto("2026-01-01", "1"), ponto("2026-04-15", "2")], SERIES)

    assert [marca.rotulo for marca in desenho.eixo_x] == ["fev/26", "mar/26", "abr/26"]


def test_rotulos_de_valor():
    assert grafico._rotulo_de_valor(0) == "0"
    assert grafico._rotulo_de_valor(250_000) == "250 mil"
    assert grafico._rotulo_de_valor(1_200_000) == "1,2 mi"
    assert grafico._rotulo_de_valor(2_000_000) == "2 mi"


def test_ultimo_ponto_de_cada_mes_do_mais_novo_para_o_mais_velho():
    pontos = [
        ponto("2026-01-30", "1"),
        ponto("2026-01-31", "2"),
        ponto("2026-02-27", "3"),
        ponto("2026-03-05", "4"),
    ]

    mensais = grafico.ultimo_de_cada_mes(pontos)

    assert [p.data for p in mensais] == [
        date(2026, 3, 5),
        date(2026, 2, 27),
        date(2026, 1, 31),
    ]


def test_variacao_sem_base_nao_existe():
    assert grafico.variacao(Decimal("110"), Decimal("100")) == Decimal("10")
    assert grafico.variacao(Decimal("110"), None) is None
    assert grafico.variacao(None, Decimal("100")) is None
    assert grafico.variacao(Decimal("110"), Decimal("0")) is None
