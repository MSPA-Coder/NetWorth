"""Adapters read-only de respostas de CB/CRV para o contrato canônico."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import Coverage, SnapshotDTO
from .normalize import SOURCE_ALIASES, CompatibilityError, normalize_payload
from .transport import TransportResponse


class SourceStatus:
    """Estados públicos, estáveis e serializáveis da leitura de uma fonte."""

    OK = "ok"
    PARTIAL = "partial"
    EMPTY = "empty"
    STALE = "stale"
    ERROR = "error"
    FX_MISSING = "fx_missing"


@dataclass(frozen=True, slots=True)
class SourceResult:
    """Resultado de uma leitura; ``snapshot`` nunca é inventado em erro."""

    source: str
    status: str
    snapshot: SnapshotDTO | None = None
    error: str = ""
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        canonical = SOURCE_ALIASES.get(self.source.casefold(), self.source)
        object.__setattr__(self, "source", canonical)
        if self.status not in {
            SourceStatus.OK,
            SourceStatus.PARTIAL,
            SourceStatus.EMPTY,
            SourceStatus.STALE,
            SourceStatus.ERROR,
            SourceStatus.FX_MISSING,
        }:
            raise ValueError("source result: estado desconhecido")
        if self.status == SourceStatus.ERROR and not self.error:
            object.__setattr__(self, "error", "A fonte não respondeu.")
        if self.status == SourceStatus.ERROR and self.snapshot is not None:
            raise ValueError("source result: erro não pode conter fotografia")

    @property
    def available(self) -> bool:
        return self.snapshot is not None and self.status != SourceStatus.ERROR

    @property
    def stale(self) -> bool:
        return self.status == SourceStatus.STALE

    @property
    def coverage(self) -> Coverage:
        if self.snapshot is not None:
            return self.snapshot.coverage
        return Coverage(
            complete=False,
            expected_sources=1,
            responded_sources=0,
            omissions=(self.error or "fonte sem resposta",),
            status=SourceStatus.ERROR,
            error=self.error or "A fonte não respondeu.",
        )


@dataclass(frozen=True, slots=True)
class CompositionResult:
    """Conjunto de fotografias sem conversão implícita entre moedas."""

    results: tuple[SourceResult, ...]
    coverage: Coverage

    @property
    def snapshots(self) -> tuple[SnapshotDTO, ...]:
        return tuple(result.snapshot for result in self.results if result.snapshot is not None)

    @property
    def currencies(self) -> tuple[str, ...]:
        return tuple(sorted({money.currency for snap in self.snapshots for money in snap.totals}))


def _source_name(expected_source: str | None, payload: Mapping[str, Any] | None) -> str:
    raw = payload.get("sistema") if payload else None
    value = raw or expected_source or "fonte-desconhecida"
    return SOURCE_ALIASES.get(str(value).casefold(), str(value))


def adapt_payload(payload: Mapping[str, Any], *, expected_source: str | None = None) -> SourceResult:
    """Normaliza uma resposta JSON sem deixar erro de fonte vazar para a tela."""
    source = _source_name(expected_source, payload if isinstance(payload, Mapping) else None)
    try:
        snapshot = normalize_payload(payload, expected_source=expected_source)
    except (CompatibilityError, TypeError, ValueError) as exc:
        return SourceResult(source, SourceStatus.ERROR, error=str(exc))
    return SourceResult(source, snapshot.status, snapshot=snapshot)


def adapt_response(
    response: TransportResponse,
    *,
    expected_source: str | None = None,
) -> SourceResult:
    """Converte status HTTP, vazio e payload em estado canônico."""
    source = _source_name(expected_source, response.payload)
    if not response.ok:
        reason = response.error or f"A fonte respondeu HTTP {response.status_code}."
        return SourceResult(source, SourceStatus.ERROR, error=reason, fetched_at=response.fetched_at)
    if response.payload is None:
        return SourceResult(source, SourceStatus.EMPTY, error="A fonte respondeu sem payload.", fetched_at=response.fetched_at)
    result = adapt_payload(response.payload, expected_source=expected_source)
    return SourceResult(result.source, result.status, result.snapshot, result.error, response.fetched_at)


def adapt_controle_bancario(payload: Mapping[str, Any]) -> SourceResult:
    return adapt_payload(payload, expected_source="controle-bancario")


def adapt_renda_variavel(payload: Mapping[str, Any]) -> SourceResult:
    return adapt_payload(payload, expected_source="controle-renda-variavel")


def compose_results(results: Iterable[SourceResult]) -> CompositionResult:
    """Compõe cobertura de fontes sem somar dinheiro de moedas diferentes."""
    normalized = tuple(results)
    expected = len(normalized)
    responded = sum(result.available for result in normalized)
    omissions: list[str] = []
    for result in normalized:
        if not result.available:
            omissions.append(f"{result.source}: {result.error or 'sem resposta'}")
        elif result.snapshot is not None:
            omissions.extend(f"{result.source}: {item}" for item in result.snapshot.omissions)
    status = SourceStatus.OK if expected and responded == expected and not omissions else SourceStatus.PARTIAL
    if not expected:
        status = SourceStatus.EMPTY
    if any(result.status == SourceStatus.STALE for result in normalized):
        status = SourceStatus.STALE
    if any(result.status == SourceStatus.FX_MISSING for result in normalized):
        status = SourceStatus.FX_MISSING
    # A composição com moedas diferentes nunca deve parecer convertida. Como
    # o contrato canônico não mantém uma tabela de câmbio local, sinalize a
    # lacuna até que uma taxa válida para ``as_of`` seja publicada.
    currencies = {money.currency for result in normalized if result.snapshot for money in result.snapshot.totals}
    if len(currencies) > 1 and status == SourceStatus.OK:
        status = SourceStatus.FX_MISSING
        omissions.append("taxa de câmbio ausente; moedas permanecem separadas")
    return CompositionResult(
        normalized,
        Coverage(status == SourceStatus.OK, expected, responded, tuple(dict.fromkeys(omissions)), status, stale=status == SourceStatus.STALE, fx_missing=status == SourceStatus.FX_MISSING),
    )


__all__ = [
    "CompositionResult",
    "SourceResult",
    "SourceStatus",
    "adapt_controle_bancario",
    "adapt_payload",
    "adapt_response",
    "adapt_renda_variavel",
    "compose_results",
]
