"""Agregações puras para o painel de patrimônio.

Este módulo não consulta fonte, ORM ou câmbio. Recebe as linhas e os eventos
que ``leitor`` já validou e devolve estruturas transitórias para a tela. Em
particular, moedas nunca são convertidas implicitamente: toda soma mantém a
moeda como parte da chave.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from consolidado.leitor import Consolidado, Leitura

ZERO = Decimal("0")

# Labels are deliberately stable: they are also the values sent to the v2
# publishers.  ``tudo`` has no lower bound and therefore never causes a
# caller to manufacture hundreds of point requests; a single range request is
# all the reader needs.
PERIODOS = ("1M", "3M", "YTD", "1Y", "5Y", "Tudo")


def intervalo_periodo(periodo: str, ate: date) -> tuple[date | None, date]:
    """Return the inclusive date range for a dashboard selector."""
    chave = str(periodo or "Tudo").strip().upper()
    if chave == "TUDO":
        return None, ate
    if chave == "YTD":
        return date(ate.year, 1, 1), ate
    meses = {"1M": 1, "3M": 3, "1Y": 12, "5Y": 60}
    if chave not in meses:
        raise ValueError(f"período desconhecido: {periodo!r}")
    total = ate.year * 12 + ate.month - 1 - meses[chave]
    ano, mes = divmod(total, 12)
    dia = min(ate.day, monthrange(ano, mes + 1)[1])
    return date(ano, mes + 1, dia), ate


def filtrar_periodo(itens: Iterable[Any], periodo: str, ate: date) -> list[Any]:
    inicio, fim = intervalo_periodo(periodo, ate)
    return [item for item in itens if (dia := _data(item)) is not None and (inicio is None or dia >= inicio) and dia <= fim]


def _decimal(valor: Any, padrao: Decimal | None = None) -> Decimal | None:
    if valor is None or valor == "":
        return padrao
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, str):
        try:
            resultado = Decimal(valor)
        except Exception:
            return padrao
        return resultado if resultado.is_finite() else padrao
    # Dashboard data is normally already normalized by leitor.  Refusing
    # float avoids reintroducing binary cents in tests or future callers.
    if isinstance(valor, (int,)):
        return Decimal(valor)
    return padrao


def _campo(item: Any, *nomes: str, padrao: Any = None) -> Any:
    for nome in nomes:
        if isinstance(item, Mapping) and nome in item:
            return item[nome]
        if hasattr(item, nome):
            return getattr(item, nome)
    return padrao


def _data(item: Any) -> date | None:
    bruto = _campo(item, "data", "data_de_referencia", "date", "dia", "observed_date")
    if isinstance(bruto, date):
        return bruto
    if isinstance(bruto, str):
        try:
            return date.fromisoformat(bruto[:10])
        except ValueError:
            return None
    return None


def _linhas(obj: Any) -> list[Any]:
    if isinstance(obj, Consolidado):
        return list(obj.linhas)
    if isinstance(obj, Leitura):
        return list(obj.linhas)
    if hasattr(obj, "linhas") and not isinstance(obj, (list, tuple, dict)):
        return list(obj.linhas)
    return list(obj or [])


def _eventos(obj: Any, atributo: str, aliases: tuple[str, ...]) -> list[Any]:
    if isinstance(obj, Consolidado):
        return list(getattr(obj, atributo))
    if isinstance(obj, Leitura):
        return list(getattr(obj, atributo))
    if isinstance(obj, Mapping):
        for alias in aliases:
            if alias in obj:
                return list(obj[alias] or [])
    return list(obj or [])


def _valor_linha(linha: Any) -> Decimal:
    return _decimal(
        _campo(linha, "valor", "valor_a_mercado", "valor_bruto", "total"),
        ZERO,
    ) or ZERO


def _moeda(item: Any) -> str:
    return str(_campo(item, "moeda", "currency", padrao="" ) or "")


def top_posicoes(
    obj: Any,
    limite: int = 10,
    *,
    por: str = "exposicao_bruta",
) -> list[dict[str, Any]]:
    """Return the largest investment positions without mixing currencies.

    ``por='exposicao_bruta'`` ranks by absolute gross exposure, so a short
    position is not hidden by its negative market value. ``por='valor'`` ranks
    by absolute market value. Rows retain their original currency and link.
    """
    if limite <= 0:
        return []
    linhas = [
        linha for linha in _linhas(obj)
        if str(_campo(linha, "papel", padrao="investimento")) == "investimento"
    ]
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for linha in linhas:
        descricao = str(_campo(linha, "descricao", "instrumento", padrao="—"))
        moeda = _moeda(linha)
        chave = (descricao, moeda)
        grupo = grupos.setdefault(
            chave,
            {
                "descricao": descricao,
                "moeda": moeda,
                "valor": ZERO,
                "exposicao_bruta": ZERO,
                "quantidade": ZERO,
                "instituicoes": set(),
                "links": set(),
                "linhas": 0,
            },
        )
        valor = _valor_linha(linha)
        bruto = _decimal(
            _campo(linha, "exposicao_bruta", "valor_bruto", "gross_exposure"),
            abs(valor),
        )
        grupo["valor"] += valor
        grupo["exposicao_bruta"] += abs(bruto or ZERO)
        quantidade = _decimal(_campo(linha, "quantidade"), None)
        if quantidade is not None:
            grupo["quantidade"] += quantidade
        instituicao = str(_campo(linha, "instituicao", padrao="") or "")
        if instituicao:
            grupo["instituicoes"].add(instituicao)
        link = str(_campo(linha, "link", padrao="") or "")
        if link:
            grupo["links"].add(link)
        grupo["linhas"] += 1

    campo = "exposicao_bruta" if por in {"exposicao_bruta", "bruto", "gross"} else "valor"
    ordenados = sorted(
        grupos.values(),
        key=lambda item: (-abs(item[campo]), item["descricao"], item["moeda"]),
    )[:limite]
    totais_brutos: dict[str, Decimal] = {}
    for grupo in grupos.values():
        totais_brutos[grupo["moeda"]] = (
            totais_brutos.get(grupo["moeda"], ZERO) + grupo["exposicao_bruta"]
        )
    resultado = []
    for grupo in ordenados:
        total = totais_brutos[grupo["moeda"]]
        resultado.append(
            {
                **grupo,
                "instituicoes": sorted(grupo["instituicoes"]),
                "links": sorted(grupo["links"]),
                "link": next(iter(grupo["links"])) if len(grupo["links"]) == 1 else "",
                "percentual": grupo["exposicao_bruta"] / total * 100 if total else None,
            }
        )
    return resultado


# English alias is useful to callers that share code with Wealthfolio exports.
top_positions = top_posicoes


def top_posicoes_por_exposicao_bruta(obj: Any, limite: int = 10) -> list[dict[str, Any]]:
    return top_posicoes(obj, limite, por="exposicao_bruta")


def top_posicoes_por_valor(obj: Any, limite: int = 10) -> list[dict[str, Any]]:
    return top_posicoes(obj, limite, por="valor")


def composicao(obj: Any, *, por: str = "moeda") -> list[dict[str, Any]]:
    """Group position/account exposure by a dimension and currency.

    A row is emitted per ``(dimension, currency)``. Percentages are calculated
    only within that currency, never across BRL and USD.
    """
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for linha in _linhas(obj):
        chave = _campo(linha, por, padrao="Não informado") or "Não informado"
        if por == "moeda":
            chave = _moeda(linha) or "Não informado"
        moeda = _moeda(linha) or "Não informado"
        key = (str(chave), moeda)
        bloco = grupos.setdefault(
            key,
            {"nome": str(chave), "moeda": moeda, "total": ZERO, "linhas": 0},
        )
        bloco["total"] += _valor_linha(linha)
        bloco["linhas"] += int(_campo(linha, "linhas", padrao=1) or 0)
    totais: dict[str, Decimal] = {}
    for bloco in grupos.values():
        totais[bloco["moeda"]] = totais.get(bloco["moeda"], ZERO) + abs(bloco["total"])
    resultado = []
    for bloco in sorted(grupos.values(), key=lambda x: (-abs(x["total"]), x["nome"], x["moeda"])):
        total = totais[bloco["moeda"]]
        item = dict(bloco)
        item["percentual"] = (abs(item["total"]) / total * 100) if total else None
        resultado.append(item)
    return resultado


def composicao_por_moeda(obj: Any) -> list[dict[str, Any]]:
    return composicao(obj, por="moeda")


def _agrupar_eventos(
    eventos: Iterable[Any],
    *,
    incluir_natureza: bool = True,
) -> list[dict[str, Any]]:
    grupos: dict[tuple[Any, ...], dict[str, Any]] = {}
    for evento in eventos:
        dia = _data(evento)
        moeda = _moeda(evento)
        natureza = str(_campo(evento, "natureza", "tipo", "kind", padrao="") or "")
        chave = (dia, moeda, natureza) if incluir_natureza else (dia, moeda)
        bloco = grupos.setdefault(
            chave,
            {"data": dia, "moeda": moeda, "natureza": natureza, "total": ZERO, "linhas": 0},
        )
        bloco["total"] += _decimal(_campo(evento, "valor", "amount", "total"), ZERO) or ZERO
        bloco["linhas"] += int(_campo(evento, "linhas", "transacoes", padrao=1) or 0)
    return [grupos[key] for key in sorted(grupos, key=lambda k: (k[0] is None, k[0], k[1], k[2]))]


def fluxos_cb(obj: Any) -> list[dict[str, Any]]:
    """Aggregate bank cash flows by date, currency and nature."""
    return _agrupar_eventos(_eventos(obj, "fluxos", ("fluxos", "movimentos", "cash_flows")))


agregar_fluxos_cb = fluxos_cb
resumo_fluxos = fluxos_cb
agrupar_fluxos = fluxos_cb


def resumo_fluxos_por_natureza(obj: Any) -> list[dict[str, Any]]:
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    eventos = _eventos(obj, "fluxos", ("fluxos", "movimentos", "cash_flows"))
    for evento in eventos:
        moeda = _moeda(evento)
        natureza = str(_campo(evento, "natureza", padrao="") or "")
        chave = (moeda, natureza)
        grupo = grupos.setdefault(
            chave,
            {
                "moeda": moeda,
                "natureza": natureza,
                "entradas": ZERO,
                "saidas": ZERO,
                "liquido": ZERO,
                "linhas": 0,
            },
        )
        grupo["entradas"] += _decimal(_campo(evento, "entradas"), ZERO) or ZERO
        grupo["saidas"] += _decimal(_campo(evento, "saidas"), ZERO) or ZERO
        grupo["liquido"] += _decimal(
            _campo(evento, "liquido", "valor", "total"), ZERO
        ) or ZERO
        grupo["linhas"] += int(_campo(evento, "linhas", padrao=1) or 0)
    return [grupos[chave] for chave in sorted(grupos)]


def _resumo_por_moeda(obj: Any, atributo: str, aliases: tuple[str, ...]) -> list[dict[str, Any]]:
    eventos = _eventos(obj, atributo, aliases)
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for evento in eventos:
        moeda = _moeda(evento)
        tipo = str(_campo(evento, "natureza", "tipo", "kind", padrao="") or "")
        key = (moeda, tipo)
        bloco = grupos.setdefault(key, {"moeda": moeda, "tipo": tipo, "total": ZERO, "linhas": 0})
        bloco["total"] += _decimal(_campo(evento, "valor", "amount", "total"), ZERO) or ZERO
        bloco["linhas"] += 1
    return [grupos[key] for key in sorted(grupos)]


def resumo_renda(obj: Any) -> list[dict[str, Any]]:
    return _resumo_por_moeda(obj, "rendas", ("rendas", "proventos", "income"))


def resumo_ganhos(obj: Any) -> list[dict[str, Any]]:
    return _resumo_por_moeda(obj, "ganhos_realizados", ("ganhos_realizados", "ganhos", "realized_gains"))


def resumo_financeiro(obj: Any) -> dict[str, list[dict[str, Any]]]:
    """One explicit, currency-preserving summary for the three v2 streams."""
    return {
        "fluxos": fluxos_cb(obj),
        "renda": resumo_renda(obj),
        "ganhos": resumo_ganhos(obj),
    }


def variacao_fotos(anterior: Any, atual: Any | None = None) -> Any:
    """Absolute and percentage variation, preserving separate currencies.

    With two snapshots, returns a currency mapping. With a sequence of
    snapshots, returns one row for each adjacent pair (oldest first).
    """
    if atual is None and isinstance(anterior, Sequence) and not isinstance(anterior, (str, bytes, dict)):
        fotos = sorted(anterior, key=lambda item: _data(item) or date.min)
        return [variacao_fotos(fotos[i], fotos[i + 1]) for i in range(len(fotos) - 1)]
    if atual is None:
        return {}

    def totais(foto: Any) -> dict[str, Decimal]:
        bruto = _campo(foto, "totais_por_moeda", "totals", padrao=None)
        if bruto is None and isinstance(foto, Mapping):
            # Compact shape accepted for callers that already grouped a photo
            # by currency, e.g. ``{"BRL": "10.00"}``.
            return {
                str(moeda): _decimal(valor, ZERO) or ZERO
                for moeda, valor in foto.items()
                if _decimal(valor, None) is not None
            }
        resultado: dict[str, Decimal] = {}
        for item in bruto or []:
            if isinstance(item, Mapping):
                moeda = _moeda(item)
                valor = _decimal(_campo(item, "total", "valor", "saldo"), ZERO) or ZERO
                resultado[moeda] = resultado.get(moeda, ZERO) + valor
        return resultado

    antes, depois = totais(anterior), totais(atual)
    resultado = []
    for moeda in sorted(set(antes) | set(depois)):
        velho, novo = antes.get(moeda, ZERO), depois.get(moeda, ZERO)
        absoluto = novo - velho
        resultado.append({
            "moeda": moeda,
            "anterior": velho,
            "atual": novo,
            "variacao_absoluta": absoluto,
            "variacao_percentual": (absoluto / abs(velho) * 100) if velho else None,
        })
    return resultado


variacao_absoluta_percentual = variacao_fotos


def links_secoes(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, Consolidado):
        return list(obj.secoes)
    if isinstance(obj, Leitura):
        return list(obj.secoes)
    if isinstance(obj, Mapping):
        return list(obj.get("secoes", obj.get("links_secoes", [])) or [])
    return list(obj or [])


def qualidade(obj: Any) -> list[Any]:
    if isinstance(obj, Consolidado):
        return list(obj.qualidade)
    if isinstance(obj, Leitura):
        return [obj.qualidade] if obj.qualidade is not None else []
    if isinstance(obj, Mapping):
        return list(obj.get("qualidade", obj.get("quality", [])) or [])
    return []
