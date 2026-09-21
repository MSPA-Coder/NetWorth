"""Views for the single Wealthfolio-compatible surface.

All financial values are assembled from the same ``Consolidado`` object and
keep the currency/coverage warnings that are part of NetWorth's contract.
Legacy URLs are translated to this surface in :mod:`consolidado.urls`; the
retired templates are never rendered by a public route.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from consolidado import dashboard, fotos, grafico
from consolidado import insights as insights_builder
from consolidado import views as legacy_views
from consolidado.cambio import MOEDA_BASE, converter_totais
from consolidado.fontes import fontes_configuradas
from consolidado.templatetags.dinheiro import dinheiro
from consolidado.wealthfolio_compat.activities import compose_activities, fetch_activities
from consolidado.wealthfolio_compat.analytics import (
    IncomeRecord,
    PerformanceRecord,
    compose_analytics,
    fetch_analytics,
)
from consolidado.wealthfolio_compat.view_models import (
    UnavailableVM,
    build_view_models,
)

PERIODS = legacy_views.PERIODOS_DASHBOARD


def _date_and_period(request: HttpRequest) -> tuple[date, str, date]:
    raw = (request.GET.get("data") or "").strip()
    try:
        anchor = date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        anchor = date.today()
    reference = anchor
    period = request.GET.get("periodo") or (
        "3m" if request.GET.get("tab") == "spending" else "1a"
    )
    if period not in PERIODS and period not in {"este_mes", "mes_passado"}:
        period = "1a"
    if period == "mes_passado":
        reference = anchor.replace(day=1) - timedelta(days=1)
    return reference, period, anchor


def _base_context(request: HttpRequest, *, visao: str) -> dict[str, Any]:
    """Build one request snapshot shared by every Wealthfolio route."""

    reference, period, period_anchor = _date_and_period(request)
    inicio = legacy_views._inicio_do_periodo(period, reference)
    consolidado = legacy_views.consolidar_v2(
        inicio=inicio,
        data=reference,
        periodo="all",
    )
    conversion = converter_totais(consolidado.totais_por_moeda, reference)
    by_role = consolidado.por("papel")
    cash = legacy_views._valor_do_papel(by_role, "caixa", reference)
    investments = legacy_views._valor_do_papel(by_role, "investimento", reference)
    flows = dashboard.resumo_fluxos_por_natureza(consolidado)
    pontos = [item for item in fotos.curvas(desde=inicio) if item.data <= reference]
    total_now = conversion.total if consolidado.completo and conversion.possivel else None
    investments_now = investments if consolidado.completo else None
    pontos = legacy_views._incluir_foto_atual(pontos, reference, total_now, investments_now)
    chart = grafico.montar(
        pontos,
        (("patrimonio", "Patrimônio", "curva-patrimonio"), ("investimentos", "Investimentos", "curva-investimentos")),
    )
    tree = legacy_views._arvore_de_contas(
        consolidado.linhas,
        referencia=reference,
        visao=visao,
        periodo=period,
        data_da_tela=reference,
        grupo_parametro=request.GET.get("grupo") or "",
        conta_parametro=request.GET.get("conta") or "",
    )
    detail = []
    if conversion.possivel:
        for role, label in (("investimento", "Investimentos"), ("caixa", "Caixa")):
            value = legacy_views._valor_do_papel(by_role, role, reference)
            if value is not None:
                detail.append({"nome": label, "valor": value, "percentual": value * 100 / conversion.total if conversion.total else Decimal("0")})
    # Keep both series available to the client.  The Investments and Net Worth
    # tabs differ only in the selected metric; recomputing one from the other
    # in JavaScript would incorrectly hide a missing/partial source.
    variacao_investimentos = legacy_views._variacao(investments_now, pontos[:-1], "investimentos")
    variacao_patrimonio = legacy_views._variacao(total_now, pontos[:-1], "patrimonio")
    insights_context = None
    insights_overview = []
    if visao == "insights":
        insights_context = insights_builder.montar(
            consolidado.linhas,
            referencia=reference,
            periodo=period,
            data=reference.isoformat(),
            dimensao=request.GET.get("dimensao") or "classe",
            filtro_dimensao=request.GET.get("filtro_dimensao") or "",
            filtro=request.GET.get("filtro") or "",
            busca=request.GET.get("busca") or "",
            ordenar=request.GET.get("ordenar") or "valor",
            direcao=request.GET.get("direcao") or "desc",
            grupo=tree["grupo_selecionado"],
            conta=tree["conta_selecionada"],
            fonte_completa=consolidado.completo,
            quantidade_fontes=len(consolidado.fontes_que_responderam),
            quantidade_fontes_esperadas=len(consolidado.leituras),
            motivo_fontes="; ".join(
                f"{item.fonte.nome}: {item.motivo or ', '.join(item.lacunas)}"
                for item in consolidado.leituras
                if not item.respondeu or item.lacunas
            ),
        )
        account_count = sum(group.get("quantidade", 0) for group in tree["grupos"])
        insights_overview.append(
            {
                "label": "Contas",
                "value": account_count,
                "detail": "contas publicadas",
                "available": bool(account_count),
            }
        )
        for dimension, label, detail_label in (
            ("classe", "Classes", "classes publicadas"),
            ("regiao", "Regiões", "regiões publicadas"),
            ("setor", "Setores", "setores publicados"),
        ):
            names = {
                insights_builder.nome_da_dimensao(line, dimension)
                for line in consolidado.linhas
            }
            names.discard(insights_builder.NAO_CLASSIFICADO)
            names.discard(insights_builder.NAO_INFORMADO)
            insights_overview.append(
                {
                    "label": label,
                    "value": len(names),
                    "detail": detail_label,
                    "available": bool(names),
                }
            )
    total = conversion.total if conversion.possivel else None
    return {
        "consolidado": consolidado,
        "conversao": conversion,
        "moeda_base": MOEDA_BASE,
        "referencia": reference,
        "data_da_tela": reference,
        "visao": visao,
        "periodo": period,
        "period_anchor": period_anchor,
        "periodos_dashboard": [(key, label) for key, (label, _cut) in PERIODS.items()],
        "dashboard_tab": request.GET.get("tab") or "investments",
        "total": total,
        "cash_total": cash,
        "investments_total": investments,
        "cash_investments_total": total,
        "fluxos_por_natureza": flows,
        "resumo_gastos": legacy_views._resumo_gastos(flows),
        "ritmo_mensal": legacy_views._ritmo_mensal(consolidado.fluxos),
        "holdings": dashboard.top_posicoes(consolidado, limite=100),
        "top_posicoes": dashboard.top_posicoes(consolidado, limite=10),
        "grafico": chart,
        "pontos_grafico": pontos,
        "investimentos_variacao": variacao_investimentos,
        "patrimonio_variacao": variacao_patrimonio,
        "cartoes_de_contas": tree["grupos"],
        "arvore_drilldown": tree["grupos"],
        "grupos_drilldown": tree["grupos"],
        "grupo_selecionado": tree["grupo_selecionado"],
        "conta_selecionada": tree["conta_selecionada"],
        "detalhamento_patrimonio": detail,
        # Kept under the old key for existing templates and callers.
        "variacao_patrimonio": variacao_patrimonio,
        "insights": insights_context,
        "insights_overview": insights_overview,
        "desempenhos": legacy_views._desempenhos(consolidado),
        "rendas": consolidado.rendas,
        "ganhos_realizados": consolidado.ganhos_realizados,
        "rendas_resumo": dashboard.resumo_renda(consolidado),
        "ganhos_resumo": dashboard.resumo_ganhos(consolidado),
        "fluxos_mensais": legacy_views._ritmo_mensal(consolidado.fluxos),
        "links_de_secao": consolidado.secoes,
        "qualidade_v2": consolidado.qualidade,
        "is_partial": not consolidado.completo,
    }


def _money_label(values: Any) -> str:
    """Formata uma coleção de Money sem nunca somar moedas distintas."""

    rendered = [dinheiro(item.amount, item.currency) for item in values or ()]
    return " · ".join(rendered) if rendered else "Indisponível"


def _percent_label(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return ""
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value.quantize(Decimal('0.01'))}%".replace(".", ",")


def _view_model(request: HttpRequest, context: dict[str, Any], *, active: str):
    """Return the immutable compatibility tree used by a public surface.

    The legacy consolidator remains the transport boundary for now, but the
    templates do not need to know that.  Keeping one cached tree per active
    surface prevents each partial from reinterpreting the source payload and
    gives the migration to the DTO transport a single seam.
    """

    cache = context.setdefault("_wealthfolio_view_models", {})
    if active not in cache:
        cache[active] = build_view_models(
            context,
            user_label=request.user.get_username(),
            active_area=active,
        )
    return cache[active]


def _analytics_context(context: dict[str, Any]) -> dict[str, Any]:
    """Consulta recursos analíticos v3 sem transformar a fonte em réplica.

    O v2 continua sendo a compatibilidade agregada para as métricas básicas.
    Quando um publicador oferece um recurso v3 detalhado, ele é preferido nas
    abas de Insights; uma fonte que não oferece a capacidade permanece visível
    como lacuna, nunca como zero ou série inventada.
    """

    fontes = fontes_configuradas()
    if not fontes:
        return {}
    inicio = legacy_views._inicio_do_periodo(context["periodo"], context["data_da_tela"])
    fim = context["data_da_tela"]
    result: dict[str, Any] = {}
    for resource in ("income", "performance", "events"):
        result[resource] = compose_analytics(
            resource,
            (
                fetch_analytics(
                    fonte,
                    resource=resource,
                    inicio=inicio,
                    fim=fim,
                    page=1,
                    page_size=500,
                )
                for fonte in fontes
            ),
        )
    return result


def _metric_vm(metric: Any) -> dict[str, Any]:
    values = tuple(getattr(metric, "values", ()) or ())
    return {
        "key": getattr(metric, "key", ""),
        "label": getattr(metric, "label", ""),
        "value": _money_label(values),
        "state": getattr(metric, "state", "ok"),
        "detail": getattr(metric, "detail", ""),
        "percent": _percent_label(getattr(metric, "percent", None), signed=True),
    }


def _insights_page_vm(request: HttpRequest, context: dict[str, Any]) -> dict[str, Any]:
    """Adapt the immutable Insights tree to display-only template data."""

    vm = _view_model(request, context, active="insights")
    summary = vm.insights_summary
    raw = context.get("insights") or {}

    raw_items_by_key = {
        item.get("id"): item
        for item in raw.get("itens") or ()
        if item.get("id")
    }

    def breakdown(item: Any) -> dict[str, Any]:
        published = raw_items_by_key.get(getattr(item, "key", ""), {})
        return {
            "key": item.key,
            "name": item.label,
            "value": _money_label(item.values),
            "percent": _percent_label(item.percent),
            "percent_number": item.percent,
            "classified": item.classified,
            "url": published.get("url") or "#",
        }

    dimensions = []
    for item in raw.get("dimensoes") or ():
        dimensions.append(
            {
                "name": item.get("nome"),
                "url": item.get("url"),
                "active": item.get("ativa", False),
            }
        )
    account_options = [
        {"id": item.get("id"), "name": item.get("nome")}
        for item in context.get("arvore_drilldown") or ()
    ]
    detail_rows = []
    for item in raw.get("itens") or ():
        conversion = item.get("conversao")
        detail_rows.append(
            {
                "name": item.get("nome") or "Não classificado",
                "url": item.get("url") or "#",
                "institutions": item.get("instituicoes") or (),
                "lines": item.get("linhas", 0),
                "percent": _percent_label(item.get("percentual")),
                "value": (
                    dinheiro(conversion.total, context["moeda_base"])
                    if getattr(conversion, "possivel", False)
                    else "Indisponível"
                ),
            }
        )

    performance = []
    performance_composition = (context.get("analytics") or {}).get("performance")
    if performance_composition and performance_composition.items:
        for item in performance_composition.items:
            if not isinstance(item, PerformanceRecord):
                continue
            points = [
                {"date": point_day.isoformat(), "value": str(value)}
                for point_day, value in item.points
            ]
            last_value = item.points[-1][1] if item.points else None
            performance.append(
                {
                    "method": item.method or "TWR",
                    "currency": item.currency or context["moeda_base"],
                    "return": _percent_label(last_value * 100 if last_value is not None else None),
                    "series_json": json.dumps(points, ensure_ascii=False),
                }
            )
    else:
        for item in context.get("desempenhos") or ():
            performance.append(
                {
                    "method": item.get("metodo") or "TWR",
                    "currency": item.get("moeda") or context["moeda_base"],
                    "return": _percent_label(item.get("retorno_percentual")),
                    "series_json": item.get("serie_json") or "[]",
                }
            )

    income_composition = (context.get("analytics") or {}).get("income")
    income_history = []
    if income_composition and income_composition.items:
        records = [item for item in income_composition.items if isinstance(item, IncomeRecord)]
        grouped: dict[tuple[str, str], Decimal] = {}
        for item in records:
            key = (item.date.strftime("%Y-%m"), item.value.currency)
            grouped[key] = grouped.get(key, Decimal("0")) + item.value.amount
        max_by_currency: dict[str, Decimal] = {}
        for (_month, currency), amount in grouped.items():
            max_by_currency[currency] = max(max_by_currency.get(currency, Decimal("0")), amount)
        income_history = [
            {
                "label": month,
                "value": dinheiro(amount, currency),
                "currency": currency,
                "percent": (amount * 100 / max_by_currency[currency]) if max_by_currency[currency] else Decimal("0"),
            }
            for (month, currency), amount in sorted(grouped.items())
        ]
        totals: dict[str, Decimal] = {}
        for item in records:
            totals[item.value.currency] = totals.get(item.value.currency, Decimal("0")) + item.value.amount
        income_metrics = [
            {
                "key": "income_total",
                "label": "Rendimentos",
                "value": " · ".join(dinheiro(amount, currency) for currency, amount in sorted(totals.items())),
                "state": "ok",
                "detail": f"{len(records)} lançamentos publicados",
                "percent": "",
            },
            {
                "key": "income_sources",
                "label": "Fontes de renda",
                "value": str(len({item.category or item.kind for item in records})),
                "state": "ok",
                "detail": "categorias publicadas",
                "percent": "",
            },
            {
                "key": "income_period",
                "label": "Período",
                "value": f"{records[0].date.strftime('%m/%Y')}" if records else "—",
                "state": "ok",
                "detail": "último recebimento publicado",
                "percent": "",
            },
        ]
        by_source: dict[tuple[str, str, str], Decimal] = {}
        for item in records:
            key = (item.category or item.kind or "Renda", item.value.currency, item.source)
            by_source[key] = by_source.get(key, Decimal("0")) + item.value.amount
        total_by_currency = next(iter(totals.values())) if len(totals) == 1 else None
        income_sources = [
            {
                "key": f"{category}:{currency}:{source}",
                "name": category,
                "value": dinheiro(amount, currency),
                "percent": (amount * 100 / total_by_currency) if total_by_currency else None,
                "percent_number": (amount * 100 / total_by_currency) if total_by_currency else None,
                "classified": bool(category),
            }
            for (category, currency, source), amount in sorted(by_source.items())
        ]
    else:
        income_metrics = [_metric_vm(item) for item in vm.income.metrics]
        income_sources = [breakdown(item) for item in vm.income.sources]
    analytics_values = tuple((context.get("analytics") or {}).values())
    analytics_partial = any(
        getattr(item, "status", "") in {"partial", "error", "unsupported", "stale"}
        for item in analytics_values
    )
    analytics_warnings = tuple(
        warning
        for item in analytics_values
        for warning in getattr(item, "warnings", ())
    )
    return {
        "tab": context.get("insights_tab") or "summary",
        "partial": not vm.shell.coverage.complete or analytics_partial,
        "warnings": analytics_warnings,
        "period": context["periodo"],
        "currency": context["moeda_base"],
        "account_options": account_options,
        "summary": {
            "available": bool(raw),
            "metrics": [_metric_vm(item) for item in summary.metrics],
            "overview": context.get("insights_overview") or (),
            "dimension_name": raw.get("dimensao_nome") or "Classe",
            "items": [breakdown(item) for item in summary.treemap],
            "dimensions": dimensions,
            "details": detail_rows,
            "target_available": getattr(summary.target_allocation, "available", False),
            "target_reason": getattr(summary.target_allocation, "reason", ""),
        },
        "performance": {
            "periods": context.get("periodos_dashboard") or (),
            "items": performance,
            "available": bool(performance),
        },
        "income": {
            "metrics": income_metrics,
            "sources": income_sources,
            "available": bool(income_sources),
            "history_available": bool(income_history) or not hasattr(vm.income.history, "reason"),
            "history": income_history,
        },
    }


def _query_url(route: str, **params: Any) -> str:
    filtered = {key: value for key, value in params.items() if value not in (None, "")}
    target = reverse(route)
    return f"{target}?{urlencode(filtered)}" if filtered else target


def _shell_vm(request: HttpRequest, context: dict[str, Any], *, active: str) -> dict[str, Any]:
    vm = _view_model(request, context, active=active)
    coverage = vm.shell.coverage
    failure_reasons = []
    for reading in context["consolidado"].leituras:
        if not reading.respondeu or reading.lacunas:
            failure_reasons.append(
                f"{reading.fonte.nome}: {reading.motivo or ', '.join(reading.lacunas)}"
            )
    return {
        "active_route": active,
        "home_url": reverse("consolidado:dashboard"),
        "dashboard_url": reverse("consolidado:dashboard"),
        "insights_url": reverse("consolidado:insights"),
        "holdings_url": reverse("consolidado:holdings"),
        "accounts_url": reverse("consolidado:accounts"),
        "activities_url": reverse("consolidado:activities"),
        "goals_url": reverse("consolidado:goals"),
        "assistant_url": reverse("consolidado:assistant"),
        "settings_url": reverse("consolidado:settings"),
        "connect_url": _query_url("consolidado:settings", secao="conexoes"),
        "logout_url": reverse("logout"),
        "user_label": vm.shell.user_label,
        "partial": not coverage.complete,
        "partial_message": "; ".join(failure_reasons) or coverage.message,
        "sources_responded": coverage.responded,
        "sources_expected": coverage.expected,
        "show_heading": False,
        "labels": {
            "dashboard": "Painel",
            "insights": "Análises",
            "holdings": "Posições",
            "activities": "Atividades",
            "goals": "Metas",
            "assistant": "Assistente",
            "settings": "Configurações",
            "connect": "Conexões",
        },
    }


def _period_items(context: dict[str, Any], *, tab: str, route: str) -> list[dict[str, Any]]:
    reference = context.get("period_anchor", context["data_da_tela"]).isoformat()
    labels = {
        "1d": "1D",
        "1s": "1S",
        "1m": "1M",
        "3m": "3M",
        "6m": "6M",
        "ano": "YTD",
        "1a": "1A",
        "5a": "5A",
        "tudo": "Máx",
    }
    dashboard_keys = {"1d", "1s", "1m", "3m", "6m", "ano", "1a", "5a", "tudo"}
    return [
        {
            "key": key,
            "label": labels.get(key, label),
            "active": key == context["periodo"],
            "url": _query_url(route, tab=tab, periodo=key, data=reference),
        }
        for key, label in context["periodos_dashboard"]
        if key in dashboard_keys
    ]


def _spending_period_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    reference = context.get("period_anchor", context["data_da_tela"]).isoformat()
    labels = {
        "este_mes": "Este mês",
        "mes_passado": "Mês passado",
        "3m": "3M",
        "6m": "6M",
        "ano": "YTD",
        "1a": "1A",
    }
    items = [
        {
            "key": key,
            "label": label,
            "active": key == context["periodo"],
            "url": _query_url(
                "consolidado:dashboard",
                tab="spending",
                periodo=key,
                data=reference,
            ),
        }
        for key, label in labels.items()
    ]
    items.append(
        {
            "key": "calendar",
            "label": "▣",
            "active": False,
            "url": _query_url(
                "consolidado:dashboard",
                tab="spending",
                periodo=context["periodo"],
                data=reference,
            ),
        }
    )
    return items


def _delta_vm(metric: Any, period_label: str) -> dict[str, Any]:
    amount = metric.delta[0].amount if getattr(metric, "delta", ()) else None
    currency = metric.delta[0].currency if getattr(metric, "delta", ()) else "BRL"
    percent = getattr(metric, "percent", None)
    return {
        "absolute": dinheiro(amount, currency) if amount is not None else "",
        "percent": _percent_label(percent, signed=True),
        "period": period_label,
        "positive": amount is not None and amount >= 0,
    }


def _dashboard_vm(request: HttpRequest, context: dict[str, Any], tab: str) -> dict[str, Any]:
    all_vm = _view_model(request, context, active="dashboard")
    period_label = next(
        (label for key, label in context["periodos_dashboard"] if key == context["periodo"]),
        {"este_mes": "Este mês", "mes_passado": "Mês passado"}.get(
            context["periodo"], "Período selecionado"
        ),
    )
    periods = (
        _spending_period_items(context)
        if tab == "spending"
        else _period_items(context, tab=tab, route="consolidado:dashboard")
    )
    tabs = [
        {"key": "investments", "label": "↗ Investimentos", "active": tab == "investments", "url": _query_url("consolidado:dashboard", tab="investments", periodo=context["periodo"], data=context["data_da_tela"].isoformat())},
        {"key": "net-worth", "label": "▣ Patrimônio líquido", "active": tab == "net-worth", "url": _query_url("consolidado:dashboard", tab="net-worth", periodo=context["periodo"], data=context["data_da_tela"].isoformat())},
        {"key": "spending", "label": "♨ Gastos", "active": tab == "spending", "url": _query_url("consolidado:dashboard", tab="spending", periodo=context["periodo"], data=context["data_da_tela"].isoformat())},
    ]
    investments = all_vm.investments

    def account_detail_url(child: dict[str, Any]) -> str:
        line = (child.get("linhas") or [{}])[0]
        account_id = line.get("instituicao") or child.get("nome")
        query: dict[str, str] = {
            "periodo": context["periodo"],
            "data": context["data_da_tela"].isoformat(),
        }
        if line.get("papel") != "investimento" and line.get("descricao"):
            query["account"] = line["descricao"]
        return f"{reverse('consolidado:account_detail', kwargs={'account_id': account_id})}?{urlencode(query)}"

    accounts = []
    for group in context["cartoes_de_contas"]:
        converted = getattr(group.get("conversao"), "total", None)
        if converted is not None:
            value = dinheiro(converted, context["moeda_base"])
        else:
            value = " · ".join(
                dinheiro(item["total"], item["moeda"])
                for item in group.get("totais_por_moeda") or ()
            )
        accounts.append(
            {
                "name": group["nome"],
                "detail": f"{group.get('quantidade', 0)} contas",
                "value": value or "Indisponível",
                "url": _query_url(
                    "consolidado:dashboard",
                    tab="investments",
                    periodo=context["periodo"],
                    data=context["data_da_tela"].isoformat(),
                    grupo=group["id"],
                ),
                "expanded": group.get("expandida", False),
                "children": [
                    {
                        "name": child["nome"],
                        "detail": child.get("subtitulo") or child.get("moeda"),
                        "value": dinheiro(
                            getattr(child.get("conversao"), "total", None),
                            context["moeda_base"],
                        )
                        if getattr(child.get("conversao"), "total", None) is not None
                        else "Indisponível",
                        # Wealthfolio opens a child account in its account
                        # detail view, where the published lines are shown.
                        # The legacy drill-down URL only expands the group on
                        # the dashboard, so derive the shell-local detail
                        # route from the first published line instead.
                        "url": account_detail_url(child),
                    }
                    for child in group.get("contas") or ()
                ],
            }
        )
    holdings = []
    for item in context["holdings"][:7]:
        name = str(item.get("descricao") or "—")
        institutions = ", ".join(item.get("instituicoes") or ())
        gain = item.get("ganho_nao_realizado")
        holdings.append(
            {
                "name": name,
                "badge": name[:4].upper(),
                "detail": f"{item.get('quantidade', 0)} un. · {institutions}".strip(" ·"),
                "value": dinheiro(item.get("valor"), item.get("moeda")),
                "delta": dinheiro(gain, item.get("moeda")) if gain is not None else "",
                "percent": _percent_label(item.get("retorno_percentual"), signed=True),
                "positive": gain is not None and gain >= 0,
                "url": reverse("consolidado:holding_detail", kwargs={"holding_id": name}),
            }
        )
    investment_chart = {
        "geometry": context["grafico"],
        "series_class": "curva-investimentos",
        "aria_label": "Evolução dos investimentos",
        "empty": context["grafico"] is None,
    }
    net_worth = all_vm.net_worth
    details = []
    for item in net_worth.detail_assets:
        details.append(
            {
                "label": item.label,
                "value": _money_label(item.values),
                "percent": _percent_label(item.percent),
                "percent_number": item.percent or Decimal("0"),
                "total": item.key == "total",
            }
        )
    spending = all_vm.spending
    stats = [
        {
            "label": metric.label,
            "value": _money_label(metric.values),
            "positive": metric.key in {"income", "net"} and any(value.amount >= 0 for value in metric.values),
        }
        for metric in spending.stats
    ]
    weekly_totals: dict[date, Decimal] = {}
    for row in context["consolidado"].fluxos:
        day = row.get("data")
        if not isinstance(day, date) or row.get("moeda") != context["moeda_base"]:
            continue
        # O Wealthfolio ancora os buckets semanais no domingo.
        week = day - timedelta(days=(day.weekday() + 1) % 7)
        weekly_totals[week] = weekly_totals.get(week, Decimal("0")) + abs(
            row.get("saidas", Decimal("0"))
        )
    weekly = sorted(weekly_totals.items())[-10:]
    maximum = max((value for _day, value in weekly), default=Decimal("0"))
    bars = [
        {
            "label": day.strftime("%d/%m"),
            "height": (value * 100 / maximum).quantize(Decimal("0.1")) if maximum else Decimal("0"),
            "height_px": int(value * 225 / maximum) if maximum else 0,
        }
        for day, value in weekly
    ]
    state = "ready" if context["consolidado"].leituras else "empty"
    return {
        "state": state,
        "tab": tab,
        "tabs": tabs,
        "periods": periods,
        "period_label": period_label,
        "meta": {
            "complete": all_vm.shell.coverage.complete,
            "sources_responded": all_vm.shell.coverage.responded,
            "sources_expected": all_vm.shell.coverage.expected,
        },
        "investments": {
            "hero_value": _money_label(investments.hero.values),
            "delta": _delta_vm(investments.hero, period_label),
            "chart": investment_chart,
            "accounts": accounts,
            "holdings": holdings,
            "remaining_count": max(0, len(context["holdings"]) - len(holdings)),
            "accounts_url": reverse("consolidado:accounts"),
            "holdings_url": reverse("consolidado:holdings"),
        },
        "net_worth": {
            "hero_value": _money_label(net_worth.hero.values),
            "delta": _delta_vm(net_worth.hero, period_label),
            "chart": {**investment_chart, "series_class": "curva-patrimonio", "aria_label": "Evolução do patrimônio líquido"},
            "details": details,
            "monthly_pace": {
                "available": not isinstance(net_worth.monthly_pace, UnavailableVM),
                "value": _money_label(getattr(net_worth.monthly_pace, "values", ())),
                "positive": bool(getattr(net_worth.monthly_pace, "values", ())) and net_worth.monthly_pace.values[0].amount >= 0,
                "detail": getattr(net_worth.monthly_pace, "detail", ""),
                "reason": getattr(net_worth.monthly_pace, "reason", ""),
                "factors": (),
            },
        },
        "spending": {
            "hero_value": _money_label(spending.hero.values),
            "comparison": {"label": getattr(spending.comparison, "reason", "Indisponível")},
            "stats": stats,
            "chart": {"bars": bars, "empty_message": spending.empty_state},
            "categories": (),
            "activities": (),
            "insights_url": reverse("consolidado:spending_insights"),
            "activities_url": reverse("consolidado:activities"),
            "budget": {"reason": spending.budget.reason},
            "events": {"reason": spending.events.reason},
        },
    }


def _page_vm(context: dict[str, Any], *, kind: str, request: HttpRequest) -> dict[str, Any]:
    titles = {
        "holdings": "Posições",
        "holding-detail": "Detalhe da posição",
        "accounts": "Contas",
        "account-detail": "Detalhe da conta",
        "activities": "Atividades",
        "spending-insights": "Detalhamento de gastos",
        "budget": "Orçamento",
        "goals": "Metas",
        "goal-new": "Metas",
        "assistant": "Assistente",
        "settings": "Configurações",
    }
    all_vm = _view_model(request, context, active=kind)
    periods = [
        {"value": item["key"], "label": item["label"], "active": item["active"]}
        for item in _period_items(context, tab="", route="consolidado:holdings")
    ]
    page: dict[str, Any] = {
        "kind": kind,
        "title": titles.get(kind, "Consulta"),
        "kicker": "NetWorth · consulta Wealthfolio",
        "subtitle": "Dados lidos do Controle Bancário e do Controle de Renda Variável.",
        "as_of_label": f"Posição em {context['data_da_tela'].strftime('%d/%m/%Y')}",
        "state": "ready",
        "meta": {
            "complete": all_vm.shell.coverage.complete,
            "sources_responded": all_vm.shell.coverage.responded,
            "sources_expected": all_vm.shell.coverage.expected,
            "warning": all_vm.shell.coverage.message,
        },
        "breadcrumbs": (),
        "actions": (),
        "rows": (),
        "toolbar": {
            "url": request.path,
            "periods": periods,
            "search": request.GET.get("busca") or "",
            "filters": (),
        },
        "back_url": reverse("consolidado:dashboard"),
    }
    if kind == "settings":
        # Settings is a read-only projection of the Wealthfolio navigation.
        # Keep the selected section in the URL while carrying the caller's
        # other query scope (date/period/source filters) to every item.
        section_specs = (
            ("PREFERÊNCIAS", (("geral", "Geral"), ("aparencia", "Aparência"))),
            ("FINANÇAS", (("contas", "Contas"), ("carteiras", "Carteiras"), ("limites-aporte", "Limites de aporte"), ("controle-gastos", "Controle de gastos"))),
            ("DADOS", (("valores-mobiliarios", "Valores mobiliários"), ("classificacoes", "Classificações"), ("backup", "Backup e exportação"))),
            ("CONEXÕES", (("conexoes", "Wealthfolio Connect"), ("dados-mercado", "Dados de mercado"), ("ai-providers", "Provedores de IA"))),
            ("EXTENSÕES", (("extensoes", "Extensões"),)),
            ("SOBRE", (("sobre", "Sobre"),)),
        )
        allowed_sections = {key for _group, items in section_specs for key, _label in items}
        selected_section = request.GET.get("secao") or "geral"
        if selected_section not in allowed_sections:
            selected_section = "geral"

        def settings_url(section: str) -> str:
            query = request.GET.copy()
            query["secao"] = section
            return f"{reverse('consolidado:settings')}?{urlencode(query, doseq=True)}"

        page["settings_section"] = selected_section
        page["settings_nav_groups"] = tuple(
            {
                "label": group,
                "items": tuple(
                    {"key": key, "label": label, "url": settings_url(key), "active": key == selected_section}
                    for key, label in items
                ),
            }
            for group, items in section_specs
        )
        section_content = {
            "geral": ("Geral", "Gerencie as configurações e preferências gerais do aplicativo."),
            "aparencia": ("Aparência", "Preferências visuais informativas do shell Wealthfolio."),
            "contas": ("Contas", "Contas publicadas pelas fontes conectadas ao NetWorth."),
            "carteiras": ("Carteiras", "Carteiras e posições exibidas em modo somente leitura."),
            "limites-aporte": ("Limites de aporte", "Limites publicados pelas fontes de dados, sem edição no shell."),
            "controle-gastos": ("Controle de gastos", "Categorias e regras de gastos fornecidas pelo Controle Bancário."),
            "valores-mobiliarios": ("Valores mobiliários", "Instrumentos publicados pelo Controle de Renda Variável."),
            "classificacoes": ("Classificações", "Classificações e categorias recebidas das fontes de origem."),
            "backup": ("Backup e exportação", "Exportações pertencem aos sistemas de origem e permanecem bloqueadas aqui."),
            "conexoes": ("Wealthfolio Connect", "Estado das conexões de leitura utilizadas pelo shell."),
            "dados-mercado": ("Dados de mercado", "Cotações e referências de mercado publicadas pela fonte de renda variável."),
            "ai-providers": ("Provedores de IA", "Provedores de IA são informativos até existir um contrato de escrita."),
            "extensoes": ("Extensões", "Extensões disponíveis para o shell, sem instalação ou alteração nesta fase."),
            "sobre": ("Sobre", "Informações do NetWorth e da camada visual derivada do Wealthfolio."),
        }
        page["settings_section_title"], page["settings_section_description"] = section_content[selected_section]
    if kind == "holdings":
        holding_type = (request.GET.get("tipo") or "investimentos").strip().casefold()
        if holding_type not in {"investimentos", "ativos", "passivos"}:
            holding_type = "investimentos"
        page["holding_type"] = holding_type
        tab_urls = []
        for key, label in (("investimentos", "Investimentos"), ("ativos", "Ativos"), ("passivos", "Passivos")):
            query = request.GET.copy()
            if key == "investimentos":
                query.pop("tipo", None)
            else:
                query["tipo"] = key
            query_string = urlencode(query, doseq=True)
            tab_urls.append({"key": key, "label": label, "active": key == holding_type, "url": f"{request.path}?{query_string}" if query_string else request.path})
        page["holding_tabs"] = tuple(tab_urls)

        raw_rows = context["holdings"]
        # The v2 position aggregate is the authoritative investment view. For
        # the other Wealthfolio tabs only expose source rows whose published
        # role supports that view; never reinterpret an investment as a debt.
        if holding_type in {"ativos", "passivos"}:
            def line_value(line: Any, name: str, default: Any = "") -> Any:
                if isinstance(line, dict):
                    return line.get(name, default)
                return getattr(line, name, default)

            wanted = {"passivo", "liability", "liabilities"} if holding_type == "passivos" else None
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for line in context["consolidado"].linhas:
                role = str(line_value(line, "papel", "")).strip().casefold()
                is_liability = role in {"passivo", "liability", "liabilities"}
                if (wanted is not None) != is_liability:
                    continue
                name = str(line_value(line, "descricao", "—"))
                currency = str(line_value(line, "moeda", ""))
                bucket = grouped.setdefault((name, currency), {"descricao": name, "moeda": currency, "valor": Decimal("0"), "quantidade": Decimal("0"), "instituicoes": set(), "ganho": Decimal("0"), "ganho_informado": False})
                value = line_value(line, "valor", Decimal("0"))
                bucket["valor"] += value if isinstance(value, Decimal) else Decimal(str(value or 0))
                quantity = line_value(line, "quantidade", None)
                if quantity is not None:
                    bucket["quantidade"] += quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
                gain = line_value(line, "ganho_nao_realizado", None)
                if gain is not None:
                    bucket["ganho"] += gain if isinstance(gain, Decimal) else Decimal(str(gain))
                    bucket["ganho_informado"] = True
                institution = str(line_value(line, "instituicao", "") or "")
                if institution:
                    bucket["instituicoes"].add(institution)
            raw_rows = list(grouped.values())
            for row in raw_rows:
                row["ganho_nao_realizado"] = row.pop("ganho") if row.pop("ganho_informado") else None

        page["rows"] = [
            {
                "url": reverse("consolidado:holding_detail", kwargs={"holding_id": row["descricao"]}),
                "name": row["descricao"],
                "ticker": row["descricao"],
                "institution": ", ".join(row.get("instituicoes") or ()),
                "detail": ", ".join(sorted(row.get("instituicoes") or ())),
                "currency": row["moeda"],
                "quantity": row.get("quantidade"),
                "price": dinheiro(
                    row["valor"] / row["quantidade"], row["moeda"]
                ) if row.get("quantidade") else "Indisponível",
                "value": dinheiro(row.get("valor"), row["moeda"]),
                "weight": _percent_label(row.get("percentual")),
                "return": dinheiro(row.get("ganho_nao_realizado"), row["moeda"])
                if row.get("ganho_nao_realizado") is not None else "Indisponível",
                "return_percent": _percent_label(row.get("retorno_percentual"), signed=True),
            }
            for row in raw_rows
        ]
        search = (request.GET.get("busca") or "").strip().casefold()
        if search:
            page["rows"] = [
                row for row in page["rows"]
                if search in f"{row['name']} {row['detail']}".casefold()
            ]
        page["toolbar"]["search"] = request.GET.get("busca") or ""
        if not page["rows"]:
            page.update(state="empty", empty_title="Nenhuma posição publicada")
    elif kind == "holding-detail":
        row = context.get("holding")
        if row:
            page["item"] = {
                "name": row["descricao"],
                "ticker": row["descricao"],
                "currency": row["moeda"],
                "quantity": row.get("quantidade"),
                "value": dinheiro(row.get("valor"), row["moeda"]),
                "weight": _percent_label(row.get("percentual")),
                "source_label": ", ".join(row.get("instituicoes") or ()),
                "source_url": row.get("link") or "",
            }
        else:
            page["state"] = "empty"
        page["stats"] = ()
        page["sections"] = ()
    elif kind in {"accounts", "account-detail"}:
        accounts = context["consolidado"].por_instituicao()
        if kind == "accounts":
            page["rows"] = [
                {
                    "url": reverse("consolidado:account_detail", kwargs={"account_id": row["instituicao"]}),
                    "name": row["instituicao"],
                    "institution": row["instituicao"],
                    "type": "Conta publicada",
                    "currency": row["moeda"],
                    "value": dinheiro(row["total"], row["moeda"]),
                    "line_count": len(row.get("linhas") or ()),
                }
                for row in accounts
            ]
            if not page["rows"]:
                page["state"] = "empty"
        else:
            account = context.get("account")
            if account:
                totals = account.get("totais_por_moeda") or ()
                if account.get("total") is not None:
                    account_value = dinheiro(account["total"], account["moeda"])
                elif totals:
                    account_value = " · ".join(
                        dinheiro(item.get("total"), item.get("moeda"))
                        for item in totals
                    )
                else:
                    account_value = "Indisponível"
                page["account"] = {
                    "name": account.get("nome") or account["instituicao"],
                    "institution": account["instituicao"],
                    "type": "Conta publicada",
                    "currency": account.get("moeda") or "",
                    "value": account_value,
                }

                def field(line: Any, name: str, default: Any = "") -> Any:
                    if isinstance(line, dict):
                        return line.get(name, default)
                    return getattr(line, name, default)

                page["rows"] = [
                    {
                        "name": field(line, "descricao"),
                        "description": field(line, "descricao"),
                        "source": field(line, "fonte"),
                        "currency": field(line, "moeda"),
                        "value": dinheiro(field(line, "valor"), field(line, "moeda")),
                        "url": field(line, "link"),
                    }
                    for line in account.get("linhas") or ()
                ]
            else:
                page["state"] = "empty"
    elif kind == "activities":
        composition = context.get("activity_composition")
        published = tuple(composition.activities) if composition is not None else ()

        def activity_options(name: str, query_name: str, empty_label: str = "Todos") -> tuple[dict[str, Any], ...]:
            values = []
            for activity in published:
                value = str(getattr(activity, name, "") or "").strip()
                if not value and name == "category_kind":
                    value = str(getattr(activity, "kind", "") or "").strip()
                if value and value not in values:
                    values.append(value)
            selected_value = request.GET.get(query_name, "")
            options = [{"value": "", "label": empty_label, "active": not selected_value}]
            if values:
                options.extend({"value": value, "label": value, "active": selected_value == value} for value in values)
            else:
                options.append({"value": "__unavailable__", "label": "Indisponível", "active": selected_value == "__unavailable__"})
            return tuple(options)

        page["toolbar"]["filters"] = (
            {"label": "Status", "name": "status", "options": activity_options("status", "status")},
            {"label": "Tipo", "name": "natureza", "options": activity_options("category_kind", "natureza", "Todas")},
            {"label": "Conta", "name": "conta", "options": activity_options("account", "conta")},
            {"label": "Instrumento", "name": "instrumento", "options": activity_options("instrument", "instrumento")},
        )
        if composition is None:
            page.update(
                state="empty",
                empty_title="Atividades individuais não publicadas",
                empty_message="As fontes atuais ainda não publicaram atividades para esta consulta.",
            )
        elif composition.status == "error":
            # A cobertura do cabeçalho continua descrevendo a fotografia v1/v2;
            # a falha específica da extensão v3 fica explícita neste estado.
            page.update(
                state="error",
                error_title="Atividades indisponíveis",
                error_message="; ".join(composition.coverage.omissions) or "As fontes não responderam.",
                retry_url=request.get_full_path(),
            )
        else:
            page["meta"] = {
                "complete": composition.coverage.complete,
                "sources_responded": composition.coverage.responded_sources,
                "sources_expected": composition.coverage.expected_sources,
                "warning": "; ".join(composition.coverage.omissions),
            }
            if not composition.activities:
                page.update(
                    state="empty",
                    empty_title="Nenhuma atividade publicada",
                    empty_message="As fontes responderam, mas não há atividades para o período selecionado.",
                )
            else:
                fontes = {}
                for fonte in fontes_configuradas():
                    fontes[fonte.apelido.casefold()] = fonte
                    fontes[fonte.nome.casefold()] = fonte
                    fontes[
                        "controle-bancario"
                        if fonte.apelido.casefold() == "cb"
                        else "controle-renda-variavel"
                    ] = fonte
                rows = []
                busca = (request.GET.get("busca") or "").strip().casefold()
                selected = {
                    "status": request.GET.get("status") or "",
                    "natureza": request.GET.get("natureza") or "",
                    "conta": request.GET.get("conta") or "",
                    "instrumento": request.GET.get("instrumento") or "",
                }
                for activity in composition.activities:
                    if selected["status"] and selected["status"] != "__unavailable__" and activity.status != selected["status"]:
                        continue
                    activity_nature = activity.category_kind or activity.kind
                    if selected["natureza"] and selected["natureza"] != "__unavailable__" and activity_nature != selected["natureza"]:
                        continue
                    if selected["conta"] and selected["conta"] != "__unavailable__" and activity.account != selected["conta"]:
                        continue
                    if selected["instrumento"] and selected["instrumento"] != "__unavailable__" and activity.instrument != selected["instrumento"]:
                        continue
                    searchable = " ".join(
                        (
                            activity.description,
                            activity.institution,
                            activity.instrument,
                            activity.category,
                            activity.origin,
                            activity.source,
                        )
                    ).casefold()
                    if busca and busca not in searchable:
                        continue
                    value = activity.realized_value or activity.value
                    positive = value.amount >= 0
                    fonte = fontes.get(activity.source.casefold())
                    url = fonte.link(activity.link) if fonte and activity.link else activity.link
                    rows.append(
                        {
                            "url": url,
                            "date": activity.date.strftime("%d/%m/%Y"),
                            "description": activity.description,
                            "detail": " · ".join(
                                part
                                for part in (
                                    activity.institution,
                                    activity.instrument,
                                    activity.account,
                                )
                                if part
                            ),
                            "nature": activity.category_kind or activity.kind,
                            "status": activity.status,
                            "account": activity.account,
                            "instrument": activity.instrument,
                            "source": activity.source,
                            "currency": value.currency,
                            "inflow": dinheiro(
                                value.amount if positive else Decimal("0"), value.currency
                            ),
                            "outflow": dinheiro(
                                abs(value.amount) if not positive else Decimal("0"), value.currency
                            ),
                            "net": dinheiro(value.amount, value.currency),
                        }
                    )
                page["rows"] = rows
                if not rows:
                    page.update(
                        state="empty",
                        empty_title="Nenhuma atividade corresponde à busca",
                        empty_message="A fonte não publicou um registro correspondente aos filtros selecionados.",
                    )
    elif kind == "spending-insights":
        page["stages"] = [
            {"label": label, "url": _query_url("consolidado:spending_insights", stage=key, periodo=context["periodo"]), "active": request.GET.get("stage", "where") == key}
            for key, label in (("where", "Onde"), ("changed", "O que mudou"), ("when", "Quando"))
        ]
        metrics = []
        for row in context["resumo_gastos"]:
            metrics.extend(
                (
                    {"label": f"Receitas · {row['moeda']}", "value": dinheiro(row["receitas"], row["moeda"]), "tone": "positive"},
                    {"label": f"Gastos · {row['moeda']}", "value": dinheiro(row["gastos"], row["moeda"])},
                    {"label": f"Líquido · {row['moeda']}", "value": dinheiro(row["liquido"], row["moeda"])},
                )
            )
        page["metrics"] = metrics
        page["categories"] = ()
        page["report_url"] = ""
    else:
        page["unavailable_title"] = titles.get(kind, "Funcionalidade")
        page["unavailable_message"] = "Ainda não disponível nesta fase"
    return page


def _serialize_decimal(value: Any) -> str | None:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value) if value is not None else None


def _json_safe(value: Any) -> Any:
    """Serialize nested dashboard values without leaking Python/Decimal types."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _json_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    consolidated = context["consolidado"]
    return {
        "as_of": context["data_da_tela"].isoformat(),
        "base_currency": context["moeda_base"],
        "coverage": {
            "complete": consolidated.completo,
            "responded": len(consolidated.fontes_que_responderam),
            "expected": len(consolidated.leituras),
        },
        "totals": [
            {**total, "total": _serialize_decimal(total["total"])}
            for total in consolidated.totais_por_moeda
        ],
        "total_base": _serialize_decimal(context["total"]),
        "cash_base": _serialize_decimal(context["cash_total"]),
        "investments_base": _serialize_decimal(context["investments_total"]),
        "metrics": {
            "net_worth_change": _json_safe(context["patrimonio_variacao"]),
            "investments_change": _json_safe(context["investimentos_variacao"]),
            "cash_flow": _json_safe(context["resumo_gastos"]),
            "income": _json_safe(context["rendas_resumo"]),
            "realized_gains": _json_safe(context["ganhos_resumo"]),
        },
        "chart": _json_safe(context["grafico"]),
        "accounts": _json_safe(context["cartoes_de_contas"]),
        "holdings": [
            {
                key: _serialize_decimal(value) if isinstance(value, Decimal) else value
                for key, value in holding.items()
                if key not in {"instituicoes", "links"} or isinstance(value, list)
            }
            for holding in context["holdings"]
        ],
    }


@login_required
@require_GET
def dashboard_view(request: HttpRequest) -> HttpResponse:
    tab = request.GET.get("tab") or "investments"
    if tab not in {"investments", "net-worth", "spending"}:
        tab = "investments"
    context = _base_context(request, visao="patrimonio" if tab == "net-worth" else "gastos" if tab == "spending" else "investimentos")
    context.update(
        {
            "dashboard_tab": tab,
            "tab": tab,
            "wf_shell": _shell_vm(request, context, active="dashboard"),
            "wf_dashboard": _dashboard_vm(request, context, tab),
        }
    )
    return render(request, "consolidado/wealthfolio_dashboard_v2.html", context)


@login_required
@require_GET
def insights_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="insights")
    context["analytics"] = _analytics_context(context)
    context["insights_tab"] = request.GET.get("tab") or "summary"
    if context["insights_tab"] not in {"summary", "performance", "income"}:
        context["insights_tab"] = "summary"
    context["wf_shell"] = _shell_vm(request, context, active="insights")
    context["wf_insights"] = _insights_page_vm(request, context)
    context["allocations"] = ()
    return render(request, "consolidado/wealthfolio_insights_v2.html", context)


@login_required
@require_GET
def holdings_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="investimentos")
    context["holding_type"] = request.GET.get("tipo") or "investments"
    context["account_filter"] = request.GET.get("conta") or "all"
    context["page_type"] = "holdings"
    context["wf_shell"] = _shell_vm(request, context, active="holdings")
    context["wf_page"] = _page_vm(context, kind="holdings", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def holding_detail_view(request: HttpRequest, holding_id: str) -> HttpResponse:
    context = _base_context(request, visao="investimentos")
    context["holding"] = next(
        (item for item in context["holdings"] if str(item.get("descricao", "")) == holding_id),
        None,
    )
    context["holding_id"] = holding_id
    context["page_type"] = "holding-detail"
    context["wf_shell"] = _shell_vm(request, context, active="holdings")
    context["wf_page"] = _page_vm(context, kind="holding-detail", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def accounts_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="investimentos")
    context["accounts"] = context["consolidado"].por_instituicao()
    context["page_type"] = "accounts"
    context["wf_shell"] = _shell_vm(request, context, active="holdings")
    context["wf_page"] = _page_vm(context, kind="accounts", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def account_detail_view(request: HttpRequest, account_id: str) -> HttpResponse:
    context = _base_context(request, visao="investimentos")
    context["accounts"] = context["consolidado"].por_instituicao()
    context["account_id"] = account_id

    # A dashboard group is a first-class Wealthfolio drill-down (for example
    # ``Esposita``), while the legacy page historically resolved institutions
    # only (for example ``Mercado Pago``).  Resolve both surfaces against the
    # same published tree so a click never lands on a technically valid but
    # empty detail page.
    account = next(
        (item for item in context["accounts"] if item["instituicao"] == account_id),
        None,
    )
    if account is None:
        group = next(
            (item for item in context["arvore_drilldown"] if item["nome"] == account_id),
            None,
        )
        if group is not None:
            totals = group.get("totais_por_moeda") or ()
            group_lines = [
                line
                for child in group.get("contas") or ()
                for line in child.get("linhas") or ()
            ]
            if len(totals) == 1:
                currency = totals[0].get("moeda") or "BRL"
                total = totals[0].get("total")
            else:
                currency = ""
                total = None
            account = {
                "nome": group["nome"],
                "instituicao": group["nome"],
                "moeda": currency,
                "total": total,
                "totais_por_moeda": totals,
                "linhas": group_lines,
            }

    # A source can publish multiple accounts under one institution.  Preserve
    # the optional account label from the clicked URL instead of silently
    # displaying the institution aggregate again.
    account_filter = (request.GET.get("account") or request.GET.get("conta") or "").strip()
    if account and account_filter:
        needle = account_filter.casefold()

        def line_value(line: Any, name: str, default: Any = "") -> Any:
            if isinstance(line, dict):
                return line.get(name, default)
            return getattr(line, name, default)

        matching = [
            line
            for line in account.get("linhas") or ()
            if needle in str(line_value(line, "descricao")).strip().casefold()
        ]
        if matching:
            currencies = {str(line_value(line, "moeda")) for line in matching}
            if len(currencies) == 1:
                currency = next(iter(currencies))
                total = sum(
                    (line_value(line, "valor", Decimal("0")) for line in matching),
                    Decimal("0"),
                )
            else:
                currency = account.get("moeda") or "BRL"
                total = account.get("total")
            account = {
                **account,
                "nome": f"{account.get('nome') or account['instituicao']} · {account_filter}",
                "moeda": currency,
                "total": total,
                "totais_por_moeda": [
                    {"moeda": currency, "total": total}
                ] if len(currencies) == 1 else account.get("totais_por_moeda") or (),
                "linhas": matching,
            }
    context["account"] = account
    context["page_type"] = "account-detail"
    context["wf_shell"] = _shell_vm(request, context, active="holdings")
    context["wf_page"] = _page_vm(context, kind="account-detail", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def activities_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="gastos")
    context["activities"] = context["consolidado"].fluxos
    fontes = fontes_configuradas()
    if fontes:
        page = request.GET.get("page") or "1"
        page_size = request.GET.get("page_size") or "100"
        try:
            page_number = max(1, int(page))
        except (TypeError, ValueError):
            page_number = 1
        try:
            page_limit = min(500, max(1, int(page_size)))
        except (TypeError, ValueError):
            page_limit = 100
        inicio = legacy_views._inicio_do_periodo(context["periodo"], context["data_da_tela"])
        activity_filters = {
            key: (request.GET.get(key) or "").strip()
            for key in ("conta", "categoria", "status", "natureza", "instrumento")
        }
        context["activity_composition"] = compose_activities(
            fetch_activities(
                fonte,
                inicio=inicio,
                fim=context["data_da_tela"],
                page=page_number,
                page_size=page_limit,
                filters=activity_filters,
            )
            for fonte in fontes
        )
    context["page_type"] = "activities"
    context["wf_shell"] = _shell_vm(request, context, active="activities")
    context["wf_page"] = _page_vm(context, kind="activities", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def goals_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="patrimonio")
    context["page_type"] = "goals"
    context["wf_shell"] = _shell_vm(request, context, active="goals")
    context["wf_page"] = _page_vm(context, kind="goals", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def goal_new_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="patrimonio")
    context["page_type"] = "goal-new"
    context["wf_shell"] = _shell_vm(request, context, active="goals")
    context["wf_page"] = _page_vm(context, kind="goal-new", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def spending_insights_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="gastos")
    context["stage"] = request.GET.get("stage") or "where"
    context["page_type"] = "spending-insights"
    context["wf_shell"] = _shell_vm(request, context, active="spending")
    context["wf_page"] = _page_vm(context, kind="spending-insights", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def budget_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="gastos")
    context["month"] = request.GET.get("month") or context["data_da_tela"].strftime("%Y-%m")
    context["month_label"] = context["data_da_tela"].strftime("%B %Y")
    context["page_type"] = "budget"
    context["wf_shell"] = _shell_vm(request, context, active="spending")
    context["wf_page"] = _page_vm(context, kind="budget", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def assistant_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="patrimonio")
    context["page_type"] = "assistant"
    context["wf_shell"] = _shell_vm(request, context, active="assistant")
    context["wf_page"] = _page_vm(context, kind="assistant", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def settings_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="patrimonio")
    context["page_type"] = "settings"
    context["wf_shell"] = _shell_vm(request, context, active="settings")
    context["wf_page"] = _page_vm(context, kind="settings", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def dashboard_api(request: HttpRequest) -> JsonResponse:
    context = _base_context(request, visao="investimentos")
    payload = _json_snapshot(context)
    # The compatibility tree is the canonical display contract. Legacy keys
    # stay for existing clients during the additive migration.
    payload["view_model"] = _json_safe(_dashboard_vm(request, context, "investments"))
    return JsonResponse(payload)


@login_required
@require_GET
def insights_api(request: HttpRequest) -> JsonResponse:
    context = _base_context(request, visao="insights")
    context["insights_tab"] = request.GET.get("tab") or "summary"
    insight = context["insights"] or {}
    payload = {
        "as_of": context["data_da_tela"].isoformat(),
        "dimension": insight.get("dimensao"),
        "items": [
            {
                "id": item["id"],
                "name": item["nome"],
                "lines": item["linhas"],
                "percent": str(item["percentual"]) if item["percentual"] is not None else None,
                "totals": [
                    {**total, "total": _serialize_decimal(total["total"])}
                    for total in item["totais_por_moeda"]
                ],
            }
            for item in insight.get("itens", [])
        ],
    }
    payload["view_model"] = _json_safe(_insights_page_vm(request, context))
    return JsonResponse(payload)
