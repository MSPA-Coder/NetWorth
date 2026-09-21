"""Cliente e normalizador read-only do contrato ``patrimonio/v3/activities``.

As fontes publicam atividades em envelopes independentes. Este módulo mantém
essa fronteira pequena: recebe JSON, valida o que foi publicado, e compõe as
linhas sem escrever, somar moedas ou fabricar lançamentos ausentes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from consolidado.fontes import Fonte

from .models import ActivityDTO, Coverage, DTOError, Money, _decimal, _text
from .transport import ReadOnlyTransport, TransportResponse

LOGGER = logging.getLogger(__name__)
CONTRACT = "patrimonio/v3"
RESOURCE = "atividades"
TIMEOUT_SECONDS = 8
MAX_BYTES = 8 * 1024 * 1024
PAGE_SIZE_DEFAULT = 100
PAGE_SIZE_MAX = 500

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_EMPTY = "empty"
STATUS_STALE = "stale"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class ActivitySourceResult:
    source: str
    status: str
    activities: tuple[ActivityDTO, ...] = ()
    total: int = 0
    page: int = 1
    page_size: int = PAGE_SIZE_DEFAULT
    pages: int = 0
    error: str = ""
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {STATUS_OK, STATUS_PARTIAL, STATUS_EMPTY, STATUS_STALE, STATUS_ERROR}:
            raise ValueError("activity source: estado desconhecido")
        if self.total < 0 or self.page < 1 or self.page_size < 1 or self.pages < 0:
            raise ValueError("activity source: paginação inválida")
        if self.status == STATUS_ERROR and self.activities:
            raise ValueError("activity source: erro não pode conter atividades")


@dataclass(frozen=True, slots=True)
class ActivityComposition:
    results: tuple[ActivitySourceResult, ...]
    activities: tuple[ActivityDTO, ...]
    coverage: Coverage
    total: int

    @property
    def status(self) -> str:
        return self.coverage.status


def _source_name(raw: Any, expected: str | None = None) -> str:
    aliases = {
        "controle-bancario": "controle-bancario",
        "controle_bancario": "controle-bancario",
        "cb": "controle-bancario",
        "controle-renda-variavel": "controle-renda-variavel",
        "controle_renda_variavel": "controle-renda-variavel",
        "crv": "controle-renda-variavel",
    }
    value = str(raw or expected or "fonte-desconhecida").strip()
    return aliases.get(value.casefold(), value)


def _date(raw: Any, field_name: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        raise DTOError(f"{field_name}: data ISO obrigatória")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DTOError(f"{field_name}: data inválida") from exc


def _link(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "://" in value:
        return ""
    if any(ord(char) < 32 for char in value):
        return ""
    return value


def _id(source: str, raw: Any, index: int) -> str:
    value = str(raw or "").strip()
    if value.startswith(f"{source}:") and len(value) > len(source) + 1:
        return value
    digest = hashlib.sha256(f"{source}\x1fatividade\x1f{value or index}".encode()).hexdigest()[:24]
    return f"{source}:atividade:{digest}"


def _text_optional(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _nested(item: Mapping[str, Any], key: str, *fallbacks: str) -> Mapping[str, Any]:
    value = item.get(key)
    if isinstance(value, Mapping):
        return value
    for fallback in fallbacks:
        value = item.get(fallback)
        if isinstance(value, Mapping):
            return value
    return {}


def _amount(item: Mapping[str, Any], currency: str, index: int) -> Money:
    for key in ("valor_realizado", "valor", "valor_previsto", "amount", "value"):
        if key in item and item[key] is not None:
            try:
                return Money(_decimal(item[key], field_name=f"itens[{index}].{key}"), currency)
            except DTOError as exc:
                raise DTOError(str(exc)) from exc
    raise DTOError(f"itens[{index}]: falta valor")


def _optional_amount(item: Mapping[str, Any], currency: str, index: int) -> Money | None:
    value = item.get("valor_realizado", item.get("realized_value"))
    if value is None:
        return None
    return Money(_decimal(value, field_name=f"itens[{index}].valor_realizado"), currency)


def _activity(source: str, item: Mapping[str, Any], index: int) -> ActivityDTO:
    if not isinstance(item, Mapping):
        raise DTOError(f"itens[{index}]: esperado objeto")
    currency = _text(item.get("moeda", item.get("currency")), field_name=f"itens[{index}].moeda").upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise DTOError(f"itens[{index}].moeda: código inválido")
    account = _nested(item, "conta", "account")
    category = _nested(item, "categoria", "category")
    institution = _text_optional(item.get("instituicao")) or _text_optional(account.get("instituicao"))
    instrument = _text_optional(item.get("instrumento"))
    return ActivityDTO(
        id=_id(source, item.get("id"), index),
        date=_date(item.get("data", item.get("date")), f"itens[{index}].data"),
        description=_text(item.get("descricao", item.get("description")), field_name=f"itens[{index}].descricao"),
        kind=_text(item.get("tipo", item.get("kind", "atividade")), field_name=f"itens[{index}].tipo"),
        status=_text(item.get("status", "publicado"), field_name=f"itens[{index}].status"),
        value=_amount(item, currency, index),
        source=source,
        realized_value=_optional_amount(item, currency, index),
        institution=institution,
        instrument=instrument,
        owner=_text_optional(item.get("titular")) or _text_optional(account.get("titular")),
        category=_text_optional(item.get("categoria_nome")) or _text_optional(category.get("nome")),
        category_kind=_text_optional(item.get("natureza")) or _text_optional(category.get("natureza")),
        account=_text_optional(item.get("conta_nome")) or _text_optional(account.get("nome")),
        origin=_text_optional(item.get("origem")),
        link=_link(item.get("deep_link", item.get("endereco"))),
    )


def normalize_activities_payload(
    payload: Mapping[str, Any],
    *,
    expected_source: str | None = None,
    fetched_at: datetime | None = None,
) -> ActivitySourceResult:
    """Normaliza um envelope v3 sem reter ou mutar o dicionário externo."""
    if not isinstance(payload, Mapping):
        raise DTOError("envelope de atividades: esperado objeto JSON")
    if payload.get("contrato") != CONTRACT:
        raise DTOError(f"contrato de atividades desconhecido: {payload.get('contrato')!r}")
    if payload.get("recurso") not in {None, RESOURCE}:
        raise DTOError("recurso de atividades desconhecido")
    source = _source_name(payload.get("sistema"), expected_source)
    raw_items = payload.get("itens", payload.get("activities", []))
    if not isinstance(raw_items, list):
        raise DTOError("itens de atividades: esperado array")
    activities = tuple(_activity(source, item, index) for index, item in enumerate(raw_items))
    pagination = payload.get("paginacao")
    if not isinstance(pagination, Mapping):
        pagination = {}
    total = pagination.get("total", len(activities))
    page = pagination.get("pagina", 1)
    page_size = pagination.get("tamanho", len(activities) or PAGE_SIZE_DEFAULT)
    pages = pagination.get("paginas", 0)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (total, pages)):
        raise DTOError("paginacao: contagem inválida")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DTOError("paginacao.pagina: valor inválido")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise DTOError("paginacao.tamanho: valor inválido")
    state = str(payload.get("estado", payload.get("status", "ok"))).strip().casefold()
    state = {"sucesso": STATUS_OK, "parcial": STATUS_PARTIAL, "vazio": STATUS_EMPTY, "desatualizado": STATUS_STALE}.get(state, state)
    if state not in {STATUS_OK, STATUS_PARTIAL, STATUS_EMPTY, STATUS_STALE}:
        raise DTOError(f"estado de atividades desconhecido: {state!r}")
    if not activities and state == STATUS_OK:
        state = STATUS_EMPTY
    return ActivitySourceResult(source, state, activities, total, page, page_size, pages, fetched_at=fetched_at)


def _http_response(fonte: Fonte, *, inicio: date | None, fim: date | None, page: int, page_size: int) -> TransportResponse:
    params = {"page": str(page), "page_size": str(page_size)}
    if inicio:
        params["inicio"] = inicio.isoformat()
    if fim:
        params["fim"] = fim.isoformat()
    endpoint = f"{fonte.url.rstrip('/')}/patrimonio/v3/activities?{urlencode(params)}"
    request = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return TransportResponse(502, error="resposta grande demais")
        payload = json.loads(raw.decode("utf-8"))
        return TransportResponse(200, payload, datetime.now().astimezone())
    except urllib.error.HTTPError as exc:
        return TransportResponse(exc.code, error=f"a fonte respondeu HTTP {exc.code}")
    # The application test harness deliberately raises a RuntimeError when a
    # view accidentally attempts network access. Treat that exactly like an
    # unavailable source: the shell must render its explicit error state, not
    # turn a read-only page into HTTP 500.
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Fonte %s não respondeu para atividades: %s", fonte.apelido, exc)
        return TransportResponse(503, error="a fonte não respondeu")


def fetch_activities(
    fonte: Fonte,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    transport: ReadOnlyTransport | None = None,
) -> ActivitySourceResult:
    """Busca somente GET e converte erros de rede em estado explícito."""
    if page < 1 or page_size < 1 or page_size > PAGE_SIZE_MAX:
        return ActivitySourceResult(fonte.apelido, STATUS_ERROR, error="paginação inválida")
    try:
        if transport is None:
            response = _http_response(fonte, inicio=inicio, fim=fim, page=page, page_size=page_size)
        else:
            params = {"page": str(page), "page_size": str(page_size)}
            if inicio:
                params["inicio"] = inicio.isoformat()
            if fim:
                params["fim"] = fim.isoformat()
            response = transport.get(f"/patrimonio/v3/activities?{urlencode(params)}", timeout=TIMEOUT_SECONDS, headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 - uma fonte fora do ar não derruba a tela
        LOGGER.warning("Falha ao buscar atividades de %s: %s", fonte.apelido, exc)
        return ActivitySourceResult(_source_name(None, fonte.apelido), STATUS_ERROR, error="a fonte não respondeu")
    if not response.ok:
        return ActivitySourceResult(_source_name(None, fonte.apelido), STATUS_ERROR, error=response.error or f"a fonte respondeu HTTP {response.status_code}", fetched_at=response.fetched_at)
    if response.payload is None:
        return ActivitySourceResult(_source_name(None, fonte.apelido), STATUS_EMPTY, error="a fonte respondeu sem payload", fetched_at=response.fetched_at)
    try:
        return normalize_activities_payload(response.payload, expected_source=fonte.apelido, fetched_at=response.fetched_at)
    except (DTOError, TypeError, ValueError) as exc:
        return ActivitySourceResult(_source_name(None, fonte.apelido), STATUS_ERROR, error=str(exc), fetched_at=response.fetched_at)


def compose_activities(results: Iterable[ActivitySourceResult]) -> ActivityComposition:
    normalized = tuple(results)
    activities = tuple(sorted((item for result in normalized for item in result.activities), key=lambda item: (item.date, item.source, item.id), reverse=True))
    expected = len(normalized)
    responded = sum(result.status != STATUS_ERROR for result in normalized)
    omissions = tuple(dict.fromkeys(f"{result.source}: {result.error}" for result in normalized if result.status == STATUS_ERROR and result.error))
    if not expected or not activities and not omissions:
        status = STATUS_EMPTY
    elif omissions:
        status = STATUS_PARTIAL if activities else STATUS_ERROR
    elif any(result.status == STATUS_STALE for result in normalized):
        status = STATUS_STALE
    else:
        status = STATUS_OK
    return ActivityComposition(
        normalized,
        activities,
        Coverage(status == STATUS_OK, expected, responded, omissions, status, stale=status == STATUS_STALE),
        sum(result.total for result in normalized),
    )


__all__ = [
    "ActivityComposition",
    "ActivitySourceResult",
    "CONTRACT",
    "PAGE_SIZE_DEFAULT",
    "PAGE_SIZE_MAX",
    "compose_activities",
    "fetch_activities",
    "normalize_activities_payload",
]
