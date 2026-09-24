"""Configuração do aplicativo do consolidado."""

from django.apps import AppConfig


class ConsolidadoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "consolidado"
    verbose_name = "Consolidado"

    def ready(self):
        from django.db.backends.signals import connection_created

        from consolidado.papel_do_banco import conferir_papel

        connection_created.connect(conferir_papel, dispatch_uid="consolidado.conferir_papel")
