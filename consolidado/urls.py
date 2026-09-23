"""Rotas do consolidado."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import path, reverse

from . import projecao_views, wealthfolio_views

app_name = "consolidado"


def _wealthfolio_url(
    request,
    *,
    tab: str,
    route: str = "dashboard",
    force_period: str | None = None,
) -> str:
    """Translate a legacy bookmark into the single Wealthfolio surface."""

    query = request.GET.copy()
    query.pop("visao", None)
    query["tab"] = tab
    if force_period is not None:
        query["periodo"] = force_period
    target = reverse(f"consolidado:{route}")
    encoded = query.urlencode()
    return f"{target}?{encoded}" if encoded else target


def legacy_patrimonio_redirect(request):
    visao = request.GET.get("visao", "patrimonio")
    if visao == "insights":
        return redirect(_wealthfolio_url(request, route="insights", tab="summary"))

    visao_to_tab = {
        "investimentos": "investments",
        "patrimonio": "net-worth",
        "gastos": "spending",
    }
    tab = visao_to_tab.get(visao, "net-worth")
    return redirect(_wealthfolio_url(request, tab=tab))


def legacy_historico_redirect(request):
    return redirect(_wealthfolio_url(request, tab="net-worth", force_period="tudo"))


urlpatterns = [
    # The Wealthfolio shell is the single public interface.  These legacy
    # routes remain as bookmark-compatible redirects and must never render the
    # retired NetWorth templates.
    path("patrimonio/", login_required(legacy_patrimonio_redirect), name="patrimonio"),
    path("patrimonio/historico/", login_required(legacy_historico_redirect), name="historico"),
    # Wealthfolio-compatible navigation.  The legacy URLs above remain stable
    # for bookmarks and integrations.
    path("dashboard/", wealthfolio_views.dashboard_view, name="dashboard"),
    path("insights/", wealthfolio_views.insights_view, name="insights"),
    path("holdings/", wealthfolio_views.holdings_view, name="holdings"),
    path("holdings/<path:holding_id>/", wealthfolio_views.holding_detail_view, name="holding_detail"),
    path("accounts/", wealthfolio_views.accounts_view, name="accounts"),
    path("accounts/<path:account_id>/", wealthfolio_views.account_detail_view, name="account_detail"),
    path("activities/", wealthfolio_views.activities_view, name="activities"),
    path("projecao/", projecao_views.projecao_view, name="projecao"),
    path("goals/", wealthfolio_views.goals_view, name="goals"),
    path("goals/new/", wealthfolio_views.goal_new_view, name="goal_new"),
    path("spending/insights/", wealthfolio_views.spending_insights_view, name="spending_insights"),
    path("spending/budget/", wealthfolio_views.budget_view, name="budget"),
    path("assistant/", wealthfolio_views.assistant_view, name="assistant"),
    path("settings/", wealthfolio_views.settings_view, name="settings"),
    path("api/wealthfolio/dashboard/", wealthfolio_views.dashboard_api, name="dashboard_api"),
    path("api/wealthfolio/insights/", wealthfolio_views.insights_api, name="insights_api"),
]
