"""Rotas do consolidado."""

from django.urls import path

from . import views, wealthfolio_views

app_name = "consolidado"

urlpatterns = [
    path("patrimonio/", views.patrimonio_view, name="patrimonio"),
    path("patrimonio/historico/", views.historico_view, name="historico"),
    # Wealthfolio-compatible navigation.  The legacy URLs above remain stable
    # for bookmarks and integrations.
    path("dashboard/", wealthfolio_views.dashboard_view, name="dashboard"),
    path("insights/", wealthfolio_views.insights_view, name="insights"),
    path("holdings/", wealthfolio_views.holdings_view, name="holdings"),
    path("holdings/<path:holding_id>/", wealthfolio_views.holding_detail_view, name="holding_detail"),
    path("accounts/", wealthfolio_views.accounts_view, name="accounts"),
    path("accounts/<path:account_id>/", wealthfolio_views.account_detail_view, name="account_detail"),
    path("activities/", wealthfolio_views.activities_view, name="activities"),
    path("goals/", wealthfolio_views.goals_view, name="goals"),
    path("goals/new/", wealthfolio_views.goal_new_view, name="goal_new"),
    path("spending/insights/", wealthfolio_views.spending_insights_view, name="spending_insights"),
    path("spending/budget/", wealthfolio_views.budget_view, name="budget"),
    path("assistant/", wealthfolio_views.assistant_view, name="assistant"),
    path("settings/", wealthfolio_views.settings_view, name="settings"),
    path("api/wealthfolio/dashboard/", wealthfolio_views.dashboard_api, name="dashboard_api"),
    path("api/wealthfolio/insights/", wealthfolio_views.insights_api, name="insights_api"),
]
