"""Configuração do aplicativo do consolidado."""

from django.apps import AppConfig


class ConsolidadoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "consolidado"
    verbose_name = "Consolidado"
