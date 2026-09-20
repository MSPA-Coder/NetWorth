"""Views for the Wealthfolio-compatible surface.

The original ``/patrimonio/`` screen remains the conservative, source-oriented
view.  These views provide the richer navigation model used by Wealthfolio
without copying data from Controle Bancário or Controle de Renda Variável.  All
financial values are assembled from the same ``Consolidado`` object and keep
the currency/coverage warnings that are part of NetWorth's contract.
"""

from __future__ import annotations

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
from consolidado.templatetags.dinheiro import dinheiro
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


def _query_url(route: str, **params: Any) -> str:
    filtered = {key: value for key, value in params.items() if value not in (None, "")}
    target = reverse(route)
    return f"{target}?{urlencode(filtered)}" if filtered else target


def _shell_vm(request: HttpRequest, context: dict[str, Any], *, active: str) -> dict[str, Any]:
    vm = build_view_models(context, user_label=request.user.get_username(), active_area=active)
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
    all_vm = build_view_models(context, user_label=request.user.get_username(), active_area="dashboard")
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
                        # O deep link publicado pela fonte pode usar o hostname
                        # interno do Compose. No shell, o drill-down GET local
                        # é o destino seguro e reproduzível.
                        "url": child.get("url") or "#",
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
    all_vm = build_view_models(context, user_label=request.user.get_username(), active_area=kind)
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
    if kind == "holdings":
        page["rows"] = [
            {
                "url": reverse("consolidado:holding_detail", kwargs={"holding_id": row["descricao"]}),
                "name": row["descricao"],
                "ticker": row["descricao"],
                "institution": ", ".join(row.get("instituicoes") or ()),
                "currency": row["moeda"],
                "quantity": row.get("quantidade"),
                "value": dinheiro(row.get("valor"), row["moeda"]),
                "weight": _percent_label(row.get("percentual")),
            }
            for row in context["holdings"]
        ]
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
                page["account"] = {
                    "name": account["instituicao"],
                    "institution": account["instituicao"],
                    "type": "Conta publicada",
                    "currency": account["moeda"],
                    "value": dinheiro(account["total"], account["moeda"]),
                }
                page["rows"] = [
                    {
                        "name": line.descricao,
                        "description": line.descricao,
                        "source": line.fonte,
                        "currency": line.moeda,
                        "value": dinheiro(line.valor, line.moeda),
                        "url": line.link,
                    }
                    for line in account.get("linhas") or ()
                ]
            else:
                page["state"] = "empty"
    elif kind == "activities":
        page.update(
            state="empty",
            empty_title="Atividades individuais não publicadas",
            empty_message="As fontes atuais publicam fluxos agregados, não lançamentos individuais.",
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
    context["insights_tab"] = request.GET.get("tab") or "summary"
    if context["insights_tab"] not in {"summary", "performance", "income"}:
        context["insights_tab"] = "summary"
    context["wf_shell"] = _shell_vm(request, context, active="insights")
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
    context["account"] = next(
        (item for item in context["accounts"] if item["instituicao"] == account_id), None
    )
    context["page_type"] = "account-detail"
    context["wf_shell"] = _shell_vm(request, context, active="holdings")
    context["wf_page"] = _page_vm(context, kind="account-detail", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def activities_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="gastos")
    context["activities"] = context["consolidado"].fluxos
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
    context["wf_shell"] = _shell_vm(request, context, active="dashboard")
    context["wf_page"] = _page_vm(context, kind="spending-insights", request=request)
    return render(request, "consolidado/wealthfolio_page_v2.html", context)


@login_required
@require_GET
def budget_view(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, visao="gastos")
    context["month"] = request.GET.get("month") or context["data_da_tela"].strftime("%Y-%m")
    context["page_type"] = "budget"
    context["wf_shell"] = _shell_vm(request, context, active="dashboard")
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
    return JsonResponse(_json_snapshot(_base_context(request, visao="investimentos")))


@login_required
@require_GET
def insights_api(request: HttpRequest) -> JsonResponse:
    context = _base_context(request, visao="insights")
    insight = context["insights"] or {}
    return JsonResponse(
        {
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
    )
