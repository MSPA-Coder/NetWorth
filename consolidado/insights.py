"""Agregações transitórias para a visão Insights.

Este módulo não consulta fontes nem persiste dados. Ele recebe as linhas que o
leitor já validou, mantém as moedas separadas e prepara uma representação
pequena para a página. Classificações que não existem nos contratos continuam
explicitamente como ``Não classificado``; o NetWorth não deduz setor ou região
a partir de ticker, instituição ou mercado.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

from consolidado.cambio import SerieDeTaxas, converter_totais

ZERO = Decimal("0.00")
NAO_CLASSIFICADO = "Não classificado"
NAO_INFORMADO = "Não informado"

DIMENSOES: tuple[tuple[str, str], ...] = (
    ("classe", "Classe de ativo"),
    ("setor", "Setor"),
    ("regiao", "Região"),
    ("mercado", "Mercado"),
    ("moeda", "Moeda"),
    ("instituicao", "Instituição"),
    ("titular", "Titular"),
    ("fonte", "Fonte"),
    ("instrumento", "Posição / conta"),
)

ORDENACOES = {
    "valor": "Valor",
    "percentual": "Percentual",
    "nome": "Nome",
    "linhas": "Linhas",
}


def id_item(dimensao: str, nome: str) -> str:
    """ID opaco e estável para filtros e drill-down de uma dimensão."""

    canonico = f"{dimensao}\x1f{nome}".casefold().strip()
    return f"insight-{sha256(canonico.encode('utf-8')).hexdigest()[:20]}"


def dimensoes() -> list[dict[str, str]]:
    return [{"chave": chave, "nome": nome} for chave, nome in DIMENSOES]


def _texto(linha: Any, atributo: str) -> str:
    return str(getattr(linha, atributo, "") or "").strip()


def nome_da_dimensao(linha: Any, dimensao: str) -> str:
    papel = _texto(linha, "papel").casefold()
    if dimensao == "classe":
        if papel == "caixa":
            return "Caixa"
        return _texto(linha, "classe") or NAO_CLASSIFICADO
    if dimensao == "setor":
        return _texto(linha, "setor") or NAO_CLASSIFICADO
    if dimensao == "regiao":
        return _texto(linha, "regiao") or NAO_CLASSIFICADO
    if dimensao == "mercado":
        if papel == "caixa":
            return "Caixa"
        return _texto(linha, "mercado") or NAO_CLASSIFICADO
    if dimensao == "moeda":
        return _texto(linha, "moeda") or NAO_INFORMADO
    if dimensao == "instituicao":
        return _texto(linha, "instituicao") or NAO_INFORMADO
    if dimensao == "titular":
        return _texto(linha, "titular") or NAO_INFORMADO
    if dimensao == "fonte":
        return _texto(linha, "fonte") or NAO_INFORMADO
    if dimensao == "instrumento":
        return _texto(linha, "descricao") or "Linha sem descrição"
    return NAO_INFORMADO


def dimensao_valida(valor: str | None) -> str:
    possiveis = {chave for chave, _ in DIMENSOES}
    return valor if valor in possiveis else "classe"


def _totais(linhas: Iterable[Any], *, absoluto: bool = False) -> list[dict[str, Any]]:
    acumulado: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        moeda = _texto(linha, "moeda") or NAO_INFORMADO
        valor = getattr(linha, "valor", ZERO) or ZERO
        if absoluto:
            valor = abs(valor)
        item = acumulado.setdefault(
            moeda,
            {"moeda": moeda, "total": ZERO, "linhas": 0},
        )
        item["total"] += valor
        item["linhas"] += 1
    return [acumulado[chave] for chave in sorted(acumulado)]


def _base_url(
    *,
    periodo: str,
    data: str,
    dimensao: str,
    filtro_dimensao: str = "",
    filtro: str = "",
    busca: str = "",
    ordenar: str = "valor",
    direcao: str = "desc",
    grupo: str = "",
    conta: str = "",
) -> str:
    parametros: list[tuple[str, str]] = [
        ("visao", "insights"),
        ("periodo", periodo),
        ("data", data),
        ("dimensao", dimensao),
    ]
    for chave, valor in (
        ("filtro_dimensao", filtro_dimensao),
        ("filtro", filtro),
        ("busca", busca),
        ("ordenar", ordenar),
        ("direcao", direcao),
        ("grupo", grupo),
        ("conta", conta),
    ):
        if valor:
            parametros.append((chave, valor))
    return f"?{urlencode(parametros)}"


def _valor_para_busca(linha: Any) -> str:
    return " ".join(
        _texto(linha, atributo)
        for atributo in (
            "descricao",
            "instituicao",
            "titular",
            "fonte",
            "moeda",
            "classe",
            "mercado",
            "setor",
            "regiao",
        )
    ).casefold()


def _conversao(
    linhas: list[Any], referencia, serie: SerieDeTaxas | None
):
    return converter_totais(_totais(linhas), referencia, serie=serie)


def _itens(
    linhas: list[Any],
    *,
    dimensao: str,
    referencia,
    serie: SerieDeTaxas | None,
    periodo: str,
    data: str,
    filtro_dimensao: str,
    filtro: str,
    busca: str,
    ordenar: str,
    direcao: str,
    grupo: str,
    conta: str,
) -> list[dict[str, Any]]:
    por_nome: dict[str, list[Any]] = defaultdict(list)
    for linha in linhas:
        por_nome[nome_da_dimensao(linha, dimensao)].append(linha)

    brutos: list[dict[str, Any]] = []
    for nome, linhas_do_grupo in por_nome.items():
        conversao = _conversao(linhas_do_grupo, referencia, serie)
        brutos.append(
            {
                "id": id_item(dimensao, nome),
                "nome": nome,
                "dimensao": dimensao,
                "linhas": len(linhas_do_grupo),
                "fontes": sorted({_texto(linha, "fonte") for linha in linhas_do_grupo if _texto(linha, "fonte")}),
                "instituicoes": sorted({_texto(linha, "instituicao") for linha in linhas_do_grupo if _texto(linha, "instituicao")}),
                "totais_por_moeda": _totais(linhas_do_grupo),
                "conversao": conversao,
                "exposicao_por_moeda": _totais(linhas_do_grupo, absoluto=True),
                "linhas_origem": linhas_do_grupo,
            }
        )

    conversoes_validas = [item["conversao"] for item in brutos if item["conversao"].possivel]
    conversao_global = len(conversoes_validas) == len(brutos) and bool(brutos)
    denominador_base = sum(
        (abs(item["conversao"].total) for item in brutos if item["conversao"].possivel),
        ZERO,
    )
    denominadores_por_moeda: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for item in brutos:
        for total in item["exposicao_por_moeda"]:
            denominadores_por_moeda[total["moeda"]] += total["total"]

    resultado: list[dict[str, Any]] = []
    for item in brutos:
        if conversao_global and denominador_base:
            item["percentual"] = (
                abs(item["conversao"].total) * 100 / denominador_base
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            item["percentuais_por_moeda"] = []
        else:
            item["percentual"] = None
            item["percentuais_por_moeda"] = [
                {
                    "moeda": total["moeda"],
                    "percentual": (
                        total["total"] * 100 / denominadores_por_moeda[total["moeda"]]
                        if denominadores_por_moeda[total["moeda"]]
                        else None
                    ),
                }
                for total in item["exposicao_por_moeda"]
            ]
        item["url"] = _base_url(
            periodo=periodo,
            data=data,
            dimensao=dimensao,
            filtro_dimensao=dimensao,
            filtro=item["id"],
            busca=busca,
            ordenar=ordenar,
            direcao=direcao,
            grupo=grupo,
            conta=conta,
        )
        item["selecionado"] = filtro_dimensao == dimensao and filtro == item["id"]
        item.pop("linhas_origem")
        resultado.append(item)

    if conversao_global and resultado and denominador_base:
        diferenca = Decimal("100.00") - sum(
            (item["percentual"] for item in resultado), ZERO
        )
        maior = max(resultado, key=lambda item: abs(item["conversao"].total))
        maior["percentual"] += diferenca
    else:
        for moeda in denominadores_por_moeda:
            itens_da_moeda = [
                item
                for item in resultado
                if any(total["moeda"] == moeda for total in item["exposicao_por_moeda"])
            ]
            percentuais = [
                parcial
                for item in itens_da_moeda
                for parcial in item["percentuais_por_moeda"]
                if parcial["moeda"] == moeda and parcial["percentual"] is not None
            ]
            for parcial in percentuais:
                parcial["percentual"] = parcial["percentual"].quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            if percentuais:
                diferenca = Decimal("100.00") - sum(
                    (parcial["percentual"] for parcial in percentuais), ZERO
                )
                percentuais[0]["percentual"] += diferenca

    if ordenar == "nome":
        def chave(item):
            return item["nome"].casefold(), item["id"]
    elif ordenar == "linhas":
        def chave(item):
            return item["linhas"], item["nome"].casefold()
    elif ordenar == "percentual":
        def chave(item):
            return (
                item["percentual"] if item["percentual"] is not None else Decimal("-1"),
                item["nome"].casefold(),
            )
    else:
        def chave(item):
            return (
                abs(item["conversao"].total) if item["conversao"].possivel else max(
                    (total["total"] for total in item["exposicao_por_moeda"]), default=ZERO
                ),
                item["nome"].casefold(),
            )
    resultado.sort(key=chave, reverse=direcao != "asc")
    return resultado


def montar(
    linhas: Iterable[Any],
    *,
    referencia,
    periodo: str,
    data: str,
    dimensao: str = "classe",
    filtro_dimensao: str = "",
    filtro: str = "",
    busca: str = "",
    ordenar: str = "valor",
    direcao: str = "desc",
    grupo: str = "",
    conta: str = "",
    fonte_completa: bool = True,
    quantidade_fontes: int = 0,
    quantidade_fontes_esperadas: int = 0,
    motivo_fontes: str = "",
) -> dict[str, Any]:
    """Monta o contexto transitório da página Insights.

    ``filtro`` usa o ID opaco de um item e, portanto, não coloca nomes de
    titulares ou contas na URL. O filtro é aplicado antes de todos os cartões;
    trocar a dimensão mantém a mesma seleção quando a dimensão for compatível.
    """

    dimensao = dimensao_valida(dimensao)
    filtro_dimensao = dimensao_valida(filtro_dimensao) if filtro_dimensao else ""
    ordenar = ordenar if ordenar in ORDENACOES else "valor"
    direcao = "asc" if direcao == "asc" else "desc"
    todas = list(linhas)
    busca_normalizada = busca.strip().casefold()
    if busca_normalizada:
        todas = [linha for linha in todas if busca_normalizada in _valor_para_busca(linha)]

    filtro_nome = ""
    filtro_valido = False
    if filtro and filtro_dimensao:
        for linha in todas:
            nome = nome_da_dimensao(linha, filtro_dimensao)
            if id_item(filtro_dimensao, nome) == filtro:
                filtro_nome = nome
                filtro_valido = True
                break
        if filtro_valido:
            todas = [
                linha
                for linha in todas
                if nome_da_dimensao(linha, filtro_dimensao) == filtro_nome
            ]
        else:
            filtro = ""
            filtro_dimensao = ""

    moedas = sorted({_texto(linha, "moeda") for linha in todas if _texto(linha, "moeda")})
    serie = SerieDeTaxas(moedas) if moedas else None
    itens = _itens(
        todas,
        dimensao=dimensao,
        referencia=referencia,
        serie=serie,
        periodo=periodo,
        data=data,
        filtro_dimensao=filtro_dimensao,
        filtro=filtro,
        busca=busca,
        ordenar=ordenar,
        direcao=direcao,
        grupo=grupo,
        conta=conta,
    )
    total = _conversao(todas, referencia, serie)
    classificaveis = [linha for linha in todas if nome_da_dimensao(linha, dimensao) == NAO_CLASSIFICADO]
    dimensoes_contexto = []
    for chave, nome in DIMENSOES:
        dimensoes_contexto.append(
            {
                "chave": chave,
                "nome": nome,
                "ativa": chave == dimensao,
                "url": _base_url(
                    periodo=periodo,
                    data=data,
                    dimensao=chave,
                    filtro_dimensao=filtro_dimensao,
                    filtro=filtro,
                    busca=busca,
                    ordenar=ordenar,
                    direcao=direcao,
                    grupo=grupo,
                    conta=conta,
                ),
            }
        )
    limpar_url = _base_url(
        periodo=periodo,
        data=data,
        dimensao=dimensao,
        grupo=grupo,
        conta=conta,
    )
    return {
        "dimensoes": dimensoes_contexto,
        "dimensao": dimensao,
        "dimensao_nome": dict(DIMENSOES)[dimensao],
        "itens": itens,
        "detalhe_linhas": [
            {
                "nome": _texto(linha, "descricao") or "Linha sem descrição",
                "fonte": _texto(linha, "fonte") or NAO_INFORMADO,
                "instituicao": _texto(linha, "instituicao") or NAO_INFORMADO,
                "titular": _texto(linha, "titular") or NAO_INFORMADO,
                "moeda": _texto(linha, "moeda") or NAO_INFORMADO,
                "valor": getattr(linha, "valor", ZERO) or ZERO,
                "quantidade": getattr(linha, "quantidade", None),
                "classe": nome_da_dimensao(linha, "classe"),
                "mercado": nome_da_dimensao(linha, "mercado"),
                "setor": nome_da_dimensao(linha, "setor"),
                "regiao": nome_da_dimensao(linha, "regiao"),
                "link": _texto(linha, "link"),
            }
            for linha in sorted(
                todas,
                key=lambda item: (
                    _texto(item, "instituicao").casefold(),
                    _texto(item, "descricao").casefold(),
                    _texto(item, "moeda").casefold(),
                ),
            )
        ],
        "total": total,
        "linhas": len(todas),
        "moedas": moedas,
        "nao_classificadas": len(classificaveis),
        "cobertura_completa": bool(fonte_completa),
        "fontes_que_responderam": quantidade_fontes,
        "fontes_esperadas": quantidade_fontes_esperadas,
        "motivo_fontes": motivo_fontes,
        "filtro_dimensao": filtro_dimensao,
        "filtro": filtro,
        "filtro_nome": filtro_nome,
        "busca": busca,
        "ordenar": ordenar,
        "direcao": direcao,
        "ordenacoes": [{"chave": chave, "nome": nome} for chave, nome in ORDENACOES.items()],
        "limpar_url": limpar_url,
        "tem_classificacao": len(classificaveis) < len(todas),
        "motivo_classificacao": (
            "A fonte não publica este atributo para todas as linhas; elas aparecem em Não classificado."
            if classificaveis
            else ""
        ),
    }
