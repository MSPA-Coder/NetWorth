"""DTOs imutáveis e seguros para dados publicados pelas fontes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any


class DTOError(ValueError):
    """Payload não pode ser representado pelo contrato interno."""


def _decimal(value: Decimal | str | int, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise DTOError(f"{field_name}: booleano não é valor monetário")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DTOError(f"{field_name}: decimal inválido") from exc
    if not result.is_finite():
        raise DTOError(f"{field_name}: decimal não finito")
    return result


def _text(value: Any, *, field_name: str, required: bool = True) -> str:
    if not isinstance(value, str):
        if required:
            raise DTOError(f"{field_name}: texto obrigatório")
        return ""
    result = value.strip()
    if required and not result:
        raise DTOError(f"{field_name}: texto vazio")
    return result


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _decimal(self.amount, field_name="amount"))
        currency = _text(self.currency, field_name="currency").upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise DTOError("currency: esperado código ISO de três letras")
        object.__setattr__(self, "currency", currency)

    @classmethod
    def from_wire(cls, amount: Any, currency: Any, *, field_name: str = "amount") -> Money:
        return cls(_decimal(amount, field_name=field_name), _text(currency, field_name="currency"))

    def to_wire(self) -> dict[str, str]:
        return {"amount": format(self.amount, "f"), "currency": self.currency}


@dataclass(frozen=True, slots=True)
class Coverage:
    complete: bool
    expected_sources: int
    responded_sources: int
    omissions: tuple[str, ...] = ()
    status: str = "ok"
    stale: bool = False
    fx_missing: bool = False
    error: str = ""

    def __post_init__(self) -> None:
        if self.expected_sources < 0 or self.responded_sources < 0:
            raise DTOError("coverage: contagens não podem ser negativas")
        if self.responded_sources > self.expected_sources:
            raise DTOError("coverage: fontes respondidas excedem fontes esperadas")
        if self.complete and (self.omissions or self.responded_sources != self.expected_sources):
            raise DTOError("coverage: completa não pode conter lacunas")
        if self.status not in {"ok", "partial", "empty", "stale", "error", "fx_missing"}:
            raise DTOError("coverage: estado desconhecido")
        if self.complete and self.status != "ok":
            raise DTOError("coverage: fotografia completa precisa estar em estado ok")
        if self.error and self.status != "error":
            raise DTOError("coverage: erro só pode existir no estado error")
        if self.stale and self.status not in {"stale", "partial"}:
            raise DTOError("coverage: stale requer estado stale ou partial")

    @property
    def available(self) -> bool:
        """Indica que a fonte respondeu, ainda que com dados incompletos."""
        return self.responded_sources > 0 and self.status != "error"

    @property
    def empty(self) -> bool:
        return self.status == "empty"


@dataclass(frozen=True, slots=True)
class Capabilities:
    source: str = ""
    contract: str = ""
    accounts: bool = False
    positions: bool = False
    flows: bool = False
    performance: bool = False
    income: bool = False
    realized_gains: bool = False
    quality: bool = False
    individual_activities: bool = False


@dataclass(frozen=True, slots=True)
class AccountDTO:
    id: str
    owner: str
    institution: str
    name: str
    balance: Money
    source: str
    link: str = ""


@dataclass(frozen=True, slots=True)
class PositionDTO:
    id: str
    owner: str
    institution: str
    instrument: str
    value: Money
    source: str
    asset_class: str = ""
    market: str = ""
    quantity: Decimal | None = None
    price: Decimal | None = None
    cost: Money | None = None
    unrealized_gain: Money | None = None
    link: str = ""
    price_quality: str = ""


@dataclass(frozen=True, slots=True)
class FlowDTO:
    date: date
    value: Money
    inflow: Money
    outflow: Money
    nature: str
    source: str
    lines: int = 0
    link: str = ""


@dataclass(frozen=True, slots=True)
class ActivityDTO:
    """Lançamento individual publicado por uma fonte v3.

    A atividade é uma projeção imutável da origem. O shell não tenta inferir
    categorias, dividir valores ou transformar uma atividade em lançamento
    contábil local; campos opcionais permanecem vazios quando a fonte não os
    publicou.
    """

    id: str
    date: date
    description: str
    kind: str
    status: str
    value: Money
    source: str
    realized_value: Money | None = None
    institution: str = ""
    instrument: str = ""
    owner: str = ""
    category: str = ""
    category_kind: str = ""
    account: str = ""
    origin: str = ""
    link: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise DTOError("activity: id obrigatório")
        if not isinstance(self.date, date):
            raise DTOError("activity: data obrigatória")
        for field_name in ("description", "kind", "status", "source"):
            _text(getattr(self, field_name), field_name=field_name)
        if self.realized_value is not None and self.realized_value.currency != self.value.currency:
            raise DTOError("activity: moedas divergentes")


@dataclass(frozen=True, slots=True)
class IncomeDTO:
    value: Money
    source: str
    by_type: tuple[tuple[str, Money], ...] = ()
    link: str = ""


@dataclass(frozen=True, slots=True)
class GainDTO:
    value: Money
    source: str
    instrument: str = ""
    transactions: int = 0
    link: str = ""


@dataclass(frozen=True, slots=True)
class PerformanceDTO:
    currency: str
    method: str
    source: str
    points: tuple[tuple[date, Decimal], ...] = ()
    link: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotDTO:
    source: str
    contract: str
    as_of: date
    generated_at: str | None
    totals: tuple[Money, ...]
    accounts: tuple[AccountDTO, ...]
    positions: tuple[PositionDTO, ...]
    flows: tuple[FlowDTO, ...]
    performance: tuple[PerformanceDTO, ...]
    income: tuple[IncomeDTO, ...]
    realized_gains: tuple[GainDTO, ...]
    coverage: Coverage
    capabilities: Capabilities
    omissions: tuple[str, ...] = ()
    quality: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "quality", dict(self.quality))

    @property
    def complete(self) -> bool:
        return self.coverage.complete and not self.omissions

    @property
    def status(self) -> str:
        """Estado normalizado da fotografia, útil para templates e APIs."""
        return self.coverage.status

    @property
    def currencies(self) -> tuple[str, ...]:
        return tuple(sorted({money.currency for money in self.totals}))

    def to_dict(self) -> dict[str, Any]:
        """Serializa sem floats, objetos mutáveis ou referências ao payload."""
        return {
            "source": self.source,
            "contract": self.contract,
            "as_of": self.as_of.isoformat(),
            "generated_at": self.generated_at,
            "totals": [item.to_wire() for item in self.totals],
            "accounts": [_asdict(item) for item in self.accounts],
            "positions": [_asdict(item) for item in self.positions],
            "flows": [_asdict(item) for item in self.flows],
            "performance": [_asdict(item) for item in self.performance],
            "income": [_asdict(item) for item in self.income],
            "realized_gains": [_asdict(item) for item in self.realized_gains],
            "coverage": _asdict(self.coverage),
            "capabilities": _asdict(self.capabilities),
            "omissions": list(self.omissions),
            "quality": _safe_json(self.quality),
        }


def _asdict(value: Any) -> Any:
    if isinstance(value, Money):
        return value.to_wire()
    if hasattr(value, "__dataclass_fields__"):
        return {name: _asdict(getattr(value, name)) for name in value.__dataclass_fields__}
    return _safe_json(value)


def _safe_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, float):
        if not isfinite(value):
            raise DTOError("serialização: float não finito")
        return format(value, ".15g")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise DTOError(f"serialização: tipo não suportado {type(value).__name__}")
