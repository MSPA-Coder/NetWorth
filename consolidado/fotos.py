"""A foto diária do patrimônio, e as duas curvas que saem dela.

POR QUE GUARDAR, SE AS FONTES RESPONDEM QUALQUER DATA

Porque desenhar anos de série perguntando uma data de cada vez, a cada abertura
de tela, seria absurdo. A foto é um resumo do que as fontes responderam, e não
uma segunda verdade: ela pode ser refeita, e é refeita, quando as fontes
corrigem o passado.

POR QUE DUAS CURVAS

O caixa só existe a partir do corte de 31/12/2025 (decisão 7 do estudo). Antes
disso o Controle Bancário responde com zero contas, e somar esse zero aos
investimentos desenharia um "patrimônio" que é só a parte investida. Então:

- **investimentos**, desde a primeira posição (maio de 2022);
- **patrimônio**, caixa mais investimentos, desde `INICIO_DO_PATRIMONIO`.

As duas vêm das mesmas fotos, e as duas são convertidas pela taxa do dia de cada
foto, nunca pela de hoje.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from consolidado.cambio import SerieDeTaxas, converter_totais
from consolidado.fontes import FONTES_CONHECIDAS
from consolidado.leitor import Consolidado, consolidar
from consolidado.models import FotoDoPatrimonio, ValorDaFoto

#: Primeiro dia em que o caixa existe nos dois sistemas. Antes dele só há a
#: curva de investimentos.
INICIO_DO_PATRIMONIO = date(2026, 1, 1)
INICIO_DOS_INVESTIMENTOS = date(2022, 5, 9)

PAPEL_INVESTIMENTO = "investimento"


@dataclass(frozen=True, slots=True)
class FotoLida:
    """O que as fontes responderam para uma data, já decidido se serve."""

    data: date
    motivo: str = ""
    valores: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def completa(self) -> bool:
        return not self.motivo


def _por_que_incompleta(consolidado: Consolidado) -> str:
    """Vazio quando a foto serve; senão, o motivo, legível para quem opera."""
    motivos = []
    conhecidas = {apelido: nome for apelido, nome, _papel in FONTES_CONHECIDAS}
    configuradas = {leitura.fonte.apelido for leitura in consolidado.leituras}
    # `Consolidado.completo` não basta: uma fonte sem endereço NEM token não
    # aparece em lugar nenhum, e a foto seria gravada só com a outra.
    for apelido in sorted(set(conhecidas) - configuradas):
        motivos.append(f"{conhecidas[apelido]} não está configurada")
    for nome in consolidado.incompletas:
        motivos.append(f"{nome} está configurada pela metade")
    for leitura in consolidado.leituras:
        if not leitura.respondeu:
            motivos.append(f"{leitura.fonte.nome}: {leitura.motivo}")
        for lacuna in leitura.lacunas:
            motivos.append(f"{leitura.fonte.nome}: {lacuna}")
    return "; ".join(motivos)


def ler(data: date) -> FotoLida:
    """Pergunta a data às fontes e agrupa a resposta em totais."""
    consolidado = consolidar(data)
    motivo = _por_que_incompleta(consolidado)
    if motivo:
        return FotoLida(data=data, motivo=motivo)

    acumulado: dict[tuple[str, str, str, str], dict] = {}
    for leitura in consolidado.leituras:
        for linha in leitura.linhas:
            chave = (leitura.fonte.apelido, leitura.fonte.papel, linha.instituicao, linha.moeda)
            bloco = acumulado.setdefault(
                chave,
                {
                    "fonte": chave[0],
                    "papel": chave[1],
                    "instituicao": chave[2],
                    "moeda": chave[3],
                    "total": Decimal("0.00"),
                    "linhas": 0,
                },
            )
            bloco["total"] += linha.valor
            bloco["linhas"] += 1
    return FotoLida(data=data, valores=tuple(acumulado[chave] for chave in sorted(acumulado)))


def gravar(lida: FotoLida, *, refazer: bool) -> str:
    """Grava a foto. Devolve `"gravada"`, `"refeita"` ou `"mantida"`.

    Refazer troca a foto inteira na mesma transação: não existe instante em que
    a data fique com metade dos valores antigos e metade dos novos.
    """
    if not lida.completa:
        raise ValueError(f"foto de {lida.data} incompleta: {lida.motivo}")
    with transaction.atomic():
        existente = FotoDoPatrimonio.objects.select_for_update().filter(data=lida.data).first()
        if existente is not None and not refazer:
            return "mantida"
        if existente is not None:
            existente.delete()
        foto = FotoDoPatrimonio.objects.create(data=lida.data, tirada_em=timezone.now())
        ValorDaFoto.objects.bulk_create(
            ValorDaFoto(foto=foto, **valor) for valor in lida.valores
        )
    return "refeita" if existente is not None else "gravada"


@dataclass(frozen=True, slots=True)
class Ponto:
    """Uma foto, convertida para moeda base.

    `None` quer dizer "não há número honesto para este dia": sem taxa válida,
    antes do início do caixa, ou antes da primeira posição. A curva fica com um
    buraco, e um buraco é visível.
    """

    data: date
    investimentos: Decimal | None
    patrimonio: Decimal | None


def curvas(desde: date | None = None) -> list[Ponto]:
    fotos = FotoDoPatrimonio.objects.order_by("data").prefetch_related("valores")
    if desde is not None:
        fotos = fotos.filter(data__gte=desde)
    fotos = list(fotos)
    moedas = {valor.moeda for foto in fotos for valor in foto.valores.all()}
    serie = SerieDeTaxas(moedas)

    pontos = []
    for foto in fotos:
        investido: dict[str, Decimal] = defaultdict(Decimal)
        inteiro: dict[str, Decimal] = defaultdict(Decimal)
        for valor in foto.valores.all():
            inteiro[valor.moeda] += valor.total
            if valor.papel == PAPEL_INVESTIMENTO:
                investido[valor.moeda] += valor.total
        pontos.append(
            Ponto(
                data=foto.data,
                investimentos=_em_moeda_base(investido, foto.data, serie),
                patrimonio=(
                    _em_moeda_base(inteiro, foto.data, serie)
                    if foto.data >= INICIO_DO_PATRIMONIO
                    else None
                ),
            )
        )
    return pontos


def _em_moeda_base(por_moeda: dict[str, Decimal], data: date, serie: SerieDeTaxas):
    totais = [{"moeda": moeda, "total": total} for moeda, total in sorted(por_moeda.items())]
    return converter_totais(totais, data, serie).total
