"""Converter para moeda base -- e recusar converter quando não dá.

CONVERTER É DECIDIR

Por isso a conversão mora aqui, no consolidador, e não nos publicadores: o
Controle Bancário sabe que a conta tem US$ 8.067,22, e é só isso que ele deve
afirmar. Quanto isso vale em reais depende de qual taxa, de que dia, de que
fonte -- três escolhas que pertencem a quem soma.

AS QUATRO REGRAS

1. **A taxa é a do dia da foto**, nunca a de hoje. Converter o passado pela taxa
   de hoje faz o patrimônio de março mudar toda manhã;
2. **Nunca uma taxa posterior à data pedida.** Ela não existia ainda;
3. **Sexta serve para sábado.** Câmbio não tem fechamento no fim de semana nem
   em feriado, então vale a última taxa **anterior ou igual** à data -- e a tela
   diz qual dia foi usado. O que não se faz é fingir que a taxa é do dia pedido;
4. **Taxa velha demais não é taxa, é chute.** Passado o limite de defasagem, a
   conversão é recusada e a tela mostra os totais por moeda, dizendo por quê.
   Um número redondo e errado é pior que dois números certos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from consolidado.models import TaxaDeCambio

MOEDA_BASE = "BRL"
CENTAVO = Decimal("0.01")

#: Depois disto, a última taxa conhecida não descreve mais o dia pedido. Sete
#: dias cobrem feriado prolongado com folga; um mês esconderia uma coleta
#: parada.
DIAS_MAXIMOS_DE_DEFASAGEM = 7

#: Quando duas fontes têm taxa para o mesmo dia, vale a primeira desta lista. A
#: PTAX é a oficial e entra quando houver declaração; o Yahoo é o que está no ar
#: hoje, e é a mesma fonte que o Renda Variável já usa para cotação.
PREFERENCIA_DE_FONTE = ("ptax", "yahoo")


@dataclass(frozen=True, slots=True)
class TaxaAplicada:
    """A taxa que foi usada, e a data que ela realmente tem."""

    moeda: str
    taxa: Decimal
    data: date
    fonte: str
    dias_de_defasagem: int

    @property
    def defasada(self) -> bool:
        return self.dias_de_defasagem > DIAS_MAXIMOS_DE_DEFASAGEM


@dataclass(frozen=True, slots=True)
class Conversao:
    """O total em moeda base, ou a explicação de por que ele não existe."""

    total: Decimal | None
    taxas: tuple[TaxaAplicada, ...]
    sem_taxa: tuple[str, ...]
    defasadas: tuple[TaxaAplicada, ...]

    @property
    def possivel(self) -> bool:
        return self.total is not None

    @property
    def motivo(self) -> str:
        if self.possivel:
            return ""
        partes = []
        if self.sem_taxa:
            partes.append(f"sem taxa para {', '.join(self.sem_taxa)}")
        if self.defasadas:
            atrasos = ", ".join(
                f"{t.moeda} (última de {t.data.strftime('%d/%m/%Y')})" for t in self.defasadas
            )
            partes.append(f"taxa velha demais: {atrasos}")
        return "; ".join(partes) or "não há o que converter"


def taxa_para(moeda: str, referencia: date) -> TaxaAplicada | None:
    """A última taxa conhecida em `referencia` ou antes dela. Nunca depois.

    Devolve `None` só quando não há taxa nenhuma até aquele dia. Taxa velha
    volta preenchida, com a defasagem à vista -- quem chama decide se aceita, e
    a tela precisa poder dizer *quão* velha ela é.
    """
    if moeda == MOEDA_BASE:
        return None
    candidatas = list(
        TaxaDeCambio.objects.filter(moeda=moeda, data__lte=referencia).order_by("-data")[
            : len(PREFERENCIA_DE_FONTE) + 4
        ]
    )
    if not candidatas:
        return None
    dia = candidatas[0].data
    do_dia = [t for t in candidatas if t.data == dia]
    escolhida = min(
        do_dia,
        key=lambda t: (
            PREFERENCIA_DE_FONTE.index(t.fonte) if t.fonte in PREFERENCIA_DE_FONTE else 99,
            t.fonte,
        ),
    )
    return TaxaAplicada(
        moeda=moeda,
        taxa=escolhida.taxa,
        data=escolhida.data,
        fonte=escolhida.fonte,
        dias_de_defasagem=(referencia - escolhida.data).days,
    )


def converter_totais(totais_por_moeda, referencia: date) -> Conversao:
    """Soma os totais em moeda base, ou explica por que não somou.

    Tudo ou nada: se uma das moedas não pode ser convertida, não existe total em
    moeda base. Somar as que dá produziria um patrimônio menor com cara de
    completo -- o mesmo defeito que o estado das fontes existe para impedir.
    """
    total = Decimal("0.00")
    taxas: list[TaxaAplicada] = []
    sem_taxa: list[str] = []
    defasadas: list[TaxaAplicada] = []
    tem_algo = False

    for bloco in totais_por_moeda:
        moeda = bloco["moeda"]
        valor = bloco["total"]
        tem_algo = True
        if moeda == MOEDA_BASE:
            total += valor
            continue
        aplicada = taxa_para(moeda, referencia)
        if aplicada is None:
            sem_taxa.append(moeda)
            continue
        if aplicada.defasada:
            defasadas.append(aplicada)
            continue
        taxas.append(aplicada)
        total += (valor * aplicada.taxa).quantize(CENTAVO, rounding=ROUND_HALF_UP)

    impossivel = bool(sem_taxa or defasadas) or not tem_algo
    return Conversao(
        total=None if impossivel else total.quantize(CENTAVO),
        taxas=tuple(taxas),
        sem_taxa=tuple(sem_taxa),
        defasadas=tuple(defasadas),
    )
