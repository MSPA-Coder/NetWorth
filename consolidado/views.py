"""A tela que responde "quanto eu tenho".

Ela mostra os totais **por moeda**, e não um número só: somar reais com dólares
exige uma taxa, e taxa é decisão datada — ela entra na etapa do câmbio, com data
e fonte visíveis em cada número convertido. Até lá, dois números certos valem
mais que um número redondo e errado.

E ela nunca mostra um total sem dizer de quantas fontes ele é feito. O estado de
cada fonte vem no mesmo objeto que os totais, de propósito: é a única defesa
contra o defeito que importa aqui — o patrimônio "cair" porque um dos sistemas
estava reiniciando, com o número continuando plausível.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from consolidado import dashboard, fotos, grafico, insights
from consolidado.cambio import MOEDA_BASE, converter_totais
from consolidado.leitor import consolidar_v2

#: Os recortes da tela de histórico. A chave vai na URL; o valor diz de quando
#: a curva começa (`None` é a história inteira).
PERIODOS = {
    "tudo": ("Tudo", None),
    "desde-2026": ("Desde 2026", fotos.INICIO_DO_PATRIMONIO),
    "12-meses": ("12 meses", "12m"),
}

PERIODOS_DASHBOARD = {
    "1m": ("1 mês", "1m"),
    "3m": ("3 meses", "3m"),
    "ano": ("Este ano", "ano"),
    "1a": ("1 ano", "1a"),
    "5a": ("5 anos", "5a"),
    "tudo": ("Tudo", None),
}

ROTULOS_DO_PAPEL = {"caixa": "Caixa", "investimento": "Investimentos"}
CASAS_DO_PERCENTUAL = Decimal("0.01")


def _inicio_do_periodo(chave: str, referencia: date) -> date | None:
    periodo_analitico = {
        "1m": "1M",
        "3m": "3M",
        "ano": "YTD",
        "1a": "1Y",
        "5a": "5Y",
        "tudo": "Tudo",
    }[chave]
    inicio, _fim = dashboard.intervalo_periodo(periodo_analitico, referencia)
    return inicio


def _valor_do_papel(blocos: list[dict], papel: str, referencia: date) -> Decimal | None:
    bloco = next((item for item in blocos if item["nome"] == papel), None)
    if bloco is None:
        return Decimal("0.00")
    conversao = converter_totais(bloco["totais_por_moeda"], referencia)
    return conversao.total if conversao.possivel else None


def _variacao(atual: Decimal | None, pontos: list[fotos.Ponto], atributo: str) -> dict | None:
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


def _incluir_foto_atual(
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


def _desempenhos(consolidado) -> list[dict]:
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
                "link": serie.get("link", ""),
            }
        )
    return resultado


def _cartoes(blocos: list[dict], referencia: date, rotulos: dict[str, str] | None = None) -> list[dict]:
    """Dá a cada agrupamento seus totais honestos e, quando dá, sua conversão.

    A regra de câmbio é a mesma do cartão principal. Assim, um cartão por
    instituição não troca dólares por reais sem dizer qual taxa permitiu isso.
    """
    cartoes = []
    for bloco in blocos:
        nome = rotulos.get(bloco["nome"], bloco["nome"]) if rotulos else bloco["nome"]
        cartoes.append(
            {
                "nome": nome,
                "totais_por_moeda": bloco["totais_por_moeda"],
                "conversao": converter_totais(bloco["totais_por_moeda"], referencia),
            }
        )
    return cartoes


def _composicao(blocos: list[dict], referencia: date, total: Decimal | None) -> list[dict]:
    """Fatias em moeda base que fecham exatamente 100%.

    Só é chamada depois que o consolidado inteiro pôde ser convertido. Arredondar
    cada fatia sem compensação faria barras que somam 99,99% ou 100,01%, uma
    discrepância pequena que dá a impressão errada de que algo ficou de fora.
    """
    if total is None or not total:
        return []
    fatias = []
    for bloco in blocos:
        conversao = converter_totais(bloco["totais_por_moeda"], referencia)
        if not conversao.possivel:
            return []
        fatias.append({"nome": bloco["nome"], "total": conversao.total})
    fatias.sort(key=lambda fatia: (-fatia["total"], fatia["nome"]))
    for fatia in fatias:
        fatia["percentual"] = (fatia["total"] * 100 / total).quantize(
            CASAS_DO_PERCENTUAL, rounding=ROUND_HALF_UP
        )
    fatias[0]["percentual"] += Decimal("100.00") - sum(
        fatia["percentual"] for fatia in fatias
    )
    return fatias


def _resumo_gastos(fluxos: list[dict]) -> list[dict]:
    """Resume entradas e saídas por moeda, sem inventar categorias.

    O contrato v2 publica fluxos agregados por natureza; portanto a visão de
    gastos apresenta o mesmo nível de detalhe, mantendo transferências e
    ajustes separados do dinheiro gerencial.
    """
    grupos: dict[str, dict[str, Decimal | int]] = {}
    for fluxo in fluxos:
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


def _ritmo_mensal(fluxos: list[dict]) -> list[dict]:
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


def _arvore_de_contas(
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
                "url": url_drilldown(grupo_id, None if conta_id == conta_parametro else conta_id),
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


@login_required
def patrimonio_view(request):
    bruto = (request.GET.get("data") or "").strip()
    referencia = None
    data_invalida = False
    if bruto:
        try:
            referencia = date.fromisoformat(bruto)
        except ValueError:
            data_invalida = True

    visao = request.GET.get("visao") or "investimentos"
    if visao not in {"investimentos", "patrimonio", "gastos", "insights"}:
        visao = "investimentos"
    periodo = request.GET.get("periodo") or "1a"
    if periodo not in PERIODOS_DASHBOARD:
        periodo = "1a"
    grupo_parametro = (request.GET.get("grupo") or "").strip()
    conta_parametro = (request.GET.get("conta") or "").strip()
    dimensao_parametro = (request.GET.get("dimensao") or "classe").strip()
    filtro_dimensao_parametro = (request.GET.get("filtro_dimensao") or "").strip()
    filtro_parametro = (request.GET.get("filtro") or "").strip()
    busca_parametro = (request.GET.get("busca") or "").strip()
    ordenar_parametro = (request.GET.get("ordenar") or "valor").strip()
    direcao_parametro = (request.GET.get("direcao") or "desc").strip()

    data_da_tela = referencia or timezone.localdate()
    inicio = _inicio_do_periodo(periodo, data_da_tela)
    inicio_da_consulta = inicio or fotos.INICIO_DOS_INVESTIMENTOS
    consolidado = consolidar_v2(
        inicio=inicio_da_consulta,
        data=data_da_tela,
        periodo="all",
    )
    # A taxa é a do dia da foto, não a de hoje: converter o passado pela taxa de
    # hoje faria o patrimônio de março mudar toda manhã.
    conversao = converter_totais(consolidado.totais_por_moeda, referencia or timezone.localdate())
    por_sistema = consolidado.por("papel")
    por_instituicao = consolidado.por("instituicao")
    por_mercado = consolidado.por_mercado()
    por_classe = consolidado.por("classe", "Caixa e não classificados")
    composicao_destaque = (
        _composicao(por_classe, data_da_tela, conversao.total)
        if conversao.possivel
        else []
    )
    pontos = [
        ponto
        for ponto in fotos.curvas(desde=inicio)
        if ponto.data <= data_da_tela
    ]
    patrimonio_atual = (
        conversao.total if consolidado.completo and conversao.possivel else None
    )
    investimentos_atuais = (
        _valor_do_papel(por_sistema, "investimento", data_da_tela)
        if consolidado.completo
        else None
    )
    pontos = _incluir_foto_atual(
        pontos,
        data_da_tela,
        patrimonio_atual,
        investimentos_atuais,
    )
    desenho = grafico.montar(
        pontos,
        (
            ("patrimonio", "Patrimônio", "curva-patrimonio"),
            ("investimentos", "Investimentos", "curva-investimentos"),
        ),
    )
    fluxos_por_natureza = dashboard.resumo_fluxos_por_natureza(consolidado)
    resumo_gastos = _resumo_gastos(fluxos_por_natureza)
    ritmo_mensal = _ritmo_mensal(consolidado.fluxos)
    detalhamento_patrimonio = []
    if conversao.possivel:
        for papel, nome in (("investimento", "Investimentos"), ("caixa", "Caixa")):
            valor = _valor_do_papel(por_sistema, papel, data_da_tela)
            if valor is not None:
                detalhamento_patrimonio.append(
                    {
                        "nome": nome,
                        "valor": valor,
                        "percentual": (valor * 100 / conversao.total) if conversao.total else Decimal("0"),
                    }
                )
    arvore_contas = _arvore_de_contas(
        consolidado.linhas,
        referencia=data_da_tela,
        visao=visao,
        periodo=periodo,
        data_da_tela=data_da_tela,
        grupo_parametro=grupo_parametro,
        conta_parametro=conta_parametro,
    )
    insights_contexto = None
    if visao == "insights":
        insights_contexto = insights.montar(
            consolidado.linhas,
            referencia=data_da_tela,
            periodo=periodo,
            data=data_da_tela.isoformat(),
            dimensao=dimensao_parametro,
            filtro_dimensao=filtro_dimensao_parametro,
            filtro=filtro_parametro,
            busca=busca_parametro,
            ordenar=ordenar_parametro,
            direcao=direcao_parametro,
            grupo=arvore_contas["grupo_selecionado"],
            conta=arvore_contas["conta_selecionada"],
            fonte_completa=consolidado.completo,
            quantidade_fontes=len(consolidado.fontes_que_responderam),
            quantidade_fontes_esperadas=len(consolidado.leituras),
            motivo_fontes=(
                "; ".join(
                    f"{leitura.fonte.nome}: {leitura.motivo or ', '.join(leitura.lacunas)}"
                    for leitura in consolidado.leituras
                    if not leitura.respondeu or leitura.lacunas
                )
            ),
        )
    return render(
        request,
        "consolidado/patrimonio.html",
        {
            "consolidado": consolidado,
            "conversao": conversao,
            "moeda_base": MOEDA_BASE,
            "referencia": referencia,
            "visao": visao,
            "insights": insights_contexto,
            "data_da_tela": data_da_tela,
            "data_invalida": data_invalida,
            "por_instituicao": consolidado.por_instituicao(),
            "grafico": desenho,
            "primeira_data": pontos[0].data if pontos else None,
            "ultima_data": pontos[-1].data if pontos else None,
            "periodos_dashboard": [
                (valor, rotulo)
                for valor, (rotulo, _recorte) in PERIODOS_DASHBOARD.items()
            ],
            "periodo": periodo,
            "top_posicoes": dashboard.top_posicoes(consolidado, limite=10),
            "fluxos_por_natureza": fluxos_por_natureza,
            "resumo_gastos": resumo_gastos,
            "ritmo_mensal": ritmo_mensal,
            "detalhamento_patrimonio": detalhamento_patrimonio,
            # A árvore é transitória e nasce do mesmo consolidado já usado pelo
            # restante da tela. Templates podem usar ``grupos`` ou os índices
            # por ID sem precisar refazer agrupamentos ou consultar fontes.
            "arvore_contas": arvore_contas["grupos"],
            "arvore_drilldown": arvore_contas,
            "grupos_drilldown": arvore_contas["grupos"],
            "grupo_selecionado": arvore_contas["grupo_selecionado"],
            "conta_selecionada": arvore_contas["conta_selecionada"],
            "url_drilldown_recolher": arvore_contas["url_recolher"],
            # Nomes compatíveis com o template da árvore e com a futura camada
            # de nós unificados. O detalhe sempre vem do mesmo consolidado.
            "cartoes_de_contas": arvore_contas["grupos"],
            "grupo_aberto": arvore_contas["grupo_selecionado"],
            "conta_aberta": arvore_contas["conta_selecionada"],
            "detalhe_conta": arvore_contas["por_conta"].get(
                arvore_contas["conta_selecionada"]
            ),
            "rendas": consolidado.rendas,
            "ganhos_realizados": consolidado.ganhos_realizados,
            "desempenhos": _desempenhos(consolidado),
            "links_de_secao": consolidado.secoes,
            "qualidade_v2": consolidado.qualidade,
            "composicao_destaque": composicao_destaque,
            "variacao_patrimonio": _variacao(patrimonio_atual, pontos[:-1], "patrimonio"),
            "variacao_investimentos": _variacao(
                investimentos_atuais, pontos[:-1], "investimentos"
            ),
            "cartoes_por_sistema": _cartoes(por_sistema, data_da_tela, ROTULOS_DO_PAPEL),
            "cartoes_por_instituicao": _cartoes(por_instituicao, data_da_tela),
            "composicoes": (
                [
                    ("Por moeda", _composicao(
                        [
                            {"nome": total["moeda"], "totais_por_moeda": [total]}
                            for total in consolidado.totais_por_moeda
                        ],
                        data_da_tela,
                        conversao.total,
                    )),
                    ("Por sistema", _composicao(por_sistema, data_da_tela, conversao.total)),
                    ("Por instituição", _composicao(
                        por_instituicao, data_da_tela, conversao.total
                    )),
                    ("Por mercado", _composicao(por_mercado, data_da_tela, conversao.total)),
                    ("Por classe", _composicao(por_classe, data_da_tela, conversao.total)),
                ]
                if conversao.possivel
                else []
            ),
        },
    )


@login_required
def historico_view(request):
    """As duas curvas, a partir das fotos gravadas. Nenhuma fonte é consultada."""
    chave = request.GET.get("periodo") or "tudo"
    if chave not in PERIODOS:
        chave = "tudo"
    inicio = PERIODOS[chave][1]
    if inicio == "12m":
        inicio = timezone.localdate() - timedelta(days=365)

    pontos = fotos.curvas(desde=inicio)
    desenho = grafico.montar(
        pontos,
        (
            ("patrimonio", "Patrimônio", "curva-patrimonio"),
            ("investimentos", "Investimentos", "curva-investimentos"),
        ),
    )
    mensais = grafico.ultimo_de_cada_mes(pontos)
    linhas = []
    for indice, ponto in enumerate(mensais):
        anterior = mensais[indice + 1] if indice + 1 < len(mensais) else None
        linhas.append(
            {
                "ponto": ponto,
                "variacao_investimentos": grafico.variacao(
                    ponto.investimentos, anterior.investimentos if anterior else None
                ),
                "variacao_patrimonio": grafico.variacao(
                    ponto.patrimonio, anterior.patrimonio if anterior else None
                ),
            }
        )
    return render(
        request,
        "consolidado/historico.html",
        {
            "grafico": desenho,
            "primeira_data": pontos[0].data if pontos else None,
            "ultima_data": pontos[-1].data if pontos else None,
            # Desde o início do caixa, patrimônio vazio só pode ser falta de taxa.
            "sem_taxa": sum(
                1
                for ponto in pontos
                if ponto.data >= fotos.INICIO_DO_PATRIMONIO and ponto.patrimonio is None
            ),
            "mensais": linhas,
            "moeda_base": MOEDA_BASE,
            "periodos": [(valor, rotulo) for valor, (rotulo, _inicio) in PERIODOS.items()],
            "periodo": chave,
            "inicio_do_patrimonio": fotos.INICIO_DO_PATRIMONIO,
        },
    )
