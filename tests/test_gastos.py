"""O detalhe da aba Gastos: só lançamento gerencial realizado conta como gasto."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from consolidado import contexto, gastos
from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.activities import STATUS_ERROR, STATUS_OK, ActivitySourceResult
from consolidado.wealthfolio_compat.models import ActivityDTO, Money

CB = Fonte("CB", "Controle Bancário", "caixa", "http://cb.teste", "t", "https://cb.publico")
CRV = Fonte("CRV", "Controle de Renda Variável", "investimento", "http://crv.teste", "t")
TRIMESTRE = gastos.Periodo(date(2026, 7, 1), date(2026, 9, 28))


def atividade(n, dia, valor, *, tipo="despesa", categoria="Saúde", natureza="gerencial", status="realizado", moeda="BRL"):
    return ActivityDTO(
        id=f"controle-bancario:atividade:{n}",
        date=dia,
        description=f"Lançamento {n}",
        kind=tipo,
        status=status,
        value=Money(Decimal(valor), moeda),
        realized_value=Money(Decimal(valor), moeda),
        source="controle-bancario",
        category=categoria,
        category_kind=natureza,
        link=f"/lancamentos/{n}/",
    )


class FonteFalsa:
    """Responde por período e por página, e anota o que foi pedido."""

    def __init__(self, por_periodo, *, falhar_pagina=None, tamanho=gastos.TAMANHO_DA_PAGINA):
        self.por_periodo = por_periodo
        self.falhar_pagina = falhar_pagina
        self.tamanho = tamanho
        self.pedidos = []

    def __call__(self, fonte, *, inicio, fim, page, page_size, filters):
        self.pedidos.append((fonte.apelido, inicio, fim, page, dict(filters)))
        if page == self.falhar_pagina:
            return ActivitySourceResult(fonte.apelido, STATUS_ERROR, error="a fonte respondeu HTTP 503")
        itens = self.por_periodo.get(inicio, ())
        paginas = max(1, -(-len(itens) // self.tamanho))
        fatia = tuple(itens[(page - 1) * self.tamanho : page * self.tamanho])
        return ActivitySourceResult(fonte.apelido, STATUS_OK, fatia, len(itens), page, page_size, paginas)


def test_resumo_de_gastos_ignora_transferencia_movimentacao_e_ajuste():
    fluxos = [
        {"moeda": "BRL", "natureza": "gerencial", "entradas": Decimal("100"), "saidas": Decimal("40"), "liquido": Decimal("60"), "linhas": 3},
        {"moeda": "BRL", "natureza": "transferencia", "entradas": Decimal("5000"), "saidas": Decimal("5000"), "liquido": Decimal("0"), "linhas": 2},
        {"moeda": "BRL", "natureza": "movimentacao", "entradas": Decimal("0"), "saidas": Decimal("900"), "liquido": Decimal("-900"), "linhas": 1},
        {"moeda": "BRL", "natureza": "ajuste_de_base", "entradas": Decimal("7000"), "saidas": Decimal("0"), "liquido": Decimal("7000"), "linhas": 1},
    ]

    [resumo] = contexto.resumo_gastos(fluxos)

    assert (resumo["receitas"], resumo["gastos"], resumo["liquido"], resumo["linhas"]) == (
        Decimal("100"), Decimal("40"), Decimal("60"), 3,
    )


def test_categorias_somam_so_despesa_gerencial_realizada_e_agrupam_o_resto():
    nomes = ["Saúde", "Viagem", "Casa", "Lazer", "Veículos", "Outros", "Loterias", "Educação"]
    itens = [atividade(i, date(2026, 9, 1), str(100 - i * 10), categoria=nome) for i, nome in enumerate(nomes)]
    itens += [
        atividade(20, date(2026, 9, 2), "999", tipo="receita"),
        atividade(21, date(2026, 9, 2), "5000", natureza="transferencia"),
        atividade(22, date(2026, 9, 2), "777", status="a_vencer"),
    ]

    resultado = gastos.detalhe([CB], TRIMESTRE, buscar=FonteFalsa({TRIMESTRE.inicio: itens}))

    categorias = resultado["categorias"]
    assert [linha["label"] for linha in categorias] == nomes[:6] + ["Demais categorias (2)"]
    # 100 + 90 + ... + 30 = 520; receita, transferência e previsto ficam fora.
    assert categorias[0] == {"label": "Saúde", "value": "R$ 100,00", "percent": "19,2%", "percent_number": Decimal("19.2")}
    assert categorias[-1]["value"] == "R$ 70,00"


def test_recentes_trazem_sinal_pelo_tipo_categoria_e_link_publico():
    itens = [
        atividade(1, date(2026, 9, 10), "98.00", categoria="Cartão de Crédito"),
        atividade(2, date(2026, 9, 12), "98.00", tipo="receita", categoria="Cartão de Crédito"),
    ]

    [estorno, tarifa] = gastos.detalhe([CB], TRIMESTRE, buscar=FonteFalsa({TRIMESTRE.inicio: itens}))["recentes"]

    assert estorno["value"] == "+R$ 98,00" and estorno["positive"] is True
    assert tarifa["value"] == "−R$ 98,00" and tarifa["positive"] is False
    assert tarifa["date"] == "10/09/2026 · Cartão de Crédito"
    assert tarifa["url"] == "https://cb.publico/lancamentos/1/"


def test_comparacao_usa_o_mesmo_numero_de_dias_antes_do_periodo():
    anterior = gastos.periodo_anterior(TRIMESTRE)
    assert anterior == gastos.Periodo(date(2026, 4, 2), date(2026, 6, 30))
    fonte = FonteFalsa(
        {
            TRIMESTRE.inicio: [atividade(1, date(2026, 9, 1), "750")],
            anterior.inicio: [atividade(2, date(2026, 5, 1), "1000")],
        }
    )

    comparacao = gastos.detalhe([CB], TRIMESTRE, buscar=fonte)["comparacao"]

    assert comparacao == {
        "label": "R$ 250,00 a menos (−25,0%) que no período anterior (02/04/2026 a 30/06/2026)",
        "positive": True,
    }


def test_periodo_anterior_recua_o_mesmo_trecho_do_calendario():
    def anterior(inicio, fim, chave):
        return gastos.periodo_anterior(gastos.Periodo(inicio, fim), chave)

    # Mês corrente até o dia 23 contra o mês passado até o dia 23.
    assert anterior(date(2026, 9, 1), date(2026, 9, 23), "este_mes") == gastos.Periodo(date(2026, 8, 1), date(2026, 8, 23))
    # Mês fechado contra mês fechado, mesmo com tamanhos diferentes.
    assert anterior(date(2026, 9, 1), date(2026, 9, 30), "mes_passado") == gastos.Periodo(date(2026, 8, 1), date(2026, 8, 31))
    assert anterior(date(2026, 3, 1), date(2026, 3, 31), "mes_passado") == gastos.Periodo(date(2026, 2, 1), date(2026, 2, 28))
    # YTD contra o mesmo trecho do ano anterior.
    assert anterior(date(2026, 1, 1), date(2026, 9, 22), "ano") == gastos.Periodo(date(2025, 1, 1), date(2025, 9, 22))
    assert anterior(date(2026, 6, 23), date(2026, 9, 22), "3m") == gastos.Periodo(date(2026, 3, 23), date(2026, 6, 22))
    # Período rolante com as duas pontas: o anterior não repete o dia 23/06.
    assert anterior(date(2026, 6, 23), date(2026, 9, 23), "3m") == gastos.Periodo(date(2026, 3, 23), date(2026, 6, 22))


def test_periodo_anterior_sem_lancamento_nao_vira_gasto_a_mais():
    fonte = FonteFalsa({TRIMESTRE.inicio: [atividade(1, date(2026, 9, 1), "750")]})

    comparacao = gastos.detalhe([CB], TRIMESTRE, buscar=fonte)["comparacao"]

    assert comparacao == {
        "label": "Sem lançamentos no período anterior (02/04/2026 a 30/06/2026) para comparar.",
        "positive": False,
    }


def test_periodo_sem_inicio_nao_tem_comparacao():
    fonte = FonteFalsa({None: [atividade(1, date(2026, 9, 1), "10")]})

    resultado = gastos.detalhe([CB], gastos.Periodo(None, date(2026, 9, 28)), buscar=fonte)

    assert resultado["comparacao"]["label"] == "Sem período anterior para comparar."
    assert len(fonte.pedidos) == 1


def test_le_todas_as_paginas_so_da_fonte_de_caixa_com_o_filtro():
    itens = [atividade(i, date(2026, 9, 1), "1") for i in range(5)]
    fonte = FonteFalsa({TRIMESTRE.inicio: itens}, tamanho=2)

    resultado = gastos.detalhe([CB, CRV], TRIMESTRE, buscar=fonte)

    assert resultado["categorias"][0]["value"] == "R$ 5,00"
    pedidos_do_atual = [p for p in fonte.pedidos if p[1] == TRIMESTRE.inicio]
    assert sorted(p[3] for p in pedidos_do_atual) == [1, 2, 3]
    assert {p[0] for p in fonte.pedidos} == {"CB"}
    assert all(p[4] == {"status": "realizado", "natureza": "gerencial"} for p in fonte.pedidos)


def test_uma_pagina_que_falha_derruba_o_detalhe_inteiro():
    itens = [atividade(i, date(2026, 9, 1), "1") for i in range(5)]

    resultado = gastos.detalhe([CB], TRIMESTRE, buscar=FonteFalsa({TRIMESTRE.inicio: itens}, tamanho=2, falhar_pagina=2))

    assert resultado["disponivel"] is False
    assert "categorias" not in resultado
    assert "HTTP 503" in resultado["motivo"]


def test_sem_fonte_de_caixa_o_detalhe_diz_o_motivo():
    resultado = gastos.detalhe([CRV], TRIMESTRE, buscar=FonteFalsa({}))

    assert resultado == {"disponivel": False, "motivo": "Nenhuma fonte de caixa configurada."}


def test_analise_traz_todas_as_categorias_com_variacao_e_o_gasto_por_mes():
    anterior = gastos.periodo_anterior(TRIMESTRE, "3m")
    fonte = FonteFalsa(
        {
            TRIMESTRE.inicio: [
                atividade(1, date(2026, 7, 5), "300", categoria="Saúde"),
                atividade(2, date(2026, 8, 5), "100", categoria="Saúde"),
                atividade(3, date(2026, 8, 9), "50", categoria="Lazer"),
            ],
            anterior.inicio: [
                atividade(4, date(2026, 5, 1), "500", categoria="Saúde"),
                atividade(5, date(2026, 5, 2), "80", categoria="Viagem"),
            ],
        }
    )

    resultado = gastos.analise([CB], TRIMESTRE, chave="3m", buscar=fonte)

    linhas = {linha["label"]: linha for linha in resultado["categorias"]}
    assert linhas["Saúde"]["value"] == "R$ 400,00" and linhas["Saúde"]["lines"] == 2
    assert linhas["Saúde"]["delta"]["label"] == "−R$ 100,00" and linhas["Saúde"]["delta"]["percent"] == "−20,0%"
    assert linhas["Lazer"]["delta"]["percent"] == "novo"
    # Categoria que sumiu no período também é mudança.
    assert linhas["Viagem"]["value"] == "R$ 0,00" and linhas["Viagem"]["delta"]["direction"] == "down"
    # O trimestre vai até 28/09: setembro seria parcial, julho e agosto não.
    assert [(m["label"], m["value"]) for m in resultado["meses"]] == [("07/2026", "R$ 300,00"), ("08/2026", "R$ 150,00")]
    assert resultado["meses"][1]["delta"]["label"] == "−R$ 150,00"


def test_analise_sem_anterior_com_lancamento_deixa_a_variacao_em_branco():
    fonte = FonteFalsa({TRIMESTRE.inicio: [atividade(1, date(2026, 7, 5), "300")]})

    [linha] = gastos.analise([CB], TRIMESTRE, chave="3m", buscar=fonte)["categorias"]

    assert linha["delta"] == {"label": "—", "percent": "", "direction": ""}


def test_mes_que_o_periodo_corta_aparece_como_parcial():
    periodo = gastos.Periodo(date(2026, 3, 23), date(2026, 9, 23))
    fonte = FonteFalsa(
        {periodo.inicio: [atividade(1, date(2026, 3, 25), "10"), atividade(2, date(2026, 4, 5), "10"), atividade(3, date(2026, 9, 5), "10")]}
    )

    meses = gastos.analise([CB], periodo, chave="6m", buscar=fonte)["meses"]

    assert [m["label"] for m in meses] == ["03/2026 (parcial)", "04/2026", "09/2026 (parcial)"]
