"""Transporte HTTP somente leitura para os contratos das fontes.

O módulo não abre sockets por conta própria. A aplicação injeta uma função ou
cliente que implemente ``get`` e devolve ``TransportResponse``. Isso mantém
segredos, timeout e autenticação no processo do NetWorth e torna o adapter
determinístico nos testes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class TransportError(RuntimeError):
    """Falha de transporte, sem confundir com payload inválido."""


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Resposta já lida pelo cliente injetado, sem guardar credenciais."""

    status_code: int
    payload: Mapping[str, Any] | None = None
    fetched_at: datetime | None = None
    error: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error

    @property
    def stale(self) -> bool:
        return self.status_code == 200 and bool(self.payload and self.payload.get("stale"))

    def __post_init__(self) -> None:
        if self.status_code < 100 or self.status_code > 599:
            raise ValueError("transport response: status HTTP inválido")
        if self.payload is not None and not isinstance(self.payload, Mapping):
            raise ValueError("transport response: payload precisa ser objeto")
        object.__setattr__(self, "headers", dict(self.headers))


class ReadOnlyTransport(Protocol):
    """Interface mínima; deliberadamente só expõe GET."""

    def get(self, path: str, *, timeout: float, headers: Mapping[str, str] | None = None) -> TransportResponse:
        ...


def response_from_payload(
    payload: Mapping[str, Any],
    *,
    status_code: int = 200,
    fetched_at: datetime | None = None,
) -> TransportResponse:
    """Cria resposta para fixtures e para adapters sem acoplar HTTP ao domínio."""
    return TransportResponse(status_code, payload, fetched_at)


__all__ = ["ReadOnlyTransport", "TransportError", "TransportResponse", "response_from_payload"]
