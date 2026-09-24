"""Montagem, a partir do `Consolidado`, dos dados que as telas mostram.

Cada função aqui é pura em relação às fontes: recebe o consolidado (ou partes
dele) já lido por `leitor.consolidar_v2` e só organiza, soma por moeda e
converte com a taxa da data. Nenhuma consulta às fontes nasce aqui, e nenhum
valor em moedas diferentes é somado sem uma conversão datada.

As funções vieram de `views.py`, onde serviam à tela de patrimônio que o shell
substituiu; o shell é o único consumidor.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

from consolidado import dashboard, fotos, grafico
from consolidado.cambio import converter_totais

PERIODOS_DASHBOARD = {
    "1d": ("1 dia", "1d"),
    "1s": ("1 semana", "1s"),
    "1m": ("1 mês", "1m"),
    "3m": ("3 meses", "3m"),
    "6m": ("6 meses", "6m"),
    "ano": ("Este ano", "ano"),
    "1a": ("1 ano", "1a"),
    "5a": ("5 anos", "5a"),
    "tudo": ("Tudo", None),
}

#: A única natureza de fluxo que é receita ou gasto (ver `resumo_gastos`).
NATUREZA_GERENCIAL = "gerencial"


def inicio_do_periodo(chave: str, referencia: date) -> date | None:
    if chave == "este_mes":
        return referencia.replace(day=1)
    if chave == "mes_passado":
        # A superfície Wealthfolio normaliza a referência para o último dia
        # do mês anterior antes de chegar aqui.
        return referencia.replace(day=1)
    if chave == "1d":
        return referencia - timedelta(days=1)
    if chave == "1s":
        return referencia - timedelta(days=7)
    periodo_analitico = {
        "1m": "1M",
        "3m": "3M",
        "6m": "6M",
        "ano": "YTD",
        "1a": "1Y",
        "5a": "5Y",
        "tudo": "Tudo",
    }[chave]
    inicio, _fim = dashboard.intervalo_periodo(periodo_analitico, referencia)
    return inicio


def valor_do_papel(blocos: list[dict], papel: str, referencia: date) -> Decimal | None:
    bloco = next((item for item in blocos if item["nome"] == papel), None)
    if bloco is None:
        return Decimal("0.00")
    conversao = converter_totais(bloco["totais_por_moeda"], referencia)
    return conversao.total if conversao.possivel else None


def valor_dos_cartoes(linhas: list[Any], referencia: date) -> Decimal | None:
    """A dívida dos cartões de crédito em moeda base (negativa), ou None sem taxa.

    O saldo do cartão já entra no caixa publicado pelo Controle Bancário -- a
    soma do patrimônio não muda. Separá-lo só deixa à vista que parte do caixa
    é, na verdade, fatura a pagar.
    """
    totais: dict[str, Decimal] = {}
    for linha in linhas:
        if getattr(linha, "e_cartao_de_credito", False):
            totais[linha.moeda] = totais.get(linha.moeda, Decimal("0.00")) + linha.valor
    if not totais:
        return Decimal("0.00")
    conversao = converter_totais(
        [{"moeda": moeda, "total": total} for moeda, total in sorted(totais.items())], referencia
    )
    return conversao.total if conversao.possivel else None


def variacao(atual: Decimal | None, pontos: list[fotos.Ponto], atributo: str) -> dict | None:
    if atual is None:
        return None
    primeiro = next(
        (ponto for ponto in pontos if getattr(ponto, atributo) is not None),
        None,
    )
    if primeiro is None:
        return None
    anterior = getattr(primeiro, atributo)
    absoluto = atual - anterior
    return {
        "absoluto": absoluto,
        "percentual": grafico.variacao(atual, anterior),
        "desde": primeiro.data,
    }


def incluir_foto_atual(
    pontos: list[fotos.Ponto],
    data_da_tela: date,
    patrimonio: Decimal | None,
    investimentos: Decimal | None,
) -> list[fotos.Ponto]:
    if patrimonio is None and investimentos is None:
        return pontos
    atuais = [ponto for ponto in pontos if ponto.data < data_da_tela]
    atuais.append(
        fotos.Ponto(
            data=data_da_tela,
            investimentos=investimentos,
            patrimonio=patrimonio,
        )
    )
    return atuais


def desempenhos(consolidado) -> list[dict]:
    resultado = []
    for serie in consolidado.twr:
        pontos = serie.get("pontos") or []
        ultimo = pontos[-1] if pontos else None
        retorno = ultimo.get("retorno_acumulado") if ultimo else None
        resultado.append(
            {
                "moeda": serie.get("moeda", ""),
                "metodo": serie.get("metodo", ""),
                "ultimo": ultimo,
                "retorno_percentual": retorno * 100 if retorno is not None else None,
                "serie_json": json.dumps(
                    [
                        {
                            "data": ponto["data"].isoformat(),
                            "valor": format(ponto["retorno_acumulado"], "f"),
                        }
                        for ponto in pontos
                        if ponto.get("data") is not None
                        and ponto.get("retorno_acumulado") is not None
                    ],
                    ensure_ascii=False,
                ),
                "link": serie.get("link", ""),
            }
        )
    return resultado


def resumo_gastos(fluxos: list[dict]) -> list[dict]:
    """Resume receitas e gastos gerenciais por moeda, sem inventar categorias.

    Só a natureza gerencial é receita ou gasto. Transferência entre contas
    próprias, movimentação (aplicação, liquidação de bolsa) e ajuste de base
    só mudam o dinheiro de bolso ou de forma: somadas, as saídas mostravam o
    dinheiro que circulou (três vezes o gasto real num trimestre), e as
    entradas quase o igualavam.
    """
    grupos: dict[str, dict[str, Decimal | int]] = {}
    for fluxo in fluxos:
        if fluxo.get("natureza") != NATUREZA_GERENCIAL:
            continue
        moeda = str(fluxo.get("moeda") or "")
        grupo = grupos.setdefault(
            moeda,
            {"moeda": moeda, "receitas": Decimal("0"), "gastos": Decimal("0"), "liquido": Decimal("0"), "linhas": 0},
        )
        grupo["receitas"] += fluxo.get("entradas", Decimal("0"))
        grupo["gastos"] += fluxo.get("saidas", Decimal("0"))
        grupo["liquido"] += fluxo.get("liquido", Decimal("0"))
        grupo["linhas"] += int(fluxo.get("linhas", 0) or 0)
    return [grupos[chave] for chave in sorted(grupos)]


def ritmo_mensal(fluxos: list[dict]) -> list[dict]:
    """Agrega o ritmo mensal publicado pelas fontes, preservando moedas."""
    grupos: dict[tuple[date, str], dict[str, Decimal | int]] = {}
    for fluxo in fluxos:
        dia = fluxo.get("data")
        if not isinstance(dia, date):
            continue
        chave = (date(dia.year, dia.month, 1), str(fluxo.get("moeda") or ""))
        grupo = grupos.setdefault(
            chave,
            {"data": chave[0], "moeda": chave[1], "receitas": Decimal("0"), "gastos": Decimal("0"), "liquido": Decimal("0")},
        )
        grupo["receitas"] += fluxo.get("entradas", Decimal("0"))
        grupo["gastos"] += fluxo.get("saidas", Decimal("0"))
        grupo["liquido"] += fluxo.get("liquido", Decimal("0"))
    return [grupos[chave] for chave in sorted(grupos, reverse=True)]


def _id_drilldown(prefixo: str, *partes: object) -> str:
    """Gera um identificador opaco, determinístico e seguro para a URL.

    Os IDs de origem são deliberadamente tratados como dados opacos. O digest
    evita colocar nomes, acentos ou caracteres de uma conta na URL e, ao mesmo
    tempo, faz com que o mesmo item mantenha o mesmo ID entre requisições.
    """
    canonico = "\x1f".join(str(parte or "").strip().casefold() for parte in partes)
    return f"{prefixo}-{sha256(canonico.encode('utf-8')).hexdigest()[:20]}"


def _totais_das_linhas(linhas: list[Any]) -> list[dict[str, Any]]:
    """Soma linhas por moeda sem misturar moedas nem consultar uma fonte."""
    acumulado: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        moeda = str(getattr(linha, "moeda", "") or "")
        bloco = acumulado.setdefault(
            moeda,
            {"moeda": moeda, "total": Decimal("0.00"), "linhas": 0},
        )
        bloco["total"] += getattr(linha, "valor", Decimal("0.00"))
        bloco["linhas"] += 1
    return [acumulado[chave] for chave in sorted(acumulado)]


def _variacao_das_linhas(linhas: list[Any]) -> tuple[Decimal | None, Decimal | None]:
    """Retorna resultado e percentual somente quando a fonte publicou custo."""
    moedas = {str(getattr(linha, "moeda", "") or "") for linha in linhas}
    # Não somamos ganhos em BRL e USD: sem uma conversão datada isso seria um
    # número aparentemente preciso, mas semanticamente inválido.
    if len(moedas) != 1:
        return None, None
    ganhos = [
        getattr(linha, "ganho_nao_realizado", None)
        for linha in linhas
        if getattr(linha, "ganho_nao_realizado", None) is not None
    ]
    custos = [
        getattr(linha, "custo", None)
        for linha in linhas
        if getattr(linha, "custo", None) is not None
    ]
    if not ganhos:
        return None, None
    absoluto = sum(ganhos, Decimal("0"))
    custo_total = sum(custos, Decimal("0"))
    percentual = absoluto / custo_total * 100 if custo_total else None
    return absoluto, percentual


def arvore_de_contas(
    linhas: list[Any],
    *,
    referencia: date,
    visao: str,
    periodo: str,
    data_da_tela: date,
    grupo_parametro: str = "",
    conta_parametro: str = "",
) -> dict[str, Any]:
    """Monta a árvore grupo -> conta -> linha para o drill down server-side.

    A função é pura em relação às fontes: recebe as linhas já consolidadas e
    só organiza/soma os objetos transitórios. Para investimentos, uma conta é
    a instituição/moeda e suas posições ficam como linhas filhas. Para caixa,
    a descrição publicada pela fonte diferencia contas da mesma instituição.
    """

    def url_drilldown(grupo: str | None = None, conta: str | None = None) -> str:
        parametros: list[tuple[str, str]] = [
            ("visao", visao),
            ("periodo", periodo),
            ("data", data_da_tela.isoformat()),
        ]
        if grupo:
            parametros.append(("grupo", grupo))
        if conta:
            parametros.append(("conta", conta))
        return f"?{urlencode(parametros)}"

    def grupo_da_linha(linha: Any) -> tuple[str, str]:
        papel = str(getattr(linha, "papel", "") or "").strip().casefold()
        if papel == "investimento":
            return "papel:investimento", "Renda variável"
        titular = str(getattr(linha, "titular", "") or "").strip()
        return (
            f"titular:{titular.casefold()}" if titular else "titular:sem-titular",
            titular or "Sem titular",
        )

    def conta_da_linha(linha: Any, chave_grupo: str) -> tuple[str, str, str]:
        papel = str(getattr(linha, "papel", "") or "").strip().casefold()
        fonte = str(getattr(linha, "fonte", "") or "").strip()
        instituicao = str(getattr(linha, "instituicao", "") or "").strip()
        moeda = str(getattr(linha, "moeda", "") or "").strip()
        descricao = str(getattr(linha, "descricao", "") or "").strip()
        origem = str(getattr(linha, "id_de_origem", "") or "").strip()
        if papel == "investimento":
            chave = (chave_grupo, fonte, instituicao, moeda)
            nome = instituicao or "Instituição não informada"
            subtitulo = moeda or "Moeda não informada"
        else:
            # A descrição é o nome/identificador publicado da conta. Mantê-la
            # na chave permite duas contas da mesma instituição e titular.
            chave = (chave_grupo, fonte, instituicao, descricao, moeda, origem)
            nome = " · ".join(parte for parte in (instituicao, descricao) if parte)
            nome = nome or "Conta não informada"
            subtitulo = moeda or "Moeda não informada"
        return "\x1f".join(chave), nome, subtitulo

    grupos: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        chave_grupo, nome_grupo = grupo_da_linha(linha)
        grupo = grupos.setdefault(
            chave_grupo,
            {
                "_chave": chave_grupo,
                "nome": nome_grupo,
                "linhas": [],
                "contas": {},
            },
        )
        grupo["linhas"].append(linha)
        chave_conta, nome_conta, subtitulo = conta_da_linha(linha, chave_grupo)
        conta = grupo["contas"].setdefault(
            chave_conta,
            {
                "_chave": chave_conta,
                "nome": nome_conta,
                "subtitulo": subtitulo,
                "linhas": [],
            },
        )
        conta["linhas"].append(linha)

    arvore: list[dict[str, Any]] = []
    por_grupo: dict[str, dict[str, Any]] = {}
    por_conta: dict[str, dict[str, Any]] = {}
    por_linha: dict[str, dict[str, Any]] = {}

    # Primeiro resolve a seleção contra as chaves reais. Assim um ID velho ou
    # inventado na URL nunca expande um item que não veio do consolidado atual.
    grupo_selecionado = grupo_parametro if grupo_parametro in {
        _id_drilldown("grupo", chave) for chave in grupos
    } else ""

    for grupo_bruto in sorted(grupos.values(), key=lambda item: (item["nome"].casefold(), item["_chave"])):
        grupo_id = _id_drilldown("grupo", grupo_bruto["_chave"])
        if grupo_id == grupo_selecionado:
            grupo_selecionado = grupo_id
        contas: list[dict[str, Any]] = []
        for conta_bruta in sorted(
            grupo_bruto["contas"].values(),
            key=lambda item: (item["nome"].casefold(), item["subtitulo"].casefold(), item["_chave"]),
        ):
            conta_id = _id_drilldown("conta", conta_bruta["_chave"])
            linhas_novas: list[dict[str, Any]] = []
            ocorrencias: dict[str, int] = {}
            for linha in sorted(
                conta_bruta["linhas"],
                key=lambda item: (
                    str(getattr(item, "descricao", "") or "").casefold(),
                    str(getattr(item, "id_de_origem", "") or "").casefold(),
                    str(getattr(item, "moeda", "") or "").casefold(),
                ),
            ):
                chave_linha = "\x1f".join(
                    str(getattr(linha, campo, "") or "")
                    for campo in ("fonte", "papel", "titular", "instituicao", "descricao", "moeda", "id_de_origem")
                )
                ocorrencia = ocorrencias.get(chave_linha, 0)
                ocorrencias[chave_linha] = ocorrencia + 1
                linha_id = _id_drilldown("linha", chave_linha, ocorrencia)
                linha_node = {
                    "id": linha_id,
                    "tipo": "linha",
                    "nome": getattr(linha, "descricao", "") or "Linha sem descrição",
                    "descricao": getattr(linha, "descricao", "") or "",
                    "moeda": getattr(linha, "moeda", "") or "",
                    "valor": getattr(linha, "valor", Decimal("0.00")),
                    "quantidade": getattr(linha, "quantidade", None),
                    "fonte": getattr(linha, "fonte", "") or "",
                    "instituicao": getattr(linha, "instituicao", "") or "",
                    "titular": getattr(linha, "titular", "") or "",
                    "papel": getattr(linha, "papel", "") or "",
                    "id_de_origem": getattr(linha, "id_de_origem", "") or "",
                    "link": getattr(linha, "link", "") or "",
                }
                linhas_novas.append(linha_node)
                por_linha[linha_id] = linha_node
            conversao_conta = converter_totais(_totais_das_linhas(conta_bruta["linhas"]), referencia)
            variacao_absoluta, variacao_percentual = _variacao_das_linhas(conta_bruta["linhas"])
            links_de_origem = sorted({
                str(getattr(linha, "link", "") or "")
                for linha in conta_bruta["linhas"]
                if getattr(linha, "link", "")
            })
            conta_node = {
                "id": conta_id,
                "tipo": "conta",
                "nome": conta_bruta["nome"],
                "subtitulo": conta_bruta["subtitulo"],
                "moeda": conta_bruta["subtitulo"],
                "linhas": linhas_novas,
                "quantidade": len(linhas_novas),
                "totais_por_moeda": _totais_das_linhas(conta_bruta["linhas"]),
                "conversao": conversao_conta,
                "variacao_absoluta": variacao_absoluta,
                "variacao_percentual": variacao_percentual,
                "url_origem": links_de_origem[0] if len(links_de_origem) == 1 else "",
                # A child row is an actionable account drill-down.  It must
                # always carry its own account id; omitting it for the
                # currently unselected account turns every child link into a
                # group-only URL and the dashboard appears to reload without
                # showing that account's published lines.
                "url": url_drilldown(grupo_id, conta_id),
                "expandida": conta_id == conta_parametro and grupo_id == grupo_selecionado,
            }
            contas.append(conta_node)
            por_conta[conta_id] = conta_node

        conversao_grupo = converter_totais(_totais_das_linhas(grupo_bruto["linhas"]), referencia)
        variacao_absoluta, variacao_percentual = _variacao_das_linhas(grupo_bruto["linhas"])
        grupo_node = {
            "id": grupo_id,
            "tipo": "grupo",
            "nome": grupo_bruto["nome"],
            "contas": contas,
            "quantidade": len(contas),
            "linhas": sum(conta["quantidade"] for conta in contas),
            "totais_por_moeda": _totais_das_linhas(grupo_bruto["linhas"]),
            "moeda": next(iter({str(getattr(linha, "moeda", "") or "") for linha in grupo_bruto["linhas"]}), ""),
            "conversao": conversao_grupo,
            "variacao_absoluta": variacao_absoluta,
            "variacao_percentual": variacao_percentual,
            "url": url_drilldown(None if grupo_id == grupo_selecionado else grupo_id, None),
            "expandida": grupo_id == grupo_selecionado,
            "solitario": grupo_bruto["_chave"].startswith("titular:") and len(contas) == 1,
        }
        arvore.append(grupo_node)
        por_grupo[grupo_id] = grupo_node

    # Conta sem grupo pode ser aberta diretamente; a URL final sempre passa a
    # carregar o grupo correto, evitando uma seleção ambígua.
    conta_selecionada = conta_parametro if conta_parametro in por_conta else ""
    if conta_selecionada:
        grupo_do_conta = next(
            (grupo["id"] for grupo in arvore if any(conta["id"] == conta_selecionada for conta in grupo["contas"])),
            "",
        )
        if grupo_do_conta and not grupo_selecionado:
            grupo_selecionado = grupo_do_conta
            por_grupo[grupo_do_conta]["expandida"] = True
            por_conta[conta_selecionada]["expandida"] = True
        elif grupo_do_conta != grupo_selecionado:
            conta_selecionada = ""

    return {
        "grupos": arvore,
        "por_grupo": por_grupo,
        "por_conta": por_conta,
        "por_linha": por_linha,
        "grupo_selecionado": grupo_selecionado,
        "conta_selecionada": conta_selecionada,
        "url_recolher": url_drilldown(),
    }
