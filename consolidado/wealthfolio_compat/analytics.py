"""Leitura dos recursos analíticos v3 publicados pelas fontes.

O módulo é deliberadamente pequeno e somente leitura. Ele não calcula renda,
performance ou eventos no NetWorth: apenas valida e projeta os fatos já
publicados pelo CRV (ou a ausência explícita de capacidade do CB).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from urllib.parse import urlencode

from consolidado.fontes import Fonte

from .models import DTOError, Money, _decimal, _text
from .transport import ReadOnlyTransport, TransportResponse

LOGGER = logging.getLogger(__name__)
CONTRACT = "patrimonio/v3"
TIMEOUT_SECONDS = 8
MAX_BYTES = 8 * 1024 * 1024
PAGE_SIZE_DEFAULT = 100
PAGE_SIZE_MAX = 500
#: O maior tamanho de página que CB e CRV aceitam no contrato v3.
PAGE_SIZE_SOURCE = 100
MAX_PAGES = 50

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_EMPTY = "empty"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"
STATUS_STALE = "stale"

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IncomeRecord:
    id: str
    date: date
    description: str
    value: Money
    source: str
    kind: str = ""
    instrument: str = ""
    institution: str = ""
    category: str = ""
    link: str = ""


@dataclass(frozen=True, slots=True)
class PerformanceRecord:
    id: str
    currency: str
    method: str
    source: str
    start: date | None = None
    end: date | None = None
    points: tuple[tuple[date, Decimal], ...] = ()
    link: str = ""


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: str
    date: date
    kind: str
    instrument: str
    currency: str
    source: str
    quantity: Decimal | None = None
    link: str = ""


@dataclass(frozen=True, slots=True)
class AnalyticsSourceResult[T]:
    source: str
    resource: str
    status: str
    items: tuple[T, ...] = ()
    total: int = 0
    error: str = ""
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            STATUS_OK,
            STATUS_PARTIAL,
            STATUS_EMPTY,
            STATUS_UNSUPPORTED,
            STATUS_ERROR,
            STATUS_STALE,
        }:
            raise ValueError("analytics source: estado desconhecido")
        if self.total < 0:
            raise ValueError("analytics source: total inválido")
        if self.status in {STATUS_ERROR, STATUS_UNSUPPORTED} and self.items:
            raise ValueError("analytics source: estado sem dados não pode conter itens")


@dataclass(frozen=True, slots=True)
class AnalyticsComposition[T]:
    resource: str
    results: tuple[AnalyticsSourceResult[T], ...]
    items: tuple[T, ...]
    status: str
    warnings: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.items)


def _source_name(raw: Any, expected: str | None = None) -> str:
    aliases = {
        "cb": "controle-bancario",
        "controle_bancario": "controle-bancario",
        "controle-bancario": "controle-bancario",
        "crv": "controle-renda-variavel",
        "controle_renda_variavel": "controle-renda-variavel",
        "controle-renda-variavel": "controle-renda-variavel",
    }
    value = str(raw or expected or "fonte-desconhecida").strip()
    return aliases.get(value.casefold(), value)


def _link(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "://" in value:
        return ""
    if any(ord(char) < 32 for char in value):
        return ""
    return value


def _date(raw: Any, field: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        raise DTOError(f"{field}: data ISO obrigatória")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DTOError(f"{field}: data inválida") from exc


def _money(item: Mapping[str, Any], index: int) -> Money:
    currency = _text(item.get("moeda", item.get("currency")), field_name=f"itens[{index}].moeda").upper()
    raw = item.get("valor", item.get("value"))
    if raw is None:
        raw = item.get("valor_realizado", item.get("amount"))
    return Money(_decimal(raw, field_name=f"itens[{index}].valor"), currency)


def _income(source: str, item: Mapping[str, Any], index: int) -> IncomeRecord:
    if not isinstance(item, Mapping):
        raise DTOError(f"itens[{index}]: esperado objeto")
    category = item.get("categoria")
    category_name = category.get("nome") if isinstance(category, Mapping) else ""
    return IncomeRecord(
        id=_text(item.get("id"), field_name=f"itens[{index}].id"),
        date=_date(item.get("data"), f"itens[{index}].data"),
        description=_text(item.get("descricao"), field_name=f"itens[{index}].descricao"),
        value=_money(item, index),
        source=source,
        kind=str(item.get("tipo") or "renda"),
        instrument=str(item.get("instrumento") or ""),
        institution=str(item.get("instituicao") or ""),
        category=str(category_name or item.get("categoria_nome") or ""),
        link=_link(item.get("deep_link")),
    )


def _performance(source: str, item: Mapping[str, Any], index: int) -> PerformanceRecord:
    if not isinstance(item, Mapping):
        raise DTOError(f"itens[{index}]: esperado objeto")
    points = []
    for point_index, point in enumerate(item.get("pontos") or ()):
        if not isinstance(point, Mapping):
            raise DTOError(f"itens[{index}].pontos[{point_index}]: esperado objeto")
        raw = point.get("retorno_acumulado", point.get("indice_twr"))
        if raw is None:
            continue
        points.append(
            (
                _date(point.get("data"), f"itens[{index}].pontos[{point_index}].data"),
                _decimal(raw, field_name=f"itens[{index}].pontos[{point_index}].retorno"),
            )
        )
    start = _date(item["inicio"], f"itens[{index}].inicio") if item.get("inicio") else None
    end = _date(item["fim"], f"itens[{index}].fim") if item.get("fim") else None
    currency = _text(item.get("moeda"), field_name=f"itens[{index}].moeda").upper()
    return PerformanceRecord(
        id=_text(item.get("id"), field_name=f"itens[{index}].id"),
        currency=currency,
        method=str(item.get("metodo") or "TWR"),
        source=source,
        start=start,
        end=end,
        points=tuple(points),
        link=_link(item.get("deep_link", item.get("endereco"))),
    )


def _event(source: str, item: Mapping[str, Any], index: int) -> EventRecord:
    if not isinstance(item, Mapping):
        raise DTOError(f"itens[{index}]: esperado objeto")
    currency = _text(item.get("moeda"), field_name=f"itens[{index}].moeda").upper()
    raw_quantity = item.get("quantidade_resultante", item.get("quantidade"))
    quantity = _decimal(raw_quantity, field_name=f"itens[{index}].quantidade") if raw_quantity is not None else None
    return EventRecord(
        id=_text(item.get("id"), field_name=f"itens[{index}].id"),
        date=_date(item.get("data"), f"itens[{index}].data"),
        kind=str(item.get("tipo") or "evento"),
        instrument=str(item.get("instrumento") or ""),
        currency=currency,
        source=source,
        quantity=quantity,
        link=_link(item.get("deep_link", item.get("endereco"))),
    )


def normalize_analytics_payload(
    payload: Mapping[str, Any],
    *,
    resource: str,
    expected_source: str | None = None,
    fetched_at: datetime | None = None,
) -> AnalyticsSourceResult[Any]:
    if not isinstance(payload, Mapping):
        raise DTOError("envelope analítico: esperado objeto JSON")
    if payload.get("contrato") != CONTRACT:
        raise DTOError(f"contrato analítico desconhecido: {payload.get('contrato')!r}")
    if payload.get("recurso") not in {None, resource}:
        raise DTOError("recurso analítico desconhecido")
    source = _source_name(payload.get("sistema"), expected_source)
    raw_items = payload.get("itens", [])
    if not isinstance(raw_items, list):
        raise DTOError("itens analíticos: esperado array")
    parser = {"income": _income, "performance": _performance, "events": _event}.get(resource)
    if parser is None:
        raise DTOError(f"recurso analítico não suportado: {resource}")
    items = tuple(parser(source, item, index) for index, item in enumerate(raw_items))
    pagination = payload.get("paginacao")
    total = pagination.get("total", len(items)) if isinstance(pagination, Mapping) else len(items)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise DTOError("paginacao.total: valor inválido")
    state = str(payload.get("estado", payload.get("status", STATUS_OK))).casefold().strip()
    state = {"sucesso": STATUS_OK, "parcial": STATUS_PARTIAL, "vazio": STATUS_EMPTY}.get(state, state)
    if state not in {STATUS_OK, STATUS_PARTIAL, STATUS_EMPTY, STATUS_STALE}:
        raise DTOError(f"estado analítico desconhecido: {state!r}")
    if not items and state == STATUS_OK:
        state = STATUS_EMPTY
    return AnalyticsSourceResult(source, resource, state, items, total, fetched_at=fetched_at)


def _http_response(
    fonte: Fonte,
    *,
    resource: str,
    inicio: date | None,
    fim: date | None,
    page: int,
    page_size: int,
    extra: Mapping[str, str] | None = None,
) -> TransportResponse:
    params = {"page": str(page), "page_size": str(page_size)}
    if inicio:
        params["inicio"] = inicio.isoformat()
    if fim:
        params["fim"] = fim.isoformat()
    params.update(extra or {})
    endpoint = f"{fonte.url.rstrip('/')}/patrimonio/v3/{resource}?{urlencode(params)}"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return TransportResponse(502, error="resposta grande demais")
        return TransportResponse(200, json.loads(raw.decode("utf-8")), datetime.now().astimezone())
    except urllib.error.HTTPError as exc:
        return TransportResponse(exc.code, error=f"a fonte respondeu HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Fonte %s não respondeu para %s: %s", fonte.apelido, resource, exc)
        return TransportResponse(503, error="a fonte não respondeu")


def fetch_analytics(
    fonte: Fonte,
    *,
    resource: str,
    inicio: date | None = None,
    fim: date | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    extra: Mapping[str, str] | None = None,
    transport: ReadOnlyTransport | None = None,
) -> AnalyticsSourceResult[Any]:
    if resource not in {"income", "performance", "events"}:
        raise ValueError("recurso analítico inválido")
    if page < 1 or page_size < 1 or page_size > PAGE_SIZE_MAX:
        return AnalyticsSourceResult(fonte.apelido, resource, STATUS_ERROR, error="paginação inválida")
    try:
        params = {"page": str(page), "page_size": str(page_size)}
        if inicio:
            params["inicio"] = inicio.isoformat()
        if fim:
            params["fim"] = fim.isoformat()
        params.update(extra or {})
        response = (
            _http_response(fonte, resource=resource, inicio=inicio, fim=fim, page=page, page_size=page_size, extra=extra)
            if transport is None
            else transport.get(
                f"/patrimonio/v3/{resource}?{urlencode(params)}",
                timeout=TIMEOUT_SECONDS,
                headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"},
            )
        )
    except Exception as exc:  # noqa: BLE001 — uma fonte indisponível não derruba a tela
        LOGGER.warning("Falha ao buscar %s de %s: %s", resource, fonte.apelido, exc)
        return AnalyticsSourceResult(_source_name(None, fonte.apelido), resource, STATUS_ERROR, error="a fonte não respondeu")
    if response.status_code in {404, 405, 501}:
        return AnalyticsSourceResult(_source_name(None, fonte.apelido), resource, STATUS_UNSUPPORTED, error=f"{fonte.nome} não publica {resource}.", fetched_at=response.fetched_at)
    if not response.ok:
        return AnalyticsSourceResult(_source_name(None, fonte.apelido), resource, STATUS_ERROR, error=response.error or f"a fonte respondeu HTTP {response.status_code}", fetched_at=response.fetched_at)
    if response.payload is None:
        return AnalyticsSourceResult(_source_name(None, fonte.apelido), resource, STATUS_EMPTY, error="a fonte respondeu sem payload", fetched_at=response.fetched_at)
    try:
        return normalize_analytics_payload(response.payload, resource=resource, expected_source=fonte.apelido, fetched_at=response.fetched_at)
    except (DTOError, TypeError, ValueError) as exc:
        return AnalyticsSourceResult(_source_name(None, fonte.apelido), resource, STATUS_ERROR, error=str(exc), fetched_at=response.fetched_at)


def fetch_all_analytics(
    fonte: Fonte,
    *,
    resource: str,
    inicio: date | None = None,
    fim: date | None = None,
    fetch=fetch_analytics,
) -> AnalyticsSourceResult[Any]:
    """Todas as páginas de um recurso, ou erro: meia série não vira série.

    As fontes aceitam no máximo ``PAGE_SIZE_SOURCE`` itens por página; pedir
    mais devolve 400, e era por isso que a renda do CRV nunca aparecia.
    """
    items: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        result = fetch(fonte, resource=resource, inicio=inicio, fim=fim, page=page, page_size=PAGE_SIZE_SOURCE)
        if result.status in {STATUS_ERROR, STATUS_UNSUPPORTED}:
            return result
        items.extend(result.items)
        if not result.items or len(items) >= result.total:
            return replace(result, items=tuple(items), total=len(items))
    return AnalyticsSourceResult(
        _source_name(None, fonte.apelido), resource, STATUS_ERROR,
        error=f"{fonte.nome}: mais de {MAX_PAGES * PAGE_SIZE_SOURCE} itens de {resource} no período",
    )


def compose_analytics[T](resource: str, results: Iterable[AnalyticsSourceResult[T]]) -> AnalyticsComposition[T]:
    normalized = tuple(results)
    items = tuple(sorted((item for result in normalized for item in result.items), key=lambda item: (getattr(item, "date", date.min), getattr(item, "id", "")), reverse=True))
    warnings = tuple(dict.fromkeys(f"{result.source}: {result.error}" for result in normalized if result.error))
    if not normalized or not items:
        status = STATUS_EMPTY if not warnings else STATUS_ERROR
    elif warnings:
        status = STATUS_PARTIAL
    elif any(result.status == STATUS_STALE for result in normalized):
        status = STATUS_STALE
    else:
        status = STATUS_OK
    return AnalyticsComposition(resource, normalized, items, status, warnings)


__all__ = [
    "AnalyticsComposition",
    "AnalyticsSourceResult",
    "EventRecord",
    "IncomeRecord",
    "PerformanceRecord",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARTIAL",
    "STATUS_STALE",
    "STATUS_UNSUPPORTED",
    "compose_analytics",
    "fetch_all_analytics",
    "fetch_analytics",
    "normalize_analytics_payload",
]
