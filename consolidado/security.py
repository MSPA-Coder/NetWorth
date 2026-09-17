"""Cabeçalhos defensivos e CSP, iguais aos dos aplicativos irmãos.

Os VALORES vêm de `sharedauth.security`; quem os APLICA é este middleware. A
divisão é do próprio pacote: o núcleo dele é Python puro e não pode depender de
Django sem arrastar o framework para dentro da biblioteca.

A política é fechada e **não há nonce**, porque não há nada embutido: todo
estilo e todo script deste aplicativo são arquivos servidos pelo WhiteNoise.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from sharedauth.security import SECURITY_HEADERS, montar_csp

CONTENT_SECURITY_POLICY = montar_csp()


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        for header, value in SECURITY_HEADERS.items():
            response.setdefault(header, value)
        return response
