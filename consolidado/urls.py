"""Rotas do consolidado."""

from django.urls import path

from . import views

app_name = "consolidado"

urlpatterns = [
    path("patrimonio/", views.patrimonio_view, name="patrimonio"),
]
