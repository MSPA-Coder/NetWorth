"""Rotas do projeto: login, saúde e o consolidado."""

from django.contrib.auth import views as auth_views
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView


def health_check(_request):
    """Mesmo contrato de saúde dos aplicativos irmãos.

    503 e não 500 quando o banco não responde: indisponibilidade temporária é a
    resposta correta, e não produz traceback no log a cada sonda.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - qualquer falha e "nao apto"
        return JsonResponse({"servico": "networth", "status": "erro"}, status=503)
    return JsonResponse({"servico": "networth", "status": "ok"})


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="consolidado:patrimonio", permanent=False)),
    path("health/", health_check, name="health_check"),
    # Sem barra também: os irmãos Flask servem `/health`, e um vigia externo que
    # não segue redirecionamento marcaria este serviço como fora do ar.
    path("health", health_check),
    path("login", auth_views.LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("consolidado.urls")),
]
