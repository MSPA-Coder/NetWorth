"""Validação e normalização pura dos envelopes patrimoniais v1/v2."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from consolidado import leitor
from consolidado.fontes import Fonte

from .models import (
    AccountDTO,
    Capabilities,
    Coverage,
    DTOError,
    FlowDTO,
    GainDTO,
    IncomeDTO,
    Money,
    PerformanceDTO,
    PositionDTO,
    SnapshotDTO,
    _decimal,
    _text,
)

CONTRACTS = frozenset({"patrimonio/v1", "patrimonio/v2"})
SOURCE_ALIASES = {
    "controle-bancario": "controle-bancario",
    "controle_bancario": "controle-bancario",
    "cb": "controle-bancario",
    "controle-renda-variavel": "controle-renda-variavel",
    "controle_renda_variavel": "controle-renda-variavel",
    "crv": "controle-renda-variavel",
}


class CompatibilityError(DTOError):
    """Envelope externo inválido ou incompatível com o contrato interno."""


def _source(raw: Any, expected: str | None) -> str:
    value = _text(raw, field_name="sistema") if raw else expected or ""
    normalized = SOURCE_ALIASES.get(value.casefold())
    if not normalized:
        raise CompatibilityError(f"sistema desconhecido: {value!r}")
    if expected and normalized != SOURCE_ALIASES.get(expected.casefold(), expected):
        raise CompatibilityError("sistema da resposta não corresponde à fonte esperada")
    return normalized


def _date(raw: Any, field_name: str) -> date:
    if isinstance(raw, date) and not hasattr(raw, "hour"):
        return raw
    if not isinstance(raw, str):
        raise CompatibilityError(f"{field_name}: data ISO obrigatória")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise CompatibilityError(f"{field_name}: data inválida") from exc


def _id(source: str, kind: str, raw: Any, index: int) -> str:
    """Retorna identificador estável sem expor IDs externos não prefixados."""
    value = str(raw or "").strip()
    prefix = f"{source}:"
    if value.startswith(prefix) and len(value) > len(prefix):
        return value
    seed = f"{source}\x1f{kind}\x1f{value or index}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{source}:{kind}:{digest}"


def _link(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return ""
    if any(ord(char) < 32 for char in value) or "://" in value:
        return ""
    return value


def _money(value: Any, currency: Any, field: str) -> Money:
    try:
        return Money.from_wire(value, currency, field_name=field)
    except DTOError as exc:
        raise CompatibilityError(str(exc)) from exc


def _decimal_optional(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return _decimal(value, field_name=field)
    except DTOError as exc:
        raise CompatibilityError(str(exc)) from exc


def _fonte(source: str) -> Fonte:
    papel = "caixa" if source == "controle-bancario" else "investimento"
    apelido = "CB" if source == "controle-bancario" else "CRV"
    nome = "Controle Bancário" if apelido == "CB" else "Controle de Renda Variável"
    return Fonte(apelido, nome, papel, "", "")


def _critical_omissions(payload: Mapping[str, Any], parsed: leitor.Leitura) -> tuple[str, ...]:
    omissions: list[str] = list(parsed.lacunas)
    raw = payload.get("omitidas")
    if isinstance(raw, Mapping):
        for key in ("sem_cotacao", "simuladas", "opcoes"):
            value = raw.get(key)
            if isinstance(value, int) and value > 0 and key == "sem_cotacao" and not omissions:
                omissions.append(f"{value} posição sem cotação ficou de fora")
    return tuple(dict.fromkeys(omissions))


def _totals(payload: Mapping[str, Any], lines: Iterable[leitor.Linha]) -> tuple[Money, ...]:
    raw = payload.get("totais_por_moeda")
    if raw is None:
        grouped: dict[str, Decimal] = {}
        for line in lines:
            grouped[line.moeda] = grouped.get(line.moeda, Decimal("0")) + line.valor
        return tuple(Money(amount, currency) for currency, amount in sorted(grouped.items()))
    if not isinstance(raw, list):
        raise CompatibilityError("totais_por_moeda: esperado array")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise CompatibilityError(f"totais_por_moeda[{index}]: esperado objeto")
        result.append(_money(item.get("total"), item.get("moeda"), f"totais_por_moeda[{index}].total"))
    return tuple(result)


def _accounts(source: str, parsed: leitor.Leitura) -> tuple[AccountDTO, ...]:
    result = []
    for index, line in enumerate(parsed.linhas):
        if line.papel != "caixa":
            continue
        result.append(AccountDTO(_id(source, "conta", line.id_de_origem, index), line.titular, line.instituicao, line.descricao, Money(line.valor, line.moeda), source, _link(line.link)))
    return tuple(result)


def _positions(source: str, parsed: leitor.Leitura) -> tuple[PositionDTO, ...]:
    result = []
    for index, line in enumerate(parsed.linhas):
        if line.papel != "investimento":
            continue
        cost = Money(line.custo, line.moeda) if line.custo is not None else None
        gain = Money(line.ganho_nao_realizado, line.moeda) if line.ganho_nao_realizado is not None else None
        result.append(PositionDTO(_id(source, "posicao", line.id_de_origem, index), line.titular, line.instituicao, line.descricao, Money(line.valor, line.moeda), source, line.classe, line.mercado, line.quantidade, line.preco, cost, gain, _link(line.link), line.qualidade))
    return tuple(result)


def _flows(source: str, parsed: leitor.Leitura) -> tuple[FlowDTO, ...]:
    result = []
    for index, item in enumerate(parsed.fluxos):
        try:
            currency = item["moeda"]
            inflow = Money(item["entradas"], currency)
            outflow = Money(item["saidas"], currency)
            value = Money(item.get("liquido", item.get("valor")), currency)
            lines = item.get("linhas", 0)
            if isinstance(lines, bool) or not isinstance(lines, int) or lines < 0:
                raise ValueError("linhas inválidas")
            if value.amount != inflow.amount - outflow.amount:
                raise ValueError("liquido não fecha")
            result.append(FlowDTO(_date(item["data"], f"fluxos[{index}].data"), value, inflow, outflow, _text(item.get("natureza"), field_name=f"fluxos[{index}].natureza"), source, lines, _link(item.get("link"))))
        except (KeyError, TypeError, ValueError, DTOError) as exc:
            raise CompatibilityError(f"fluxos[{index}]: {exc}") from exc
    return tuple(result)


def _income(source: str, parsed: leitor.Leitura) -> tuple[IncomeDTO, ...]:
    result = []
    for index, item in enumerate(parsed.rendas):
        try:
            currency = item["moeda"]
            by_type = tuple((str(key), Money(value, currency)) for key, value in sorted((item.get("por_tipo") or {}).items()))
            result.append(IncomeDTO(Money(item.get("total", item.get("valor")), currency), source, by_type, _link(item.get("link"))))
        except (KeyError, TypeError, DTOError) as exc:
            raise CompatibilityError(f"renda[{index}]: {exc}") from exc
    return tuple(result)


def _legacy_income(source: str, payload: Mapping[str, Any]) -> tuple[IncomeDTO, ...]:
    """Converte a lista v1 de proventos no mesmo agregado usado pela v2."""
    grouped: dict[str, dict[str, Decimal]] = {}
    raw = payload.get("proventos") or []
    if not isinstance(raw, list):
        raise CompatibilityError("proventos: esperado array")
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise CompatibilityError(f"proventos[{index}]: esperado objeto")
        try:
            currency = _text(item.get("moeda"), field_name=f"proventos[{index}].moeda").upper()
            kind = str(item.get("tipo") or "provento")
            amount = _decimal(item.get("valor"), field_name=f"proventos[{index}].valor")
        except DTOError as exc:
            raise CompatibilityError(str(exc)) from exc
        by_type = grouped.setdefault(currency, {})
        by_type[kind] = by_type.get(kind, Decimal("0")) + amount
    return tuple(
        IncomeDTO(
            Money(sum(by_type.values(), Decimal("0")), currency),
            source,
            tuple((kind, Money(amount, currency)) for kind, amount in sorted(by_type.items())),
            "",
        )
        for currency, by_type in sorted(grouped.items())
    )


def _gains(source: str, parsed: leitor.Leitura) -> tuple[GainDTO, ...]:
    result = []
    for index, item in enumerate(parsed.ganhos_realizados):
        try:
            currency = item["moeda"]
            transactions = item.get("transacoes", 0)
            if isinstance(transactions, bool) or not isinstance(transactions, int) or transactions < 0:
                raise ValueError("transacoes inválidas")
            result.append(GainDTO(Money(item.get("resultado", item.get("valor", 0)), currency), source, str(item.get("instrumento") or ""), transactions, _link(item.get("link"))))
        except (KeyError, TypeError, ValueError, DTOError) as exc:
            raise CompatibilityError(f"ganhos_realizados[{index}]: {exc}") from exc
    return tuple(result)


def _performance(source: str, parsed: leitor.Leitura) -> tuple[PerformanceDTO, ...]:
    result = []
    for index, item in enumerate(parsed.twr):
        points = []
        for point_index, point in enumerate(item.get("pontos") or []):
            if not isinstance(point, Mapping):
                raise CompatibilityError(f"desempenho[{index}].pontos[{point_index}]: esperado objeto")
            raw_return = point.get("retorno_acumulado", point.get("indice_twr"))
            if raw_return is None:
                continue
            points.append((_date(point.get("data"), f"desempenho[{index}].pontos.data"), _decimal_optional(raw_return, "retorno_acumulado") or Decimal("0")))
        result.append(PerformanceDTO(_text(item.get("moeda"), field_name=f"desempenho[{index}].moeda"), str(item.get("metodo") or ""), source, tuple(points), _link(item.get("link"))))
    return tuple(result)


def normalize_payload(payload: Mapping[str, Any], *, expected_source: str | None = None) -> SnapshotDTO:
    """Valida um envelope v1/v2 e o converte para DTOs congelados."""
    if not isinstance(payload, Mapping):
        raise CompatibilityError("envelope: esperado objeto JSON")
    contract = payload.get("contrato")
    if contract not in CONTRACTS:
        raise CompatibilityError(f"contrato desconhecido: {contract!r}")
    source = _source(payload.get("sistema"), expected_source)
    reference = _date(payload.get("data_de_referencia"), "data_de_referencia")
    parsed = leitor.interpretar(_fonte(source), dict(payload))
    if not parsed.respondeu:
        raise CompatibilityError(parsed.motivo or "payload recusado pela validação")
    omissions = _critical_omissions(payload, parsed)
    income = _income(source, parsed)
    if not income and contract == "patrimonio/v1":
        income = _legacy_income(source, payload)
    capabilities = Capabilities(
        source=source,
        contract=contract,
        accounts=bool(parsed.linhas and any(line.papel == "caixa" for line in parsed.linhas)),
        positions=bool(parsed.linhas and any(line.papel == "investimento" for line in parsed.linhas)),
        flows=bool(parsed.fluxos),
        performance=bool(parsed.twr),
        income=bool(income),
        realized_gains=bool(parsed.ganhos_realizados),
        quality=parsed.qualidade is not None,
        individual_activities=False,
    )
    coverage = Coverage(not omissions, 1, 1, omissions, "partial" if omissions else "ok")
    quality = parsed.qualidade if isinstance(parsed.qualidade, Mapping) else {}
    return SnapshotDTO(
        source=source,
        contract=contract,
        as_of=reference,
        generated_at=payload.get("gerado_em") if isinstance(payload.get("gerado_em"), str) else None,
        totals=_totals(payload, parsed.linhas),
        accounts=_accounts(source, parsed),
        positions=_positions(source, parsed),
        flows=_flows(source, parsed),
        performance=_performance(source, parsed),
        income=income,
        realized_gains=_gains(source, parsed),
        coverage=coverage,
        capabilities=capabilities,
        omissions=omissions,
        quality=quality,
    )


def normalize_payloads(payloads: Iterable[Mapping[str, Any]]) -> tuple[SnapshotDTO, ...]:
    """Normaliza várias fontes sem somar moedas ou mutar os envelopes."""
    return tuple(normalize_payload(payload) for payload in payloads)
