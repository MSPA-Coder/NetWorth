"""Tira a foto diária do patrimônio.

    manage.py registrar_foto                          # os últimos 7 dias fechados
    manage.py registrar_foto --data 2026-09-16        # um dia
    manage.py registrar_foto --desde 2022-05-09       # a história inteira, até ontem
    manage.py registrar_foto --desde 2025-12-31 --refazer

SEM ARGUMENTO, ELE REFAZ UMA SEMANA

A foto de ontem não é a última palavra sobre ontem: uma despesa de ontem pode
ser lançada amanhã. Por isso a execução diária refaz os últimos sete dias, e
não só o de ontem. Refazer só troca a foto quando a leitura nova também está
completa. Com uma fonte fora do ar, a foto anterior fica.

Com `--data` ou `--desde`, a data que já tem foto é pulada, e `--refazer`
troca. É o que se usa depois de corrigir um saldo inicial.

HOJE NÃO TEM FOTO

O dia corrente ainda não fechou. A foto de hoje teria o valor do instante, e é
o mesmo defeito que a série de câmbio já pagou.

Termina com erro quando alguma data ficou sem foto, para o timer do servidor
alertar.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from consolidado import fotos
from consolidado.models import FotoDoPatrimonio

JANELA_PADRAO_EM_DIAS = 7
AVISAR_A_CADA = 100


def _data(texto: str, opcao: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError as erro:
        raise CommandError(f"{opcao}: data inválida ({texto!r}); use AAAA-MM-DD.") from erro


class Command(BaseCommand):
    help = "Grava a foto diária do patrimônio (só de dias fechados e só completa)."

    def add_arguments(self, parser):
        parser.add_argument("--data", help="Um dia só (AAAA-MM-DD).")
        parser.add_argument("--desde", help="Primeiro dia de um intervalo (AAAA-MM-DD).")
        parser.add_argument("--ate", help="Último dia do intervalo; padrão: ontem.")
        parser.add_argument(
            "--dias",
            type=int,
            default=JANELA_PADRAO_EM_DIAS,
            help="Sem --data/--desde: quantos dias fechados refazer (padrão: 7).",
        )
        parser.add_argument(
            "--refazer",
            action="store_true",
            help="Com --data/--desde: troca a foto que já existe.",
        )

    def handle(self, *args, **opcoes):
        ontem = timezone.localdate() - timedelta(days=1)
        if opcoes["data"] and opcoes["desde"]:
            raise CommandError("Use --data ou --desde, não os dois.")

        if opcoes["data"]:
            inicio = fim = _data(opcoes["data"], "--data")
            refazer = opcoes["refazer"]
        elif opcoes["desde"]:
            inicio = _data(opcoes["desde"], "--desde")
            fim = _data(opcoes["ate"], "--ate") if opcoes["ate"] else ontem
            refazer = opcoes["refazer"]
        else:
            if opcoes["dias"] < 1:
                raise CommandError("--dias precisa ser pelo menos 1.")
            inicio, fim = ontem - timedelta(days=opcoes["dias"] - 1), ontem
            refazer = True

        if fim > ontem:
            raise CommandError(
                f"{fim:%d/%m/%Y} ainda não fechou: a última data com foto possível é "
                f"{ontem:%d/%m/%Y}."
            )
        if inicio > fim:
            raise CommandError("O intervalo está invertido.")

        contagem = {"gravada": 0, "refeita": 0, "mantida": 0, "pulada": 0}
        incompletas: list[tuple[date, str]] = []
        total = (fim - inicio).days + 1
        for indice in range(total):
            dia = inicio + timedelta(days=indice)
            if not refazer and FotoDoPatrimonio.objects.filter(data=dia).exists():
                contagem["pulada"] += 1
                continue
            lida = fotos.ler(dia)
            if not lida.completa:
                incompletas.append((dia, lida.motivo))
                self.stderr.write(f"{dia:%d/%m/%Y}: sem foto -- {lida.motivo}")
                continue
            contagem[fotos.gravar(lida, refazer=refazer)] += 1
            if total > AVISAR_A_CADA and (indice + 1) % AVISAR_A_CADA == 0:
                self.stdout.write(f"... {indice + 1} de {total} ({dia:%d/%m/%Y})")

        self.stdout.write(
            f"Fotos de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}: "
            f"{contagem['gravada']} gravadas, {contagem['refeita']} refeitas, "
            f"{contagem['pulada']} já existiam, {len(incompletas)} sem foto."
        )
        if incompletas:
            raise CommandError(
                f"{len(incompletas)} data(s) ficaram sem foto; a primeira foi "
                f"{incompletas[0][0]:%d/%m/%Y} ({incompletas[0][1]})."
            )
