"""Patrimônio projetado: "se o que está lançado acontecer, quanto vou ter?".

DE ONDE VEM CADA PARTE

O caixa projetado é do Controle Bancário, lido de `/patrimonio/v3/projection`.
Este módulo não recalcula saldo: a regra de status (vencido, a vencer) e o
saldo de partida são da fonte, pelas mesmas funções das telas dela.

Os investimentos entram pelo valor de hoje, lidos do mesmo `Consolidado` que o
resto do shell usa. Não há estimativa de rendimento: um número de rendimento
futuro seria uma opinião apresentada como dado.

O QUE MUDA O NÚMERO, E POR ISSO APARECE NA TELA

- aporte programado não é perda: o que sai do caixa para investimento
  (`investido_acumulado`, publicado pela fonte) volta ao patrimônio no mesmo
  dia;
- vencidos não realizados entram no dia de hoje e são mostrados à parte;
- datas futuras não têm taxa de câmbio. A conversão usa a taxa de hoje, com a
  data dela à vista; sem taxa válida, o total em moeda base não aparece, como
  no resto do NetWorth;
- a projeção termina onde terminam os lançamentos da fonte. As recorrências só
  existem até o horizonte configurado lá, e a tela marca esse limite;
- sem os investimentos (Renda Variável fora do ar), não existe patrimônio
  projetado: a tela mostra só o caixa e diz por quê.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from consolidado import leitor
from consolidado.cambio import MOEDA_BASE, Conversao, converter_totais
from consolidado.fontes import Fonte

CAMINHO = "/patrimonio/v3/projection"
CONTRATO = "patrimonio/v3"
TEMPO_LIMITE_SEGUNDOS = 10
TAMANHO_MAXIMO_BYTES = 4 * 1024 * 1024
MAXIMO_DE_DIAS = 1100

#: Chave na URL -> (rótulo, dias à frente). `None` é "até o fim da projeção".
HORIZONTES = {
    "30d": ("30 dias", 30),
    "90d": ("90 dias", 90),
    "6m": ("6 meses", 183),
    "tudo": ("Até o fim da projeção", None),
}
HORIZONTE_PADRAO = "6m"

MOEDA = re.compile(r"[A-Z]{3}")
ZERO = Decimal("0.00")

logger = logging.getLogger(__name__)


# --- O que a fonte publica ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class PontoDeCaixa:
    data: date
    moeda: str
    saldo: Decimal
    investido: Decimal


@dataclass(frozen=True, slots=True)
class MesDeCaixa:
    mes: date
    moeda: str
    entradas: Decimal
    saidas: Decimal
    transferencias_entrada: Decimal
    transferencias_saida: Decimal
    investimentos_entrada: Decimal
    investimentos_saida: Decimal
    saldo_final: Decimal
    link: str = ""


@dataclass(frozen=True, slots=True)
class Vencidos:
    moeda: str
    entradas: Decimal
    saidas: Decimal
    quantidade: int


@dataclass(frozen=True, slots=True)
class ContaProjetada:
    nome: str
    instituicao: str
    moeda: str
    saldo_final: Decimal
    menor_saldo: Decimal
    data_do_menor: date
    link: str = ""
    tipo: str = ""

    @property
    def e_cartao_de_credito(self) -> bool:
        return self.tipo == leitor.TIPO_CARTAO_DE_CREDITO


@dataclass(frozen=True, slots=True)
class ProjecaoDeCaixa:
    fonte: str
    data_base: date
    fim: date
    vencidos_incluidos: bool
    meses_configurados: int | None
    ultima_recorrencia: date | None
    saldos_iniciais: dict[str, Decimal]
    vencidos: tuple[Vencidos, ...]
    serie: tuple[PontoDeCaixa, ...]
    meses: tuple[MesDeCaixa, ...]
    contas: tuple[ContaProjetada, ...]

    @property
    def moedas(self) -> tuple[str, ...]:
        return tuple(sorted(self.saldos_iniciais))

    @property
    def fim_da_projecao(self) -> date:
        """Até onde a curva é informativa: o fim pedido ou a última recorrência."""
        if self.ultima_recorrencia is None:
            return self.fim
        return min(self.fim, max(self.ultima_recorrencia, self.data_base))


@dataclass(frozen=True, slots=True)
class LeituraDaProjecao:
    fonte: str
    projecao: ProjecaoDeCaixa | None = None
    motivo: str = ""


def _data(valor: Any, onde: str) -> date:
    if not isinstance(valor, str):
        raise ValueError(f"{onde}: data ausente")
    try:
        return date.fromisoformat(valor)
    except ValueError as erro:
        raise ValueError(f"{onde}: {valor!r} não é uma data") from erro


def _moeda(valor: Any, onde: str) -> str:
    if not isinstance(valor, str) or not MOEDA.fullmatch(valor):
        raise ValueError(f"{onde}: moeda inválida")
    return valor


def _lista(corpo: dict, chave: str) -> list[dict]:
    valor = corpo.get(chave)
    if not isinstance(valor, list) or not all(isinstance(item, dict) for item in valor):
        raise ValueError(f"projeção: '{chave}' tem de ser uma lista de objetos")
    return valor


def interpretar(fonte: Fonte, corpo: Any) -> ProjecaoDeCaixa:
    """Valida o envelope inteiro. Qualquer campo errado recusa a projeção toda.

    Uma projeção meio lida desenharia uma curva plausível e errada -- o defeito
    que o resto do NetWorth existe para evitar.
    """
    if not isinstance(corpo, dict):
        raise ValueError("projeção: o corpo não é um objeto")
    if corpo.get("contrato") != CONTRATO or corpo.get("recurso") != "projecao":
        raise ValueError("projeção: contrato ou recurso desconhecido")
    para_decimal = leitor._para_decimal

    horizonte = corpo.get("horizonte") or {}
    if not isinstance(horizonte, dict):
        raise ValueError("projeção: 'horizonte' tem de ser um objeto")
    meses_configurados = horizonte.get("meses_configurados")
    if meses_configurados is not None and (isinstance(meses_configurados, bool) or not isinstance(meses_configurados, int)):
        raise ValueError("projeção: 'meses_configurados' tem de ser inteiro")
    bruta_recorrencia = horizonte.get("ultima_recorrencia")
    ultima_recorrencia = _data(bruta_recorrencia, "horizonte") if bruta_recorrencia else None

    saldos = {
        _moeda(item.get("moeda"), "saldos_iniciais"): para_decimal(item.get("saldo"), "saldos_iniciais")
        for item in _lista(corpo, "saldos_iniciais")
    }
    vencidos = tuple(
        Vencidos(
            moeda=_moeda(item.get("moeda"), "vencidos"),
            entradas=para_decimal(item.get("entradas"), "vencidos.entradas"),
            saidas=para_decimal(item.get("saidas"), "vencidos.saidas"),
            quantidade=leitor._inteiro_nao_negativo(item.get("quantidade"), "vencidos.quantidade"),
        )
        for item in _lista(corpo, "vencidos")
    )
    serie = tuple(
        PontoDeCaixa(
            data=_data(item.get("data"), "serie"),
            moeda=_moeda(item.get("moeda"), "serie"),
            saldo=para_decimal(item.get("saldo"), "serie.saldo"),
            investido=para_decimal(item.get("investido_acumulado"), "serie.investido_acumulado"),
        )
        for item in _lista(corpo, "serie")
    )
    meses = tuple(
        MesDeCaixa(
            mes=_data(f"{item.get('mes')}-01", "meses"),
            moeda=_moeda(item.get("moeda"), "meses"),
            **{
                campo: para_decimal(item.get(campo), f"meses.{campo}")
                for campo in (
                    "entradas", "saidas", "transferencias_entrada", "transferencias_saida",
                    "investimentos_entrada", "investimentos_saida", "saldo_final",
                )
            },
            link=leitor._link(fonte, item.get("deep_link"), "meses"),
        )
        for item in _lista(corpo, "meses")
    )
    contas = []
    for item in _lista(corpo, "contas"):
        menor = item.get("menor_saldo")
        if not isinstance(menor, dict):
            raise ValueError("projeção: conta sem 'menor_saldo'")
        contas.append(
            ContaProjetada(
                nome=leitor._texto(item, "nome", "contas"),
                instituicao=leitor._texto_opcional(item, "instituicao", "contas"),
                moeda=_moeda(item.get("moeda"), "contas"),
                saldo_final=para_decimal(item.get("saldo_final"), "contas.saldo_final"),
                menor_saldo=para_decimal(menor.get("valor"), "contas.menor_saldo"),
                data_do_menor=_data(menor.get("data"), "contas.menor_saldo"),
                link=leitor._link(fonte, item.get("deep_link"), "contas"),
                tipo=leitor._texto_opcional(item, "tipo", "contas"),
            )
        )

    moedas_da_serie = {ponto.moeda for ponto in serie}
    if moedas_da_serie - set(saldos):
        raise ValueError("projeção: a série tem moeda sem saldo inicial")
    vencidos_incluidos = corpo.get("vencidos_incluidos")
    if not isinstance(vencidos_incluidos, bool):
        raise ValueError("projeção: 'vencidos_incluidos' tem de ser verdadeiro ou falso")

    return ProjecaoDeCaixa(
        fonte=fonte.nome,
        data_base=_data(corpo.get("data_base"), "data_base"),
        fim=_data(corpo.get("fim"), "fim"),
        vencidos_incluidos=vencidos_incluidos,
        meses_configurados=meses_configurados,
        ultima_recorrencia=ultima_recorrencia,
        saldos_iniciais=saldos,
        vencidos=vencidos,
        serie=tuple(sorted(serie, key=lambda ponto: (ponto.moeda, ponto.data))),
        meses=tuple(sorted(meses, key=lambda item: (item.mes, item.moeda))),
        contas=tuple(contas),
    )


def buscar(fonte: Fonte, fim: date) -> LeituraDaProjecao:
    """Pede a projeção até `fim`. Falha vira motivo legível, nunca exceção."""
    consulta = urlencode({"fim": fim.isoformat()})
    endereco = f"{fonte.url.rstrip('/')}{CAMINHO}?{consulta}"
    pedido = urllib.request.Request(
        endereco,
        headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE_SEGUNDOS) as resposta:
            bruto = resposta.read(TAMANHO_MAXIMO_BYTES + 1)
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            return LeituraDaProjecao(fonte.nome, motivo="a fonte ainda não publica a projeção")
        return LeituraDaProjecao(fonte.nome, motivo=f"a fonte respondeu HTTP {erro.code}")
    # O arnês de testes levanta RuntimeError em qualquer acesso à rede: a tela
    # tem de tratar isso como fonte fora do ar, não como erro 500.
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as erro:
        logger.warning("Projeção de %s não respondeu: %s", fonte.apelido, erro)
        return LeituraDaProjecao(fonte.nome, motivo="a fonte não respondeu")
    if len(bruto) > TAMANHO_MAXIMO_BYTES:
        return LeituraDaProjecao(fonte.nome, motivo="resposta grande demais")
    try:
        return LeituraDaProjecao(fonte.nome, projecao=interpretar(fonte, json.loads(bruto.decode("utf-8"))))
    except (ValueError, UnicodeDecodeError) as erro:
        logger.warning("Projeção de %s recusada: %s", fonte.apelido, erro)
        return LeituraDaProjecao(fonte.nome, motivo=f"projeção recusada: {erro}")


# --- O que a tela mostra -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PontoProjetado:
    """Um dia da curva, por moeda. `patrimonio` é None sem os investimentos."""

    data: date
    caixa: dict[str, Decimal]
    patrimonio: dict[str, Decimal] | None


@dataclass(frozen=True, slots=True)
class LinhaDoMes:
    mes: date
    moeda: str
    entradas: Decimal
    saidas: Decimal
    para_investir: Decimal
    caixa_no_fim: Decimal
    patrimonio_no_fim: Decimal | None
    link: str


@dataclass(frozen=True, slots=True)
class Projetado:
    """A projeção pronta para a tela, dentro do horizonte escolhido."""

    projecao: ProjecaoDeCaixa
    ate: date
    pontos: tuple[PontoProjetado, ...]
    meses: tuple[LinhaDoMes, ...]
    investimentos_hoje: dict[str, Decimal] | None
    conversao: Conversao | None

    @property
    def tem_patrimonio(self) -> bool:
        return self.investimentos_hoje is not None

    def em_moeda_base(self, valores: dict[str, Decimal] | None) -> Decimal | None:
        """Converte com a taxa de hoje, ou devolve None se alguma moeda não tem."""
        if valores is None or self.conversao is None or self.conversao.total is None:
            return None
        taxas = {taxa.moeda: taxa.taxa for taxa in self.conversao.taxas}
        total = ZERO
        for moeda, valor in valores.items():
            if moeda == MOEDA_BASE:
                total += valor
            elif moeda in taxas:
                total += valor * taxas[moeda]
            else:
                return None
        return total.quantize(Decimal("0.01"))


def investimentos_por_moeda(consolidado: leitor.Consolidado) -> dict[str, Decimal] | None:
    """Os investimentos de hoje, por moeda -- ou None se não dá para confiar neles.

    Sem o consolidado completo, a falta de uma fonte faria o patrimônio
    projetado sair menor sem aviso. Nesse caso a tela mostra só o caixa.
    """
    if not consolidado.completo:
        return None
    bloco = next(
        (item for item in consolidado.por("papel") if item["nome"] == "investimento"),
        None,
    )
    if bloco is None:
        return {}
    return {total["moeda"]: total["total"] for total in bloco["totais_por_moeda"]}


def montar(
    projecao: ProjecaoDeCaixa,
    investimentos: dict[str, Decimal] | None,
    ate: date,
) -> Projetado:
    """Junta o caixa projetado com os investimentos de hoje, dia a dia.

    Cada moeda fica separada. O patrimônio de uma moeda num dia é o caixa
    dela, mais os investimentos de hoje nela, mais o que foi aportado até o
    dia. A conversão para a moeda base é feita depois, pela tela, com a taxa
    de hoje e tudo ou nada.
    """
    ate = min(ate, projecao.fim)
    moedas = sorted(set(projecao.moedas) | set(investimentos or {}))
    por_moeda: dict[str, list[PontoDeCaixa]] = {}
    for ponto in projecao.serie:
        if ponto.data <= ate:
            por_moeda.setdefault(ponto.moeda, []).append(ponto)

    datas = sorted({ponto.data for pontos in por_moeda.values() for ponto in pontos} | {projecao.data_base, ate})
    pontos: list[PontoProjetado] = []
    for dia in datas:
        caixa: dict[str, Decimal] = {}
        investido: dict[str, Decimal] = {}
        for moeda in moedas:
            anteriores = [ponto for ponto in por_moeda.get(moeda, ()) if ponto.data <= dia]
            if anteriores:
                caixa[moeda] = anteriores[-1].saldo
                investido[moeda] = anteriores[-1].investido
            else:
                caixa[moeda] = projecao.saldos_iniciais.get(moeda, ZERO)
                investido[moeda] = ZERO
        patrimonio = None
        if investimentos is not None:
            patrimonio = {
                moeda: caixa[moeda] + investido[moeda] + investimentos.get(moeda, ZERO)
                for moeda in moedas
            }
        pontos.append(PontoProjetado(data=dia, caixa=caixa, patrimonio=patrimonio))

    meses = []
    for mes in projecao.meses:
        if mes.mes > ate:
            continue
        fim_do_mes = min(_ultimo_dia(mes.mes), ate)
        ponto_do_fim = [ponto for ponto in pontos if ponto.data <= fim_do_mes]
        patrimonio_no_fim = None
        if ponto_do_fim and ponto_do_fim[-1].patrimonio is not None:
            patrimonio_no_fim = ponto_do_fim[-1].patrimonio.get(mes.moeda)
        caixa_no_fim = ponto_do_fim[-1].caixa.get(mes.moeda, mes.saldo_final) if ponto_do_fim else mes.saldo_final
        meses.append(
            LinhaDoMes(
                mes=mes.mes,
                moeda=mes.moeda,
                entradas=mes.entradas,
                saidas=mes.saidas,
                para_investir=mes.investimentos_saida - mes.investimentos_entrada,
                caixa_no_fim=caixa_no_fim,
                patrimonio_no_fim=patrimonio_no_fim,
                link=mes.link,
            )
        )

    conversao = converter_totais(
        [{"moeda": moeda, "total": ZERO} for moeda in moedas], projecao.data_base
    ) if moedas else None
    return Projetado(
        projecao=projecao,
        ate=ate,
        pontos=tuple(pontos),
        meses=tuple(meses),
        investimentos_hoje=investimentos,
        conversao=conversao,
    )


def _ultimo_dia(mes: date) -> date:
    proximo = date(mes.year + mes.month // 12, mes.month % 12 + 1, 1)
    return proximo - timedelta(days=1)


def fim_do_pedido(chave: str, hoje: date) -> date:
    """A data `fim` que se pede à fonte para um horizonte."""
    dias = HORIZONTES[chave][1]
    return hoje + timedelta(days=MAXIMO_DE_DIAS if dias is None else dias)
