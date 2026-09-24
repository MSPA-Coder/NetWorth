from datetime import date
from decimal import Decimal

from consolidado import leitor
from consolidado.fontes import Fonte
from consolidado.wealthfolio_compat.view_models import (
    UnavailableVM,
    build_view_models,
)

CB = Fonte("CB", "Controle Bancário", "caixa", "", "")
CRV = Fonte("CRV", "Controle de Renda Variável", "investimento", "", "")


def consolidated(*, partial=False, empty=False):
    if empty:
        return leitor.Consolidado()
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CB,
                estado=leitor.OK,
                linhas=[leitor.Linha(CB.nome, "caixa", "Pessoa", "Banco", "Conta", "BRL", Decimal("1000"))],
                fluxos=[{"data": date(2026, 9, 1), "moeda": "BRL", "natureza": "gerencial", "entradas": Decimal("100"), "saidas": Decimal("40"), "liquido": Decimal("60"), "linhas": 2}],
            ),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.FALHOU if partial else leitor.OK,
                linhas=[] if partial else [leitor.Linha(CRV.nome, "investimento", "Pessoa", "Corretora", "ETF", "USD", Decimal("200"), quantidade=Decimal("2"), classe="acao")],
            ),
        ]
    )


def context_for(obj):
    return {
        "consolidado": obj,
        "periodo": "3m",
        "periodos_dashboard": [("1m", "1 mês"), ("3m", "3 meses")],
        "moeda_base": "BRL",
        "total": Decimal("2000"),
        "cash_total": Decimal("1000"),
        "investments_total": Decimal("1000"),
        "variacao_patrimonio": {"absoluto": Decimal("100"), "percentual": Decimal("5")},
        "resumo_gastos": [{"moeda": "BRL", "receitas": Decimal("100"), "gastos": Decimal("40"), "liquido": Decimal("60"), "linhas": 2}],
        "fluxos_por_natureza": [{"data": "2026-09-01", "moeda": "BRL", "natureza": "gerencial", "entradas": Decimal("100"), "saidas": Decimal("40"), "liquido": Decimal("60"), "linhas": 2}],
        "detalhamento_patrimonio": [{"nome": "Investimentos", "valor": Decimal("1000"), "percentual": Decimal("50")}],
        "rendas": [{"moeda": "BRL", "total": Decimal("25"), "por_tipo": {"dividendo": Decimal("25")}}],
        "desempenhos": [{"moeda": "USD", "metodo": "TWR", "retorno_percentual": Decimal("8"), "ultimo": {"data": date(2026, 9, 1), "retorno_acumulado": Decimal("0.08")}}],
    }


def test_shell_e_dashboard_investimentos_preservam_periodo_moeda_e_posicoes():
    vm = build_view_models(context_for(consolidated()))

    assert vm.shell.language == "pt-BR"
    assert vm.shell.navigation[0] == ("dashboard", "Dashboard")
    assert vm.investments.periods[1].active is True
    assert vm.investments.hero.values[0].currency == "BRL"
    assert vm.investments.positions[0].values[0].currency == "USD"
    assert vm.investments.positions[0].classification == "acao"


def test_net_worth_tem_detalhamento():
    vm = build_view_models(context_for(consolidated()))

    assert vm.net_worth.hero.delta[0].amount == Decimal("100")
    assert vm.net_worth.detail_assets[0].label == "Investimentos"
    assert vm.net_worth.detail_assets[-1].label == "Patrimônio líquido"


def test_spending_expone_fluxos_mas_nao_inventa_categorias_ou_atividades():
    vm = build_view_models(context_for(consolidated()))

    assert vm.spending.stats[0].label == "Receitas"
    assert vm.spending.chart.series[0].points[0].values[0].amount == Decimal("60")
    assert isinstance(vm.spending.where, UnavailableVM)
    assert isinstance(vm.spending.recent_activity, UnavailableVM)
    assert isinstance(vm.spending.budget, UnavailableVM)
    assert isinstance(vm.spending.events, UnavailableVM)


def test_insights_tem_quatro_metricas_e_alocacao_alvo_indisponivel():
    context = context_for(consolidated())
    context["insights"] = {"dimensao": "classe", "itens": [{"id": "acao", "nome": "Ações", "totais_por_moeda": [{"moeda": "USD", "total": Decimal("200")}], "percentual": Decimal("100")}]}
    vm = build_view_models(context)

    assert len(vm.insights_summary.metrics) == 4
    assert vm.insights_summary.metrics[-1].state == "unavailable"
    assert isinstance(vm.insights_summary.target_allocation, UnavailableVM)
    assert vm.insights_summary.treemap[0].label == "Ações"


def test_performance_e_income_tem_contrato_estavel():
    vm = build_view_models(context_for(consolidated()))

    assert vm.performance.metrics[0].percent == Decimal("8")
    assert vm.performance.series.series[0].points[0].secondary_values[0].currency == "USD"
    assert len(vm.income.metrics) == 3
    assert vm.income.sources[0].values[0].amount == Decimal("25")
    assert isinstance(vm.income.history, UnavailableVM)


def test_cobertura_parcial_e_estados_vazios_sao_explicitos():
    partial = build_view_models(context_for(consolidated(partial=True)))
    empty = build_view_models(context_for(consolidated(empty=True)))

    assert partial.shell.coverage.complete is False
    assert partial.investments.hero.state == "partial"
    assert empty.investments.empty_state == "Nenhuma posição publicada."
    assert empty.spending.empty_state == "Sem fluxos publicados."
