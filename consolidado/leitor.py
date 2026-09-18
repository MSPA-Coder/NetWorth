"""Lê o resumo publicado por cada fonte e diz, sem rodeio, o que conseguiu ler.

A REGRA QUE SUSTENTA A CONFIANÇA NA TELA

Uma fonte fora do ar **não pode produzir um número menor sem dizer**. É o pior
defeito possível neste aplicativo: o patrimônio "caiu" trinta por cento porque o
Renda Variável estava reiniciando, e ninguém percebe — o número continua
plausível, e é justamente por isso que ninguém desconfia.

Daí a forma deste módulo: ele nunca devolve só números. Devolve `Consolidado`,
que carrega o estado de **cada** fonte, e a tela é obrigada a passar por ele
para chegar aos totais. Não existe caminho neste código que produza um total
sem produzir, junto, de quantas fontes ele é feito.

O QUE ELE NÃO FAZ

Não recalcula nada. Saldo é do Controle Bancário, valor a mercado é do Controle
de Renda Variável. Este módulo transporta e soma **dentro de cada moeda**;
converter entre moedas é outra decisão, tomada com a série de câmbio datada.

E não confia no que chega: o que vem pela rede é dado, não instrução. Todo valor
monetário é convertido para `Decimal` a partir de texto, e uma linha malformada
derruba a fonte inteira para o estado "respondeu errado" em vez de virar um
total silenciosamente torto.

O LINK DE CADA LINHA

Cada fonte publica, em `endereco`, o caminho da tela onde a linha se explica.
O caminho é dela -- este módulo não o deduz do id, que é opaco -- e só é aceito
relativo à raiz da própria fonte: começa com uma barra, e só uma. Qualquer
outra coisa (um endereço absoluto, `//outro-lugar`, um `javascript:`) viraria
um link para fora dos dois sistemas, e é descartada. Um link ruim não muda
número nenhum, então ele some sozinho, sem derrubar a fonte.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from consolidado.fontes import Fonte, fontes_configuradas, fontes_incompletas

# v1 remains the wire format used by the first deployment.  v2 is deliberately
# additive: the basic account/position rows have the same meaning, while the
# publisher may append analytical data (flows, performance and quality).
CONTRATO_ACEITO = "patrimonio/v1"
CONTRATOS_ACEITOS = frozenset({"patrimonio/v1", "patrimonio/v2"})
CONTRATO_V2 = "patrimonio/v2"
TEMPO_LIMITE_SEGUNDOS = 8
TAMANHO_MAXIMO_BYTES = 8 * 1024 * 1024

logger = logging.getLogger(__name__)

OK = "ok"
FALHOU = "falhou"
NAO_RESPONDEU = "nao_respondeu"


@dataclass(frozen=True, slots=True)
class Linha:
    """Uma coisa que vale dinheiro, seja ela conta ou posição."""

    fonte: str
    papel: str
    titular: str
    instituicao: str
    descricao: str
    moeda: str
    valor: Decimal
    id_de_origem: str = ""
    detalhe: str = ""
    link: str = ""
    classe: str = ""
    mercado: str = ""
    quantidade: Decimal | None = None
    # v2 enrichment.  These are optional because publishers are deployed
    # independently and an old response must remain useful during rollout.
    natureza: str = ""
    categoria: str = ""
    tipo: str = ""
    custo: Decimal | None = None
    exposicao_bruta: Decimal | None = None
    ganho_realizado: Decimal | None = None
    renda: Decimal | None = None
    preco: Decimal | None = None
    custo_medio: Decimal | None = None
    ganho_nao_realizado: Decimal | None = None
    peso: Decimal | None = None
    retorno: Decimal | None = None
    volatilidade: Decimal | None = None
    secao: str = ""
    qualidade: str = ""
    """Endereço da tela de origem, ou vazio quando a fonte não indicou uma."""

    @property
    def instrumento(self) -> str:
        return self.descricao

    @property
    def valor_bruto(self) -> Decimal:
        return self.exposicao_bruta if self.exposicao_bruta is not None else abs(self.valor)

    @property
    def resultado_realizado(self) -> Decimal | None:
        return self.ganho_realizado


@dataclass
class Leitura:
    """O que uma fonte respondeu, ou por que não respondeu."""

    fonte: Fonte
    estado: str = NAO_RESPONDEU
    motivo: str = ""
    data_de_referencia: date | None = None
    linhas: list[Linha] = field(default_factory=list)
    lacunas: list[str] = field(default_factory=list)
    # Transitórios v2.  They intentionally stay as dictionaries: the source
    # owns their domain and NetWorth only validates numbers and dates before
    # aggregating them.  Nothing here is persisted.
    fluxos: list[dict[str, Any]] = field(default_factory=list)
    ganhos_realizados: list[dict[str, Any]] = field(default_factory=list)
    rendas: list[dict[str, Any]] = field(default_factory=list)
    twr: list[dict[str, Any]] = field(default_factory=list)
    qualidade: dict[str, Any] | list[Any] | None = None
    secoes: list[dict[str, Any]] = field(default_factory=list)
    inicio: date | None = None
    data_de_fim: date | None = None
    """O que a fonte respondeu que deixou de fora por não conseguir avaliar.

    A fonte respondeu, e o que veio está certo -- mas não é tudo. Uma posição
    sem cotação que some do total faz o patrimônio encolher com cara de
    completo, então a lacuna torna o consolidado incompleto, como uma fonte
    fora do ar.
    """

    @property
    def respondeu(self) -> bool:
        return self.estado == OK

    @property
    def data(self) -> date | None:
        return self.data_de_fim or self.data_de_referencia

    @property
    def movimentos(self) -> list[dict[str, Any]]:
        """Alias used by a few v2 publisher builds for cash flows."""
        return self.fluxos

    @property
    def performance(self) -> list[dict[str, Any]]:
        return self.twr

    @property
    def posicoes(self) -> list[Linha]:
        return [linha for linha in self.linhas if linha.papel == "investimento"]


@dataclass
class Consolidado:
    """Totais por moeda **e** de quantas fontes eles são feitos.

    As duas coisas vêm juntas de propósito. Separá-las permitiria mostrar o
    total sem mostrar que ele está incompleto, e é essa a falha que este
    aplicativo não pode ter.
    """

    leituras: list[Leitura] = field(default_factory=list)
    incompletas: list[str] = field(default_factory=list)

    @property
    def linhas(self) -> list[Linha]:
        return [linha for leitura in self.leituras for linha in leitura.linhas]

    @property
    def fontes_que_responderam(self) -> list[Leitura]:
        return [leitura for leitura in self.leituras if leitura.respondeu]

    @property
    def completo(self) -> bool:
        """Todas as fontes conhecidas responderam, por inteiro, e nenhuma está
        configurada pela metade."""
        return (
            bool(self.leituras)
            and not self.incompletas
            and all(leitura.respondeu and not leitura.lacunas for leitura in self.leituras)
        )

    @property
    def totais_por_moeda(self) -> list[dict]:
        """Uma linha por moeda. Nunca somadas entre si -- converter é decidir."""
        acumulado: dict[str, dict] = {}
        for linha in self.linhas:
            bloco = acumulado.setdefault(
                linha.moeda, {"moeda": linha.moeda, "total": Decimal("0.00"), "linhas": 0}
            )
            bloco["total"] += linha.valor
            bloco["linhas"] += 1
        return [acumulado[moeda] for moeda in sorted(acumulado)]

    def por_instituicao(self) -> list[dict]:
        acumulado: dict[tuple[str, str], dict] = {}
        for linha in self.linhas:
            chave = (linha.instituicao, linha.moeda)
            bloco = acumulado.setdefault(
                chave,
                {
                    "instituicao": linha.instituicao,
                    "moeda": linha.moeda,
                    "total": Decimal("0.00"),
                    "linhas": [],
                },
            )
            bloco["total"] += linha.valor
            bloco["linhas"].append(linha)
        return [acumulado[chave] for chave in sorted(acumulado)]

    def por(self, atributo: str, rotulo_vazio: str = "Não informado") -> list[dict]:
        """Agrupa linhas para os cartões, sem transformar moedas em uma só.

        A conversão continua com quem chama. Agrupar primeiro e converter
        depois evita que um cartão em reais pareça conter um valor em dólar
        que ainda não pode ser convertido.
        """
        acumulado: dict[str, dict] = {}
        for linha in self.linhas:
            valor = getattr(linha, atributo) or rotulo_vazio
            bloco = acumulado.setdefault(valor, {"nome": valor, "linhas": []})
            bloco["linhas"].append(linha)
        for bloco in acumulado.values():
            bloco["totais_por_moeda"] = _totais_por_moeda(bloco["linhas"])
        return [acumulado[chave] for chave in sorted(acumulado)]

    def por_mercado(self) -> list[dict]:
        """Agrupa B3, EUA e caixa sem inventar mercado para uma posição.

        O caixa não é posição, mas é uma fatia útil da composição. Já uma
        posição antiga sem `mercado` fica explicitamente não informada, para
        não ser classificada pelo consolidado.
        """
        acumulado: dict[str, dict] = {}
        for linha in self.linhas:
            mercado = linha.mercado or ("Caixa" if linha.papel == "caixa" else "Não informado")
            bloco = acumulado.setdefault(mercado, {"nome": mercado, "linhas": []})
            bloco["linhas"].append(linha)
        for bloco in acumulado.values():
            bloco["totais_por_moeda"] = _totais_por_moeda(bloco["linhas"])
        return [acumulado[chave] for chave in sorted(acumulado)]

    @property
    def fluxos(self) -> list[dict[str, Any]]:
        return [item for leitura in self.leituras for item in leitura.fluxos]

    @property
    def ganhos_realizados(self) -> list[dict[str, Any]]:
        return [item for leitura in self.leituras for item in leitura.ganhos_realizados]

    @property
    def rendas(self) -> list[dict[str, Any]]:
        return [item for leitura in self.leituras for item in leitura.rendas]

    @property
    def twr(self) -> list[dict[str, Any]]:
        return [item for leitura in self.leituras for item in leitura.twr]

    @property
    def qualidade(self) -> list[Any]:
        return [leitura.qualidade for leitura in self.leituras if leitura.qualidade is not None]

    @property
    def secoes(self) -> list[dict[str, Any]]:
        return [item for leitura in self.leituras for item in leitura.secoes]

    @property
    def ganhos(self) -> list[dict[str, Any]]:
        return self.ganhos_realizados

    @property
    def performance(self) -> list[dict[str, Any]]:
        return self.twr

    @property
    def posicoes(self) -> list[Linha]:
        return [linha for linha in self.linhas if linha.papel == "investimento"]


def _para_decimal(bruto, onde: str) -> Decimal:
    if not isinstance(bruto, str):
        # O contrato manda texto justamente para isto: `float` não representa
        # 0,10, e aceitar número aqui deixaria a imprecisão entrar em silêncio.
        raise ValueError(f"{onde}: valor monetário tem de vir como texto, veio {type(bruto).__name__}")
    try:
        resultado = Decimal(bruto)
    except InvalidOperation as erro:
        raise ValueError(f"{onde}: {bruto!r} não é um número") from erro
    if not resultado.is_finite():
        raise ValueError(f"{onde}: {bruto!r} não é um número finito")
    return resultado


def _texto(dados: dict, chave: str, onde: str) -> str:
    valor = dados.get(chave)
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{onde}: falta '{chave}'")
    return valor.strip()


def _texto_opcional(dados: dict, chave: str, onde: str) -> str:
    """Um texto opcional, ainda assim validado quando o publicador o envia."""
    valor = dados.get(chave)
    if valor is None or valor == "":
        return ""
    if not isinstance(valor, str):
        raise ValueError(f"{onde}: '{chave}' tem de ser texto")
    return valor.strip()


def _decimal_opcional(dados: dict, chave: str, onde: str) -> Decimal | None:
    valor = dados.get(chave)
    if valor is None or valor == "":
        return None
    return _para_decimal(valor, f"{onde}.{chave}")


def _data_opcional(dados: dict, chaves: tuple[str, ...], onde: str) -> date | None:
    """Parse an ISO date from one of the names used by v2 publishers."""
    valor = next((dados.get(chave) for chave in chaves if dados.get(chave) is not None), None)
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise ValueError(f"{onde}: data tem de vir como texto")
    try:
        # Date-times are accepted for observations, while retaining only the
        # publisher's calendar day for grouping.  Plain dates remain strict.
        return date.fromisoformat(valor[:10])
    except ValueError as erro:
        raise ValueError(f"{onde}: {valor!r} não é uma data ISO") from erro


def _valor_em(dados: dict, chaves: tuple[str, ...], onde: str, *, obrigatorio: bool = True):
    for chave in chaves:
        if chave in dados:
            bruto = dados[chave]
            if bruto is None or bruto == "":
                if obrigatorio:
                    raise ValueError(f"{onde}.{chave}: valor não pode ser vazio")
                return None
            return _para_decimal(bruto, f"{onde}.{chave}")
    if obrigatorio:
        raise ValueError(f"{onde}: falta um valor ({'/'.join(chaves)})")
    return None


def _lista(corpo: dict, chaves: tuple[str, ...], onde: str) -> list:
    """Return an optional v2 collection without silently accepting a scalar."""
    valor = next((corpo[chave] for chave in chaves if chave in corpo), [])
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise ValueError(f"{onde}: '{chaves[0]}' tem de ser uma lista")
    return valor


def _registro_analitico(
    bruto: Any,
    *,
    onde: str,
    valor_chaves: tuple[str, ...] = ("valor", "total", "amount"),
    data_obrigatoria: bool = False,
) -> dict[str, Any]:
    """Validate one non-positional v2 record and return a normalized mapping.

    Extra fields are retained for a drill-down, but numeric fields known to be
    monetary are parsed here.  This prevents a publisher's JSON number from
    entering an aggregation unnoticed while keeping the wire contract
    extensible.
    """
    if not isinstance(bruto, dict):
        raise ValueError(f"{onde}: item tem de ser um objeto")
    item = dict(bruto)
    dia = _data_opcional(
        item,
        ("data", "data_de_referencia", "date", "dia", "em", "fim", "data_fim"),
        onde,
    )
    if data_obrigatoria and dia is None:
        raise ValueError(f"{onde}: falta 'data'")
    if dia is not None:
        item["data"] = dia
    moeda = item.get("moeda") or item.get("currency")
    if moeda is not None:
        if not isinstance(moeda, str) or not moeda.strip():
            raise ValueError(f"{onde}: moeda tem de ser texto não vazio")
        item["moeda"] = moeda.strip()
    elif valor_chaves:
        raise ValueError(f"{onde}: falta 'moeda'")
    valor = _valor_em(item, valor_chaves, onde, obrigatorio=bool(valor_chaves))
    if valor is not None:
        item["valor"] = valor
    return item


def _secoes_v2(corpo: dict, fonte: Fonte) -> list[dict[str, Any]]:
    bruto = corpo.get(
        "secoes",
        corpo.get(
            "enderecos",
            corpo.get("links_secoes", corpo.get("section_links", corpo.get("links", []))),
        ),
    )
    if bruto is None:
        return []
    if isinstance(bruto, dict):
        bruto = [
            ({"id": chave, "nome": chave, **valor} if isinstance(valor, dict)
             else {"id": chave, "nome": chave, "endereco": valor})
            for chave, valor in bruto.items()
        ]
    if not isinstance(bruto, list):
        raise ValueError("secoes: tem de ser uma lista ou objeto")
    resultado = []
    for indice, secao in enumerate(bruto):
        onde = f"{fonte.nome}/secoes[{indice}]"
        if not isinstance(secao, dict):
            raise ValueError(f"{onde}: item tem de ser um objeto")
        item = dict(secao)
        caminho = item.get("endereco", item.get("link", item.get("path")))
        item["link"] = _link(fonte, caminho, onde)
        item.setdefault("nome", item.get("id", ""))
        resultado.append(item)
    return resultado


def _totais_por_moeda(linhas: list[Linha]) -> list[dict]:
    """Totais que ainda preservam a moeda, para qualquer grupo da tela."""
    acumulado: dict[str, dict] = {}
    for linha in linhas:
        bloco = acumulado.setdefault(
            linha.moeda, {"moeda": linha.moeda, "total": Decimal("0.00"), "linhas": 0}
        )
        bloco["total"] += linha.valor
        bloco["linhas"] += 1
    return [acumulado[moeda] for moeda in sorted(acumulado)]


def _nomes_por_id(colecao) -> dict[str, str]:
    """Do identificador normalizado para o nome legível, para a tela."""
    if not isinstance(colecao, list):
        return {}
    nomes = {}
    for item in colecao:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            nomes[item["id"]] = str(item.get("nome") or item["id"])
    return nomes


CAMINHO_ACEITO = re.compile(r"/(?![/\\])[^\s\\]*")
TAMANHO_MAXIMO_DO_CAMINHO = 2048


def _link(fonte: Fonte, caminho, onde: str) -> str:
    """O link da linha, se a fonte publicou um caminho aceitável."""
    if caminho is None or caminho == "":
        return ""
    if (
        not isinstance(caminho, str)
        or len(caminho) > TAMANHO_MAXIMO_DO_CAMINHO
        or not CAMINHO_ACEITO.fullmatch(caminho)
    ):
        logger.warning("%s: endereço descartado, não é um caminho relativo à fonte", onde)
        return ""
    return fonte.link(caminho)


def _linha_v2(
    fonte: Fonte,
    item: dict,
    *,
    papel: str,
    titulares: dict[str, str],
    instituicoes: dict[str, str],
    colecao: str,
    indice: int,
) -> Linha:
    onde = f"{fonte.nome}/{colecao}[{indice}]"
    if not isinstance(item, dict):
        raise ValueError(f"{onde}: item tem de ser um objeto")
    titular_id = item.get("titular") or item.get("titular_id")
    instituicao_id = item.get("instituicao") or item.get("instituicao_id")
    titular = titulares.get(titular_id, str(titular_id or "—"))
    instituicao = instituicoes.get(instituicao_id, str(instituicao_id or "—"))
    descricao = item.get("instrumento", item.get("nome", item.get("descricao")))
    if not isinstance(descricao, str) or not descricao.strip():
        raise ValueError(f"{onde}: falta 'instrumento'/'nome'")
    moeda = item.get("moeda", item.get("currency"))
    if not isinstance(moeda, str) or not moeda.strip():
        raise ValueError(f"{onde}: falta 'moeda'")
    valor = _valor_em(
        item,
        ("valor_a_mercado", "valor", "saldo", "valor_bruto", "exposicao_bruta"),
        onde,
    )
    quantidade = _decimal_opcional(item, "quantidade", onde)
    # Price is optional in a summary, but when a publisher sends it it is
    # still a monetary value and must not arrive as a JSON number.
    preco = _valor_em(item, ("preco", "preco_unitario", "valor_unitario"), onde, obrigatorio=False)
    custo = _valor_em(item, ("custo", "custo_total", "cost_basis"), onde, obrigatorio=False)
    custo_medio = _valor_em(item, ("custo_medio", "preco_medio", "average_cost"), onde, obrigatorio=False)
    bruto = _valor_em(
        item,
        ("exposicao_bruta", "valor_bruto", "gross_exposure"),
        onde,
        obrigatorio=False,
    )
    ganho = _valor_em(
        item,
        ("ganho_realizado", "resultado_realizado", "realized_gain", "result"),
        onde,
        obrigatorio=False,
    )
    renda = _valor_em(item, ("renda", "income", "proventos"), onde, obrigatorio=False)
    ganho_nao_realizado = _valor_em(
        item,
        ("resultado_nao_realizado", "ganho_nao_realizado", "unrealized_gain"),
        onde,
        obrigatorio=False,
    )
    peso = _valor_em(item, ("peso", "weight"), onde, obrigatorio=False)
    retorno = _valor_em(
        item,
        ("retorno_nao_realizado", "retorno", "return"),
        onde,
        obrigatorio=False,
    )
    volatilidade = _valor_em(item, ("volatilidade", "volatility"), onde, obrigatorio=False)
    return Linha(
        fonte=fonte.nome,
        papel=str(item.get("papel") or papel),
        titular=titular,
        instituicao=instituicao,
        descricao=descricao.strip(),
        moeda=moeda.strip(),
        valor=valor,
        id_de_origem=_texto_opcional(item, "id", onde),
        detalhe=_texto_opcional(item, "preco_em", onde),
        link=_link(fonte, item.get("endereco", item.get("link")), onde),
        classe=_texto_opcional(item, "classe", onde),
        mercado=_texto_opcional(item, "mercado", onde),
        quantidade=quantidade,
        natureza=_texto_opcional(item, "natureza", onde),
        categoria=_texto_opcional(item, "categoria", onde),
        tipo=_texto_opcional(item, "tipo", onde),
        custo=custo,
        exposicao_bruta=bruto,
        ganho_realizado=ganho,
        renda=renda,
        preco=preco,
        custo_medio=custo_medio,
        ganho_nao_realizado=ganho_nao_realizado,
        peso=peso,
        retorno=retorno,
        volatilidade=volatilidade,
        secao=_texto_opcional(item, "secao", onde),
        qualidade=_texto_opcional(item, "situacao_do_preco", onde),
    )


def _inteiro_nao_negativo(valor: Any, onde: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise ValueError(f"{onde}: tem de ser um inteiro não negativo")
    return valor


def _fluxos_v2(corpo: dict, fonte: Fonte) -> list[dict[str, Any]]:
    resultado = []
    for indice, bruto in enumerate(
        _lista(corpo, ("fluxos", "movimentos", "cash_flows"), f"{fonte.nome}/fluxos")
    ):
        onde = f"{fonte.nome}/fluxos[{indice}]"
        if not isinstance(bruto, dict):
            raise ValueError(f"{onde}: item tem de ser um objeto")
        dia = _data_opcional(bruto, ("data",), onde)
        if dia is None:
            raise ValueError(f"{onde}: falta 'data'")
        moeda = _texto(bruto, "moeda", onde)
        natureza = _texto(bruto, "natureza", onde)
        entradas = _valor_em(bruto, ("entradas",), onde)
        saidas = _valor_em(bruto, ("saidas",), onde)
        liquido = _valor_em(bruto, ("liquido", "valor"), onde)
        if liquido != entradas - saidas:
            raise ValueError(f"{onde}: liquido não fecha com entradas menos saídas")
        resultado.append(
            {
                **bruto,
                "data": dia,
                "moeda": moeda,
                "natureza": natureza,
                "entradas": entradas,
                "saidas": saidas,
                "liquido": liquido,
                "valor": liquido,
                "linhas": _inteiro_nao_negativo(bruto.get("linhas"), f"{onde}.linhas"),
                "link": _link(fonte, bruto.get("endereco"), onde),
            }
        )
    return resultado


def _ganhos_v2(corpo: dict, fonte: Fonte) -> list[dict[str, Any]]:
    resultado = []
    colecao = _lista(
        corpo,
        ("ganhos_realizados_por_moeda", "ganhos_realizados", "ganhos"),
        f"{fonte.nome}/ganhos_realizados",
    )
    for indice, bruto in enumerate(colecao):
        onde = f"{fonte.nome}/ganhos_realizados[{indice}]"
        if not isinstance(bruto, dict):
            raise ValueError(f"{onde}: item tem de ser um objeto")
        item = dict(bruto)
        item["moeda"] = _texto(bruto, "moeda", onde)
        for chave in ("ganhos", "perdas", "resultado", "win_rate"):
            if chave in bruto and bruto[chave] is not None:
                item[chave] = _para_decimal(bruto[chave], f"{onde}.{chave}")
        item["valor"] = item.get("resultado", Decimal("0"))
        item["link"] = _link(fonte, bruto.get("endereco"), onde)
        resultado.append(item)
    return resultado


def _rendas_v2(corpo: dict, fonte: Fonte) -> list[dict[str, Any]]:
    resultado = []
    colecao = _lista(
        corpo,
        ("renda_por_moeda", "rendas", "income"),
        f"{fonte.nome}/renda",
    )
    for indice, bruto in enumerate(colecao):
        onde = f"{fonte.nome}/renda[{indice}]"
        if not isinstance(bruto, dict):
            raise ValueError(f"{onde}: item tem de ser um objeto")
        total = _valor_em(bruto, ("total", "valor"), onde)
        por_tipo = bruto.get("por_tipo") or {}
        if not isinstance(por_tipo, dict):
            raise ValueError(f"{onde}.por_tipo: tem de ser um objeto")
        item = {
            **bruto,
            "moeda": _texto(bruto, "moeda", onde),
            "total": total,
            "valor": total,
            "por_tipo": {
                str(tipo): _para_decimal(valor, f"{onde}.por_tipo.{tipo}")
                for tipo, valor in por_tipo.items()
            },
            "link": _link(fonte, bruto.get("endereco"), onde),
        }
        resultado.append(item)
    return resultado


def _desempenho_v2(corpo: dict, fonte: Fonte) -> list[dict[str, Any]]:
    resultado = []
    colecao = _lista(
        corpo,
        ("desempenho_por_moeda", "twr", "desempenho"),
        f"{fonte.nome}/desempenho",
    )
    for indice, bruto in enumerate(colecao):
        onde = f"{fonte.nome}/desempenho[{indice}]"
        if not isinstance(bruto, dict):
            raise ValueError(f"{onde}: item tem de ser um objeto")
        pontos = []
        for ponto_indice, ponto in enumerate(_lista(bruto, ("pontos",), f"{onde}.pontos")):
            ponto_onde = f"{onde}.pontos[{ponto_indice}]"
            if not isinstance(ponto, dict):
                raise ValueError(f"{ponto_onde}: item tem de ser um objeto")
            dia = _data_opcional(ponto, ("data",), ponto_onde)
            if dia is None:
                raise ValueError(f"{ponto_onde}: falta 'data'")
            normalizado = {**ponto, "data": dia}
            for chave in (
                "valor_a_mercado",
                "fluxo_neutralizado",
                "renda_total",
                "indice_twr",
                "retorno_acumulado",
            ):
                if chave in ponto and ponto[chave] is not None:
                    normalizado[chave] = _para_decimal(ponto[chave], f"{ponto_onde}.{chave}")
            por_tipo = ponto.get("renda_por_tipo") or {}
            if not isinstance(por_tipo, dict):
                raise ValueError(f"{ponto_onde}.renda_por_tipo: tem de ser um objeto")
            normalizado["renda_por_tipo"] = {
                str(tipo): _para_decimal(valor, f"{ponto_onde}.renda_por_tipo.{tipo}")
                for tipo, valor in por_tipo.items()
            }
            pontos.append(normalizado)
        resultado.append(
            {
                **bruto,
                "moeda": _texto(bruto, "moeda", onde),
                "metodo": _texto(bruto, "metodo", onde),
                "pontos": pontos,
                "link": _link(fonte, bruto.get("endereco"), onde),
            }
        )
    return resultado


def _interpretar_v2(fonte: Fonte, corpo: dict) -> Leitura:
    """Interpret v2's additive analytics while retaining v1's row semantics."""
    # A v1 envelope relabelled as v2 is not a valid rollout response.  This
    # also keeps the old, useful failure mode: a typo in a publisher's route
    # cannot make us silently claim support for a format we did not validate.
    novos = {
        "fluxos", "movimentos", "cash_flows", "ganhos_realizados", "ganhos",
        "rendas", "income", "twr", "desempenho", "qualidade", "secoes",
        "links_secoes", "posicoes_enriquecidas", "inicio", "data", "periodo", "versao",
        "periodo_dos_fluxos", "ganhos_realizados_por_moeda", "renda_por_moeda",
        "desempenho_por_moeda", "enderecos", "posicoes_atuais",
    }
    linhas_v2 = corpo.get("posicoes", [])
    linha_enriquecida = any(
        isinstance(item, dict)
        and any(
            chave in item
            for chave in (
                "custo", "custo_total", "custo_medio", "exposicao_bruta",
                "ganho_realizado", "ganho_nao_realizado", "peso", "retorno",
            )
        )
        for item in linhas_v2
    ) if isinstance(linhas_v2, list) else False
    if not any(chave in corpo for chave in novos) and not linha_enriquecida:
        return Leitura(
            fonte=fonte,
            estado=FALHOU,
            motivo="contrato desconhecido: patrimonio/v2 sem campos v2",
        )
    try:
        intervalo = corpo.get("periodo") if isinstance(corpo.get("periodo"), dict) else {}
        if not intervalo and isinstance(corpo.get("periodo_dos_fluxos"), dict):
            intervalo = corpo["periodo_dos_fluxos"]
        referencia = _data_opcional(corpo, ("data_de_referencia", "data", "fim"), fonte.nome)
        if referencia is None:
            referencia = _data_opcional(intervalo, ("data", "fim", "ate"), fonte.nome)
        if referencia is None:
            raise ValueError(f"{fonte.nome}: falta 'data_de_referencia'/'data'")
        inicio = _data_opcional(corpo, ("inicio", "data_inicio"), fonte.nome)
        if inicio is None:
            inicio = _data_opcional(intervalo, ("inicio", "data_inicio", "desde"), fonte.nome)
        if inicio is not None and inicio > referencia:
            raise ValueError(f"{fonte.nome}: inicio posterior a data")
        titulares = _nomes_por_id(corpo.get("titulares"))
        instituicoes = _nomes_por_id(corpo.get("instituicoes"))
        linhas: list[Linha] = []
        contas = _lista(corpo, ("contas",), f"{fonte.nome}/contas")
        posicoes = _lista(corpo, ("posicoes_enriquecidas", "posicoes"), f"{fonte.nome}/posicoes")
        for indice, conta in enumerate(contas):
            linhas.append(_linha_v2(
                fonte, conta, papel=str(corpo.get("papel") or "caixa"),
                titulares=titulares, instituicoes=instituicoes, colecao="contas", indice=indice,
            ))
        for indice, posicao in enumerate(posicoes):
            linhas.append(_linha_v2(
                fonte, posicao, papel=str(corpo.get("papel") or "investimento"),
                titulares=titulares, instituicoes=instituicoes, colecao="posicoes", indice=indice,
            ))

        fluxos = _fluxos_v2(corpo, fonte)
        ganhos = _ganhos_v2(corpo, fonte)
        rendas = _rendas_v2(corpo, fonte)
        twr = _desempenho_v2(corpo, fonte)
        lacunas = _lacunas(corpo.get("omitidas"), fonte.nome)
        qualidade = corpo.get("qualidade", corpo.get("quality"))
        if qualidade is not None and not isinstance(qualidade, (dict, list)):
            raise ValueError(f"{fonte.nome}/qualidade: tem de ser objeto ou lista")
        secoes = _secoes_v2(corpo, fonte)
    except (ValueError, TypeError) as erro:
        return Leitura(fonte=fonte, estado=FALHOU, motivo=str(erro))
    return Leitura(
        fonte=fonte,
        estado=OK,
        data_de_referencia=referencia,
        inicio=inicio,
        data_de_fim=referencia,
        linhas=linhas,
        lacunas=lacunas,
        fluxos=fluxos,
        ganhos_realizados=ganhos,
        rendas=rendas,
        twr=twr,
        qualidade=qualidade,
        secoes=secoes,
    )


def interpretar(fonte: Fonte, corpo: dict) -> Leitura:
    """Transforma o envelope publicado em linhas, ou recusa o envelope inteiro.

    Recusar inteiro é deliberado: meia leitura viraria meio total, e meio total
    é indistinguível de um total menor.
    """
    if not isinstance(corpo, dict):
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta não é um objeto JSON")
    contrato = corpo.get("contrato")
    if contrato == CONTRATO_V2:
        return _interpretar_v2(fonte, corpo)
    if contrato != CONTRATO_ACEITO:
        return Leitura(
            fonte=fonte,
            estado=FALHOU,
            motivo=f"contrato desconhecido: {contrato!r} (esperado {CONTRATO_ACEITO!r})",
        )

    try:
        referencia = date.fromisoformat(_texto(corpo, "data_de_referencia", fonte.nome))
        titulares = _nomes_por_id(corpo.get("titulares"))
        instituicoes = _nomes_por_id(corpo.get("instituicoes"))
        papel = str(corpo.get("papel") or fonte.papel)

        linhas: list[Linha] = []
        for indice, conta in enumerate(corpo.get("contas") or []):
            onde = f"{fonte.nome}/contas[{indice}]"
            linhas.append(
                Linha(
                    fonte=fonte.nome,
                    papel=papel,
                    titular=titulares.get(conta.get("titular"), str(conta.get("titular") or "—")),
                    instituicao=instituicoes.get(
                        conta.get("instituicao"), str(conta.get("instituicao") or "—")
                    ),
                    descricao=_texto(conta, "nome", onde),
                    moeda=_texto(conta, "moeda", onde),
                    valor=_para_decimal(conta.get("saldo"), onde),
                    id_de_origem=_texto_opcional(conta, "id", onde),
                    link=_link(fonte, conta.get("endereco"), onde),
                )
            )
        for indice, posicao in enumerate(corpo.get("posicoes") or []):
            onde = f"{fonte.nome}/posicoes[{indice}]"
            linhas.append(
                Linha(
                    fonte=fonte.nome,
                    papel=papel,
                    titular=titulares.get(posicao.get("titular"), str(posicao.get("titular") or "—")),
                    instituicao=instituicoes.get(
                        posicao.get("instituicao"), str(posicao.get("instituicao") or "—")
                    ),
                    descricao=_texto(posicao, "instrumento", onde),
                    moeda=_texto(posicao, "moeda", onde),
                    valor=_para_decimal(posicao.get("valor_a_mercado"), onde),
                    id_de_origem=_texto_opcional(posicao, "id", onde),
                    detalhe=str(posicao.get("preco_em") or ""),
                    link=_link(fonte, posicao.get("endereco"), onde),
                    classe=_texto_opcional(posicao, "classe", onde),
                    mercado=_texto_opcional(posicao, "mercado", onde),
                    quantidade=_decimal_opcional(posicao, "quantidade", onde),
                )
            )
        lacunas = _lacunas(corpo.get("omitidas"), fonte.nome)
    except ValueError as erro:
        return Leitura(fonte=fonte, estado=FALHOU, motivo=str(erro))

    return Leitura(
        fonte=fonte, estado=OK, data_de_referencia=referencia, linhas=linhas, lacunas=lacunas
    )


def _lacunas(omitidas, onde: str) -> list[str]:
    """O que a fonte deixou de fora sem ter como avaliar.

    O envelope conta três omissões, e só uma é lacuna. Carteira simulada não é
    patrimônio, e opção fica de fora por decisão até ser publicada com o mesmo
    cuidado das ações: as duas ficam de fora em toda data, então não abrem um
    buraco na série. Posição **sem cotação** é outra coisa: ela vale dinheiro, e
    o total sem ela é menor do que o patrimônio.
    """
    if omitidas is None:
        return []
    if not isinstance(omitidas, dict):
        raise ValueError(f"{onde}: 'omitidas' tem de ser um objeto")
    sem_cotacao = omitidas.get("sem_cotacao", 0)
    if isinstance(sem_cotacao, bool) or not isinstance(sem_cotacao, int) or sem_cotacao < 0:
        raise ValueError(f"{onde}: 'omitidas.sem_cotacao' tem de ser um inteiro")
    if not sem_cotacao:
        return []
    return [
        f"{sem_cotacao} posição sem cotação ficou de fora"
        if sem_cotacao == 1
        else f"{sem_cotacao} posições sem cotação ficaram de fora"
    ]


def _endereco_do_contrato(fonte: Fonte, contrato: str) -> str:
    """Build a versioned endpoint without changing the Fonte v1 contract."""
    if contrato == CONTRATO_ACEITO:
        return fonte.endereco_do_resumo
    if contrato == CONTRATO_V2:
        return f"{fonte.url.rstrip('/')}/patrimonio/v2/resumo"
    raise ValueError(f"contrato desconhecido: {contrato!r}")


def buscar(
    fonte: Fonte,
    referencia: date | None = None,
    *,
    contrato: str = CONTRATO_ACEITO,
    inicio: date | None = None,
    data: date | None = None,
    periodo: str | None = None,
) -> Leitura:
    """Uma requisição, com tempo limite curto. Falhar aqui é normal, não é erro.

    O tempo limite é curto de propósito: a tela precisa abrir mesmo com uma
    fonte inacessível, e dizer que ela não respondeu vale mais do que esperar
    trinta segundos por ela.
    """
    endereco = _endereco_do_contrato(fonte, contrato)
    if data is None:
        data = referencia
    parametros: dict[str, str] = {}
    if inicio is not None:
        parametros["inicio"] = inicio.isoformat()
    if data is not None:
        parametros["data"] = data.isoformat()
    if periodo:
        parametros["periodo"] = periodo
    if parametros:
        endereco = f"{endereco}?{urlencode(parametros)}"
    pedido = urllib.request.Request(
        endereco,
        headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE_SEGUNDOS) as resposta:
            bruto = resposta.read(TAMANHO_MAXIMO_BYTES + 1)
    except urllib.error.HTTPError as erro:
        motivo = {
            401: "token recusado pela fonte",
            503: "a fonte respondeu que a integração não está configurada nela",
        }.get(erro.code, f"a fonte respondeu HTTP {erro.code}")
        return Leitura(fonte=fonte, estado=FALHOU, motivo=motivo)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        logger.warning("Fonte %s não respondeu: %s", fonte.apelido, erro)
        return Leitura(fonte=fonte, estado=NAO_RESPONDEU, motivo="não respondeu")

    if len(bruto) > TAMANHO_MAXIMO_BYTES:
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta grande demais")
    try:
        corpo = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta não é JSON")
    if not isinstance(corpo, dict):
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta não é um objeto JSON")
    leitura = interpretar(fonte, corpo)
    if (
        data is not None
        and leitura.respondeu
        and leitura.data_de_referencia != data
    ):
        # Uma fonte que ignora `?data=` responde o patrimônio de hoje para uma
        # pergunta sobre março, e o número passa por histórico. É a forma mais
        # silenciosa de inventar o passado, então a leitura inteira é recusada.
        return Leitura(
            fonte=fonte,
            estado=FALHOU,
            motivo=(
                f"a fonte respondeu a data {leitura.data_de_referencia:%d/%m/%Y} "
                f"para a pergunta sobre {data:%d/%m/%Y}"
            ),
        )
    return leitura


def consolidar(
    referencia: date | None = None,
    *,
    contrato: str = CONTRATO_ACEITO,
    inicio: date | None = None,
    data: date | None = None,
    periodo: str | None = None,
) -> Consolidado:
    """O patrimônio como ele está agora, com o estado de cada fonte junto."""
    return Consolidado(
        leituras=[
            buscar(
                fonte,
                referencia,
                contrato=contrato,
                inicio=inicio,
                data=data,
                periodo=periodo,
            )
            for fonte in fontes_configuradas()
        ],
        incompletas=fontes_incompletas(),
    )


def buscar_v2(
    fonte: Fonte,
    *,
    inicio: date | None = None,
    data: date | None = None,
    periodo: str | None = None,
) -> Leitura:
    """Explicit v2 convenience wrapper for dashboard callers."""
    return buscar(
        fonte,
        contrato=CONTRATO_V2,
        inicio=inicio,
        data=data,
        periodo=periodo,
    )


def consolidar_v2(
    *,
    inicio: date | None = None,
    data: date | None = None,
    periodo: str | None = None,
) -> Consolidado:
    """Read both v2 publishers with one inclusive interval."""
    return consolidar(
        contrato=CONTRATO_V2,
        inicio=inicio,
        data=data,
        periodo=periodo,
    )
