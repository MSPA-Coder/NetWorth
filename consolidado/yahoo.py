"""A série diária de câmbio, vinda do Yahoo Finance.

É a mesma fonte que o Controle de Renda Variável já usa para cotação (decisão 4
do estudo), e o formato de chamada é o mesmo de lá -- o par de moedas vira o
símbolo `USDBRL=X`, e o que interessa é o fechamento de cada dia.

A PTAX entra depois, quando houver declaração de imposto: ela é a taxa oficial,
e é a que a Receita aceita. O modelo já guarda a fonte em cada linha justamente
para as duas conviverem sem uma apagar a outra.

O QUE CHEGA PELA REDE É DADO, NÃO INSTRUÇÃO

Toda resposta é conferida antes de virar taxa: estrutura, tipo e sinal. Um
fechamento nulo (dia sem negócio) é pulado, não interpolado -- inventar a taxa
de um dia em que não houve mercado é inventar mercado.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FONTE = "yahoo"
TEMPO_LIMITE_SEGUNDOS = 15
TAMANHO_MAXIMO_BYTES = 4 * 1024 * 1024


class ColetaDeCambioError(RuntimeError):
    """A coleta não trouxe uma série utilizável."""


@dataclass(frozen=True, slots=True)
class Fechamento:
    data: date
    taxa: Decimal


def simbolo(moeda: str, base: str) -> str:
    return f"{moeda.upper()}{base.upper()}=X"


def buscar_serie(moeda: str, base: str, inicio: date, fim: date) -> list[Fechamento]:
    """Fechamentos diários entre `inicio` e `fim`, inclusive."""
    if inicio > fim:
        return []
    parametros = urlencode(
        {
            "period1": int(datetime.combine(inicio, time.min, tzinfo=UTC).timestamp()),
            "period2": int(datetime.combine(fim + timedelta(days=1), time.min, tzinfo=UTC).timestamp()),
            "interval": "1d",
            "events": "history",
        }
    )
    endereco = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo(moeda, base)}?{parametros}"
    pedido = urllib.request.Request(endereco, headers={"User-Agent": "NetWorth/0.1"})
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE_SEGUNDOS) as resposta:  # noqa: S310 - host fixo e https
            bruto = resposta.read(TAMANHO_MAXIMO_BYTES + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ColetaDeCambioError(f"O Yahoo não respondeu para {simbolo(moeda, base)}.") from erro

    if len(bruto) > TAMANHO_MAXIMO_BYTES:
        raise ColetaDeCambioError("Resposta do Yahoo grande demais.")
    try:
        corpo = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ColetaDeCambioError("Resposta do Yahoo não é JSON.") from erro

    return _fechamentos(corpo, moeda, base)


def _fechamentos(corpo, moeda: str, base: str) -> list[Fechamento]:
    grafico = corpo.get("chart") if isinstance(corpo, dict) else None
    resultado = grafico.get("result") if isinstance(grafico, dict) else None
    if not isinstance(resultado, list) or not resultado or not isinstance(resultado[0], dict):
        raise ColetaDeCambioError(f"O Yahoo não tem série para {simbolo(moeda, base)}.")
    serie = resultado[0]
    instantes = serie.get("timestamp")
    indicadores = serie.get("indicators")
    if not isinstance(instantes, list) or not isinstance(indicadores, dict):
        raise ColetaDeCambioError(f"Série inválida para {simbolo(moeda, base)}.")
    cotacoes = indicadores.get("quote")
    if not isinstance(cotacoes, list) or not cotacoes or not isinstance(cotacoes[0], dict):
        raise ColetaDeCambioError(f"Série inválida para {simbolo(moeda, base)}.")
    fechamentos = cotacoes[0].get("close")
    if not isinstance(fechamentos, list):
        raise ColetaDeCambioError(f"Série inválida para {simbolo(moeda, base)}.")

    fuso = _fuso_da_bolsa(serie, moeda, base)
    colhidos: dict[date, Fechamento] = {}
    for indice, instante in enumerate(instantes):
        valor = fechamentos[indice] if indice < len(fechamentos) else None
        if valor is None:
            # Dia sem negócio. Pular é o certo: interpolar inventaria mercado.
            continue
        try:
            dia = datetime.fromtimestamp(int(instante), fuso).date()
            taxa = Decimal(str(valor))
        except (TypeError, ValueError, InvalidOperation, OSError, OverflowError) as erro:
            raise ColetaDeCambioError(f"Série inválida para {simbolo(moeda, base)}.") from erro
        if taxa <= 0:
            continue
        # O mesmo dia pode voltar repetido; a última leitura do dia é a que vale.
        colhidos[dia] = Fechamento(data=dia, taxa=taxa)
    return [colhidos[dia] for dia in sorted(colhidos)]


def _fuso_da_bolsa(serie: dict, moeda: str, base: str) -> tzinfo:
    """O fuso em que o Yahoo marca o dia de cada fechamento.

    Câmbio é negociado em Londres, e o carimbo de cada dia é a meia-noite de
    lá. No horário de verão britânico, essa meia-noite cai às 23:00 UTC do dia
    anterior. Convertido em UTC, o fechamento de 16/09 virava taxa de 15/09 --
    de abril a outubro, a série inteira andava um dia para trás.

    Sem o fuso, a série é recusada: adivinhar UTC é justamente o erro acima.
    """
    meta = serie.get("meta") if isinstance(serie.get("meta"), dict) else {}
    nome = meta.get("exchangeTimezoneName")
    if isinstance(nome, str) and nome:
        try:
            return ZoneInfo(nome)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    raise ColetaDeCambioError(f"O Yahoo não informou o fuso da série {simbolo(moeda, base)}.")
