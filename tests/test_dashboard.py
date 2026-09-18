from __future__ import annotations

from datetime import date
from decimal import Decimal

from consolidado import dashboard, leitor
from consolidado.fontes import Fonte

FONTE = Fonte("CB", "Controle Bancário", "caixa", "http://cb.teste", "token")
CRV = Fonte("CRV", "Renda Variável", "investimento", "http://crv.teste", "token")


def _v2(**alteracoes):
    corpo = {
        "contrato": "patrimonio/v2",
        "papel": "caixa",
        "data_de_referencia": "2026-09-18",
        "periodo_dos_fluxos": {"inicio": "2026-01-01", "fim": "2026-09-18"},
        "contas": [],
        "posicoes": [],
        "fluxos": [],
        "ganhos_realizados_por_moeda": [],
        "renda_por_moeda": [],
        "desempenho_por_moeda": [],
        "enderecos": {},
    }
    corpo.update(alteracoes)
    return corpo


def test_v2_valida_intervalo_fluxo_e_link_de_secao():
    leitura = leitor.interpretar(
        FONTE,
        _v2(
            contas=[
                {
                    "id": "controle-bancario:conta:1",
                    "titular": "m",
                    "instituicao": "b",
                    "nome": "Conta",
                    "moeda": "BRL",
                    "saldo": "0.00",
                }
            ],
            fluxos=[
                {
                    "data": "2026-09-18",
                    "moeda": "BRL",
                    "natureza": "gerencial",
                    "entradas": "10.25",
                    "saidas": "0.00",
                    "liquido": "10.25",
                    "linhas": 1,
                }
            ],
            enderecos={"fluxo": "/transactions/"},
        ),
    )

    assert leitura.respondeu
    assert leitura.inicio == date(2026, 1, 1)
    assert leitura.data == date(2026, 9, 18)
    assert leitura.fluxos[0]["liquido"] == Decimal("10.25")
    assert leitura.secoes[0]["link"] == "http://cb.teste/transactions/"


def test_v2_numero_json_em_fluxo_recusa_a_fonte_inteira():
    leitura = leitor.interpretar(
        FONTE,
        _v2(
            fluxos=[{
                "data": "2026-09-18", "moeda": "BRL", "natureza": "gerencial",
                "entradas": 10.25, "saidas": "0.00", "liquido": "10.25", "linhas": 1,
            }]
        ),
    )

    assert leitura.estado == leitor.FALHOU
    assert "texto" in leitura.motivo


def test_agregadores_preservam_moedas_e_natureza():
    eventos = [
        {"data": date(2026, 9, 1), "moeda": "BRL", "natureza": "receita", "valor": "10"},
        {"data": date(2026, 9, 1), "moeda": "BRL", "natureza": "receita", "valor": "2.50"},
        {"data": date(2026, 9, 1), "moeda": "USD", "natureza": "receita", "valor": "3"},
    ]

    assert dashboard.fluxos_cb(eventos) == [
        {
            "data": date(2026, 9, 1),
            "moeda": "BRL",
            "natureza": "receita",
            "total": Decimal("12.50"),
            "linhas": 2,
        },
        {
            "data": date(2026, 9, 1),
            "moeda": "USD",
            "natureza": "receita",
            "total": Decimal("3"),
            "linhas": 1,
        },
    ]


def test_top_posicoes_soma_o_mesmo_ativo_em_corretoras_diferentes():
    linhas = [
        leitor.Linha(
            fonte="Renda Variável", papel="investimento", titular="Mariano",
            instituicao="Genial", descricao="WEGE3", moeda="BRL",
            valor=Decimal("100"), quantidade=Decimal("2"), exposicao_bruta=Decimal("100"),
            link="http://crv.teste/positions/1",
        ),
        leitor.Linha(
            fonte="Renda Variável", papel="investimento", titular="Mariano",
            instituicao="XP", descricao="WEGE3", moeda="BRL",
            valor=Decimal("50"), quantidade=Decimal("1"), exposicao_bruta=Decimal("50"),
            link="http://crv.teste/positions/2",
        ),
    ]

    (posicao,) = dashboard.top_posicoes(linhas)

    assert posicao["valor"] == Decimal("150")
    assert posicao["quantidade"] == Decimal("3")
    assert posicao["instituicoes"] == ["Genial", "XP"]
    assert posicao["percentual"] == Decimal("100")
    assert posicao["link"] == ""

def test_periodos_sao_inclusivos():
    assert dashboard.intervalo_periodo("YTD", date(2026, 9, 18)) == (
        date(2026, 1, 1),
        date(2026, 9, 18),
    )
    assert dashboard.intervalo_periodo("5Y", date(2026, 9, 18)) == (
        date(2021, 9, 18),
        date(2026, 9, 18),
    )


def test_v2_interpreta_os_agregados_reais_do_crv():
    leitura = leitor.interpretar(
        CRV,
        {
            "contrato": "patrimonio/v2",
            "papel": "investimento",
            "data_de_referencia": "2026-09-18",
            "periodo": {"nome": "year", "inicio": "2025-09-18", "fim": "2026-09-18"},
            "titulares": [{"id": "mariano", "nome": "Mariano"}],
            "instituicoes": [{"id": "genial", "nome": "Genial"}],
            "contas": [],
            "posicoes": [{
                "id": "controle-renda-variavel:posicao:1",
                "titular": "mariano", "instituicao": "genial", "instrumento": "WEGE3",
                "moeda": "BRL", "valor_a_mercado": "15630.00",
                "exposicao_bruta": "15630.00", "custo_total": "12000.00",
                "resultado_nao_realizado": "3630.00", "retorno_nao_realizado": "0.3025",
                "endereco": "/positions/1",
            }],
            "omitidas": {"sem_cotacao": 0},
            "ganhos_realizados_por_moeda": [{
                "moeda": "BRL", "instrumento": "acao", "ganhos": "20.00",
                "perdas": "-5.00", "resultado": "15.00", "transacoes": 2,
            }],
            "renda_por_moeda": [{
                "moeda": "BRL", "total": "8.00", "por_tipo": {"dividendo": "8.00"},
            }],
            "desempenho_por_moeda": [{
                "moeda": "BRL", "metodo": "TWR", "pontos": [{
                    "data": "2026-09-18", "valor_a_mercado": "15630.00",
                    "fluxo_neutralizado": "0.00", "renda_total": "8.00",
                    "renda_por_tipo": {"dividendo": "8.00"},
                    "indice_twr": "1.10", "retorno_acumulado": "0.10",
                }],
            }],
            "enderecos": {"performance": "/performance"},
            "qualidade": {"status": "ok"},
        },
    )

    assert leitura.respondeu
    assert leitura.linhas[0].ganho_nao_realizado == Decimal("3630.00")
    assert leitura.ganhos_realizados[0]["valor"] == Decimal("15.00")
    assert leitura.rendas[0]["por_tipo"]["dividendo"] == Decimal("8.00")
    assert leitura.twr[0]["pontos"][0]["indice_twr"] == Decimal("1.10")
    assert leitura.secoes[0]["link"] == "http://crv.teste/performance"
