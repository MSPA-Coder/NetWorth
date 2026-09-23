"""DTOs imutáveis para os recursos v3 lidos direto das fontes (atividades e analíticos).

A foto patrimonial não passa por aqui: ela é lida e validada por
`consolidado.leitor`, que produz o `Consolidado`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
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
