"""View-models puros para o shell compatível com Wealthfolio.

Este módulo recebe um ``Consolidado`` ou o contexto produzido por
``wealthfolio_views._base_context``. Não faz consultas, não grava preferências
e não conhece templates: sua saída é uma árvore imutável de dados de tela.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from consolidado import leitor

from .models import Money

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class UnavailableVM:
    available: bool = False
    reason: str = "A fonte não publica este dado."


@dataclass(frozen=True, slots=True)
class CoverageVM:
    complete: bool
    responded: int
    expected: int
    missing_sources: tuple[str, ...] = ()
    omissions: tuple[str, ...] = ()
    status: str = "ok"

    @property
    def message(self) -> str:
        if self.complete:
            return "Dados completos."
        return "Dados parciais: " + "; ".join(self.missing_sources + self.omissions)


@dataclass(frozen=True, slots=True)
class ShellVM:
    product: str
    language: str
    user_label: str
    active_area: str
    navigation: tuple[tuple[str, str], ...]
    coverage: CoverageVM


@dataclass(frozen=True, slots=True)
class MetricVM:
    key: str
    label: str
    values: tuple[Money, ...] = ()
    delta: tuple[Money, ...] = ()
    percent: Decimal | None = None
    state: str = "ok"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PeriodVM:
    key: str
    label: str
    active: bool = False


@dataclass(frozen=True, slots=True)
class SeriesPointVM:
    day: date
    values: tuple[Money, ...] = ()
    secondary_values: tuple[Money, ...] = ()


@dataclass(frozen=True, slots=True)
class SeriesVM:
    key: str
    label: str
    points: tuple[SeriesPointVM, ...] = ()
    available: bool = True


@dataclass(frozen=True, slots=True)
class ChartVM:
    key: str
    series: tuple[SeriesVM, ...] = ()
    state: str = "ok"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AccountVM:
    key: str
    label: str
    values: tuple[Money, ...]
    source: str
    link: str = ""


@dataclass(frozen=True, slots=True)
class PositionVM:
    key: str
    label: str
    values: tuple[Money, ...]
    quantity: Decimal | None
    institution: str
    source: str
    link: str = ""
    classification: str = "Não classificado"


@dataclass(frozen=True, slots=True)
class BreakdownItemVM:
    key: str
    label: str
    values: tuple[Money, ...]
    percent: Decimal | None = None
    classified: bool = True


@dataclass(frozen=True, slots=True)
class FlowVM:
    day: date
    nature: str
    inflow: tuple[Money, ...]
    outflow: tuple[Money, ...]
    net: tuple[Money, ...]
    lines: int
    source: str


@dataclass(frozen=True, slots=True)
class DashboardInvestmentsVM:
    hero: MetricVM
    periods: tuple[PeriodVM, ...]
    chart: ChartVM
    accounts: tuple[AccountVM, ...]
    positions: tuple[PositionVM, ...]
    coverage: CoverageVM
    empty_state: str | None = None


@dataclass(frozen=True, slots=True)
class NetWorthVM:
    hero: MetricVM
    periods: tuple[PeriodVM, ...]
    chart: ChartVM
    detail_assets: tuple[BreakdownItemVM, ...]
    # O ritmo mensal junta fotos e fluxos: vive em `contexto.ritmo_mensal_do_patrimonio`.
    coverage: CoverageVM
    empty_state: str | None = None


@dataclass(frozen=True, slots=True)
class SpendingVM:
    hero: MetricVM
    comparison: MetricVM | UnavailableVM
    stats: tuple[MetricVM, ...]
    chart: ChartVM
    where: tuple[BreakdownItemVM, ...] | UnavailableVM
    recent_activity: tuple[FlowVM, ...] | UnavailableVM
    budget: UnavailableVM
    events: UnavailableVM
    coverage: CoverageVM
    empty_state: str | None = None


@dataclass(frozen=True, slots=True)
class InsightsSummaryVM:
    metrics: tuple[MetricVM, ...]
    composition_cards: tuple[BreakdownItemVM, ...]
    treemap: tuple[BreakdownItemVM, ...]
    target_allocation: UnavailableVM
    coverage: CoverageVM


@dataclass(frozen=True, slots=True)
class PerformanceVM:
    metrics: tuple[MetricVM, ...]
    series: ChartVM
    coverage: CoverageVM


@dataclass(frozen=True, slots=True)
class IncomeVM:
    metrics: tuple[MetricVM, ...]
    history: ChartVM | UnavailableVM
    sources: tuple[BreakdownItemVM, ...]
    coverage: CoverageVM


@dataclass(frozen=True, slots=True)
class WealthfolioVM:
    shell: ShellVM
    investments: DashboardInvestmentsVM
    net_worth: NetWorthVM
    spending: SpendingVM
    insights_summary: InsightsSummaryVM
    performance: PerformanceVM
    income: IncomeVM


def _context(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {"consolidado": value}


def _consolidado(context: Mapping[str, Any]) -> leitor.Consolidado:
    value = context.get("consolidado")
    if not isinstance(value, leitor.Consolidado):
        raise TypeError("view-models exigem context['consolidado'] do tipo Consolidado")
    return value


def _coverage(obj: leitor.Consolidado) -> CoverageVM:
    missing = tuple(
        reading.fonte.nome
        for reading in obj.leituras
        if not reading.respondeu
    ) + tuple(obj.incompletas)
    omissions = tuple(
        f"{reading.fonte.nome}: {lacuna}"
        for reading in obj.leituras
        for lacuna in reading.lacunas
    )
    complete = obj.completo and not omissions
    return CoverageVM(complete, len(obj.fontes_que_responderam), len(obj.leituras), missing, omissions, "ok" if complete else "partial")


def _money(value: Any, currency: str) -> Money:
    return Money(value, currency)


def _values_by_currency(lines: list[Any], *, absolute: bool = False) -> tuple[Money, ...]:
    grouped: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        amount = line.valor_bruto if absolute else line.valor
        grouped[line.moeda] += amount
    return tuple(_money(value, currency) for currency, value in sorted(grouped.items()))


def _values_from_totals(totals: Any) -> tuple[Money, ...]:
    return tuple(_money(item["total"], item["moeda"]) for item in (totals or ()))


def _periods(context: Mapping[str, Any], active: str) -> tuple[PeriodVM, ...]:
    raw = context.get("periodos_dashboard") or (
        ("1m", "1 mês"), ("3m", "3 meses"), ("ano", "Este ano"),
        ("1a", "1 ano"), ("5a", "5 anos"), ("tudo", "Tudo"),
    )
    return tuple(PeriodVM(str(key), str(label), str(key) == active) for key, label in raw)


def _chart_from_context(context: Mapping[str, Any], key: str, labels: tuple[str, ...]) -> ChartVM:
    chart = context.get("grafico")
    if not chart or not getattr(chart, "curvas", None):
        return ChartVM(key, (), "empty", "Ainda não há histórico suficiente para este período.")
    series: list[SeriesVM] = []
    for curve in chart.curvas:
        points = []
        for point in getattr(curve, "pontos", ()):
            value = getattr(point, "valor", None)
            currency = str(context.get("moeda_base") or "BRL")
            if value is not None:
                points.append(SeriesPointVM(point.data, (_money(value, currency),)))
        series.append(SeriesVM(str(getattr(curve, "classe", "serie")), str(getattr(curve, "rotulo", "")), tuple(points)))
    return ChartVM(key, tuple(series))


def _accounts(context: Mapping[str, Any], obj: leitor.Consolidado) -> tuple[AccountVM, ...]:
    rows = []
    for index, ((institution, currency), group) in enumerate(_group_lines(obj.linhas, "instituicao")):
        rows.append(AccountVM(f"conta:{institution}:{currency}:{index}", institution, _values_by_currency(group), "consolidado"))
    return tuple(rows)


def _group_lines(lines: list[Any], attribute: str):
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for line in lines:
        groups[(str(getattr(line, attribute, "") or "Não informado"), line.moeda)].append(line)
    for key in sorted(groups):
        yield key, groups[key]


def _positions(obj: leitor.Consolidado) -> tuple[PositionVM, ...]:
    return tuple(
        PositionVM(
            line.id_de_origem or f"posicao:{index}", line.descricao, (Money(line.valor, line.moeda),),
            line.quantidade, line.instituicao, line.fonte, line.link,
            line.classe or "Não classificado",
        )
        for index, line in enumerate(obj.linhas)
        if line.papel == "investimento"
    )


def _flows(context: Mapping[str, Any], obj: leitor.Consolidado) -> tuple[FlowVM, ...]:
    # O resumo por natureza perde a data. Para o gráfico, use sempre a série
    # diária publicada no envelope; nunca reconstrua dias a partir de totais.
    source_flows = obj.fluxos
    result = []
    for item in source_flows:
        if not isinstance(item, Mapping):
            continue
        currency = str(item.get("moeda") or "BRL")
        day = item.get("data")
        if isinstance(day, str):
            try:
                day = date.fromisoformat(day)
            except ValueError:
                continue
        if not isinstance(day, date):
            continue
        result.append(FlowVM(day, str(item.get("natureza") or "Não informado"), (_money(item.get("entradas", ZERO), currency),), (_money(item.get("saidas", ZERO), currency),), (_money(item.get("liquido", ZERO), currency),), int(item.get("linhas", 0) or 0), str(item.get("fonte") or "Controle Bancário")))
    return tuple(result)


def _metric(key: str, label: str, values: tuple[Money, ...], *, detail: str = "", state: str = "ok", delta: tuple[Money, ...] = (), percent: Decimal | None = None) -> MetricVM:
    return MetricVM(key, label, values, delta, percent, state, detail)


def _investments(context: Mapping[str, Any], obj: leitor.Consolidado, coverage: CoverageVM) -> DashboardInvestmentsVM:
    investment_lines = [line for line in obj.linhas if line.papel == "investimento"]
    current = context.get("investments_total")
    currency = str(context.get("moeda_base") or "BRL")
    values = (_money(current, currency),) if current is not None else _values_by_currency(investment_lines)
    variation = context.get("investimentos_variacao") or {}
    delta = (_money(variation["absoluto"], currency),) if variation.get("absoluto") is not None else ()
    hero = _metric("portfolio_total", "Patrimônio investido", values, delta=delta, percent=variation.get("percentual"), state="partial" if not coverage.complete else "ok")
    return DashboardInvestmentsVM(hero, _periods(context, str(context.get("periodo") or "1a")), _chart_from_context(context, "investments", ("Investimentos",)), _accounts(context, obj), _positions(obj), coverage, "Nenhuma posição publicada." if not investment_lines else None)


def _net_worth(context: Mapping[str, Any], obj: leitor.Consolidado, coverage: CoverageVM) -> NetWorthVM:
    currency = str(context.get("moeda_base") or "BRL")
    total = context.get("total")
    values = (_money(total, currency),) if total is not None else _values_from_totals(getattr(obj, "totais_por_moeda", ()))
    variation = context.get("variacao_patrimonio") or {}
    delta = (_money(variation["absoluto"], currency),) if variation.get("absoluto") is not None else ()
    hero = _metric("net_worth", "Patrimônio líquido", values, delta=delta, percent=variation.get("percentual"), state="partial" if not coverage.complete else "ok")
    detail = []
    for item in context.get("detalhamento_patrimonio") or ():
        detail.append(BreakdownItemVM(str(item.get("nome") or "Não classificado"), str(item.get("nome") or "Não classificado"), (_money(item["valor"], currency),), item.get("percentual")))
    if not detail:
        for label, role in (("Investimentos", "investimento"), ("Caixa", "caixa")):
            lines = [line for line in obj.linhas if line.papel == role]
            if lines:
                detail.append(BreakdownItemVM(role, label, _values_by_currency(lines)))
    detail.append(BreakdownItemVM("total", "Patrimônio líquido", values, Decimal("100") if values else None)) if detail else None
    return NetWorthVM(hero, _periods(context, str(context.get("periodo") or "1a")), _chart_from_context(context, "net_worth", ("Patrimônio",)), tuple(detail), coverage, "Nenhum ativo publicado." if not obj.linhas else None)


def _spending(context: Mapping[str, Any], obj: leitor.Consolidado, coverage: CoverageVM) -> SpendingVM:
    # Um contexto de teste, cache ou navegação pode carregar chaves analíticas
    # antigas. Sem leituras atuais, elas não podem ressuscitar números na tela.
    summaries = context.get("resumo_gastos") or () if obj.leituras else ()
    values = []
    stats = []
    for summary in summaries:
        currency = str(summary.get("moeda") or "BRL")
        expense = _money(summary.get("gastos", ZERO), currency)
        income = _money(summary.get("receitas", ZERO), currency)
        net = _money(summary.get("liquido", ZERO), currency)
        values.append(expense)
        stats.extend((_metric("income", "Receitas", (income,)), _metric("expenses", "Gastos", (expense,)), _metric("net", "Líquido", (net,))))
    hero = _metric("expenses", "Gastos", tuple(values), state="partial" if not coverage.complete else "ok")
    flow_rows = _flows(context, obj) if obj.leituras else ()
    chart = ChartVM("spending", (), "empty", "A fonte publica fluxos agregados por dia.")
    if flow_rows:
        chart = ChartVM("spending", (SeriesVM("net", "Líquido", tuple(SeriesPointVM(flow.day, flow.net) for flow in flow_rows)),))
    return SpendingVM(hero, UnavailableVM(reason="A fonte não publica comparação anterior por categoria."), tuple(stats), chart, UnavailableVM(reason="Controle Bancário v2 publica natureza agregada, não categorias."), UnavailableVM(reason="A fonte não publica atividades individuais."), UnavailableVM(reason="Orçamento é configuração local e ainda não está disponível nesta tela."), UnavailableVM(reason="Eventos não são publicados pela fonte."), coverage, "Sem fluxos publicados." if not flow_rows else None)


def _composition(context: Mapping[str, Any], dimension: str, limit: int = 4) -> tuple[BreakdownItemVM, ...]:
    insight = context.get("insights") or {}
    if insight.get("dimensao") != dimension:
        return ()
    result = []
    for item in (insight.get("itens") or ())[:limit]:
        values = _values_from_totals(item.get("totais_por_moeda"))
        result.append(BreakdownItemVM(str(item.get("id") or item.get("nome") or ""), str(item.get("nome") or "Não classificado"), values, item.get("percentual"), str(item.get("nome")) != "Não classificado"))
    return tuple(result)


def _cost_basis(obj: leitor.Consolidado) -> MetricVM:
    """O custo das posições, por moeda, só quando TODAS o publicaram.

    Custo de parte da carteira ao lado do valor da carteira inteira faria o
    ganho parecer maior do que é. E fica por moeda: custo em dólar é histórico,
    e convertê-lo pela taxa de hoje daria um custo que nunca existiu.
    """
    lines = [line for line in obj.linhas if line.papel == "investimento"]
    if not lines:
        return _metric("cost_basis", "Custo de aquisição", (), state="unavailable", detail="Nenhuma posição publicada.")
    missing = sum(1 for line in lines if line.custo is None)
    if missing:
        return _metric(
            "cost_basis", "Custo de aquisição", (), state="unavailable",
            detail=f"{missing} de {len(lines)} posições sem custo publicado (a fonte não publica custo de datas passadas).",
        )
    grouped: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        grouped[line.moeda] += line.custo
    values = tuple(_money(value, currency) for currency, value in sorted(grouped.items()))
    return _metric("cost_basis", "Custo de aquisição", values, detail=f"{len(lines)} posições, por moeda")


def _insights_summary(context: Mapping[str, Any], obj: leitor.Consolidado, coverage: CoverageVM) -> InsightsSummaryVM:
    currency = str(context.get("moeda_base") or "BRL")
    total = context.get("total")
    metrics = (
        _metric("portfolio_value", "Valor da carteira", (_money(total, currency),) if total is not None else _values_from_totals(obj.totais_por_moeda)),
        _metric("cash", "Caixa", (_money(context["cash_total"], currency),) if context.get("cash_total") is not None else ()),
        _metric("invested", "Investido", (_money(context["investments_total"], currency),) if context.get("investments_total") is not None else ()),
        _cost_basis(obj),
    )
    cards = []
    for dimension in ("classe", "moeda", "instituicao", "mercado"):
        cards.extend(_composition(context, dimension, 1))
    treemap = _composition(context, str((context.get("insights") or {}).get("dimensao") or "classe"), 100)
    return InsightsSummaryVM(metrics, tuple(cards[:4]), treemap, UnavailableVM(reason="Alocação-alvo ainda não está disponível no view-model."), coverage)


def _performance(context: Mapping[str, Any], coverage: CoverageVM) -> PerformanceVM:
    metrics = []
    series = []
    for item in context.get("desempenhos") or ():
        if item.get("retorno_percentual") is not None:
            metrics.append(_metric("return", "Retorno acumulado", (), percent=item["retorno_percentual"]))
        points = []
        last = item.get("ultimo") or {}
        if last.get("data") and last.get("retorno_acumulado") is not None:
            day = last["data"] if isinstance(last["data"], date) else date.fromisoformat(str(last["data"]))
            points.append(SeriesPointVM(day, (), (Money(last["retorno_acumulado"], item.get("moeda") or "BRL"),)))
        series.append(SeriesVM(str(item.get("metodo") or "twr"), str(item.get("metodo") or "TWR"), tuple(points)))
    return PerformanceVM(tuple(metrics), ChartVM("performance", tuple(series), "ok" if series else "empty", "Sem performance publicada."), coverage)


def _income(context: Mapping[str, Any], coverage: CoverageVM) -> IncomeVM:
    sources = []
    metrics = []
    for item in context.get("rendas") or ():
        currency = str(item.get("moeda") or "BRL")
        value = _money(item.get("total", ZERO), currency)
        metrics.append(_metric("income_total", "Rendimentos", (value,)))
        sources.append(BreakdownItemVM(currency, currency, (value,)))
    while len(metrics) < 3:
        labels = ("Média mensal", "Fontes de renda", "Rendimentos")
        metrics.append(_metric(f"income_{len(metrics)}", labels[len(metrics)], (), state="unavailable", detail="Histórico de renda não publicado para este recorte."))
    return IncomeVM(tuple(metrics[:3]), UnavailableVM(reason="A fonte ainda não publica histórico detalhado de rendimentos."), tuple(sources), coverage)


def build_view_models(value: Any, *, user_label: str = "", active_area: str = "dashboard") -> WealthfolioVM:
    """Constrói a árvore completa de view-models sem tocar em banco."""
    context = _context(value)
    obj = _consolidado(context)
    coverage = _coverage(obj)
    return WealthfolioVM(
        ShellVM("NetWorth", "pt-BR", user_label, active_area, (("dashboard", "Dashboard"), ("insights", "Insights"), ("holdings", "Posições"), ("accounts", "Contas"), ("goals", "Metas")), coverage),
        _investments(context, obj, coverage),
        _net_worth(context, obj, coverage),
        _spending(context, obj, coverage),
        _insights_summary(context, obj, coverage),
        _performance(context, coverage),
        _income(context, coverage),
    )


__all__ = [
    "AccountVM", "BreakdownItemVM", "ChartVM", "CoverageVM", "DashboardInvestmentsVM",
    "FlowVM", "IncomeVM", "InsightsSummaryVM", "MetricVM", "NetWorthVM", "PerformanceVM",
    "PeriodVM", "PositionVM", "SeriesPointVM", "SeriesVM", "ShellVM", "SpendingVM",
    "UnavailableVM", "WealthfolioVM", "build_view_models",
]
