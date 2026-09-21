"""Nomes canônicos do contrato usado pelo shell Wealthfolio.

Os publicadores continuam livres para evoluir os envelopes ``patrimonio/v1``
e ``patrimonio/v2``. Este módulo é a pequena fronteira estável que o restante
do NetWorth pode importar sem conhecer o Controle Bancário ou o CRV. Os nomes
sem o sufixo ``DTO`` são aliases intencionais dos DTOs imutáveis existentes;
isso evita criar uma segunda representação dos dados de origem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import (
    AccountDTO,
    ActivityDTO,
    Capabilities,
    Coverage,
    FlowDTO,
    GainDTO,
    IncomeDTO,
    Money,
    PerformanceDTO,
    PositionDTO,
    SnapshotDTO,
)


@dataclass(frozen=True, slots=True)
class DeepLink:
    """Link interno publicado pela fonte para uma tela de origem.

    Links externos e URLs absolutas são deliberadamente rejeitados. A fonte
    só pode apontar para uma rota local dela mesma; o adapter já descarta
    endereços inseguros antes de construir o DTO.
    """

    source: str
    path: str

    def __post_init__(self) -> None:
        source = self.source.strip()
        path = self.path.strip()
        if not source:
            raise ValueError("deep link: fonte obrigatória")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("deep link: esperado caminho local")
        if "\\" in path or "://" in path or any(ord(char) < 32 for char in path):
            raise ValueError("deep link: caminho inválido")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "path", path)

    @property
    def href(self) -> str:
        return self.path

    def to_wire(self) -> dict[str, str]:
        return {"source": self.source, "path": self.path}


@dataclass(frozen=True, slots=True)
class PerformancePoint:
    """Ponto de uma série de retorno, sem conversão implícita de moedas."""

    day: date
    value: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.day, date):
            raise ValueError("performance point: data obrigatória")
        try:
            value = self.value if isinstance(self.value, Decimal) else Decimal(str(self.value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("performance point: valor inválido") from exc
        if not value.is_finite():
            raise ValueError("performance point: valor não finito")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("performance point: moeda inválida")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "currency", currency)

    def to_wire(self) -> dict[str, str]:
        return {
            "data": self.day.isoformat(),
            "valor": format(self.value, "f"),
            "moeda": self.currency,
        }


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """Série de gráfico canônica, com estado explícito quando indisponível."""

    key: str
    label: str
    points: tuple[PerformancePoint, ...] = ()
    state: str = "ok"
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("chart series: chave e rótulo obrigatórios")
        if self.state not in {"ok", "empty", "partial", "stale", "error", "unavailable"}:
            raise ValueError("chart series: estado desconhecido")
        object.__setattr__(self, "points", tuple(self.points))


# Public API names. Aliases preserve identidade e compatibilidade com os
# módulos que ainda importam os nomes históricos enquanto a migração acontece.
type SourceSnapshot = SnapshotDTO
type Account = AccountDTO
type Holding = PositionDTO
type Activity = FlowDTO
type IndividualActivity = ActivityDTO
type RealizedGain = GainDTO
type CapabilitySet = Capabilities


__all__ = [
    "Account",
    "AccountDTO",
    "Activity",
    "ActivityDTO",
    "IndividualActivity",
    "Capabilities",
    "CapabilitySet",
    "ChartSeries",
    "Coverage",
    "DeepLink",
    "FlowDTO",
    "GainDTO",
    "Holding",
    "IncomeDTO",
    "Money",
    "PerformanceDTO",
    "PerformancePoint",
    "PositionDTO",
    "RealizedGain",
    "SnapshotDTO",
    "SourceSnapshot",
]
