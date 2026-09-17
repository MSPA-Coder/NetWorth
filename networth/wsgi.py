"""Entrada WSGI do NetWorth."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "networth.settings")

application = get_wsgi_application()
