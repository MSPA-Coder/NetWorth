"""Preenche a série de câmbio até o último dia já fechado (ontem).

Ele **acrescenta**, nunca reescreve: uma taxa já gravada fica como está, mesmo
que a fonte devolva outro valor para o mesmo dia. A razão é auditoria -- aquele
número já pode ter sustentado um patrimônio que alguém olhou, e trocá-lo em
silêncio faria a mesma foto valer duas coisas diferentes. Se uma taxa estiver
errada, corrigi-la é um ato deliberado, não efeito colateral de uma coleta.

É por isso que o dia corrente fica de fora. O Yahoo devolve também o dia de
hoje, com o preço do momento no lugar do fechamento; gravado, aquele valor
parcial nunca mais seria corrigido. A foto de hoje usa a taxa de ontem, que a
regra de defasagem de `cambio.py` aceita.

Quais moedas: por padrão, as que aparecem nas fontes agora -- perguntar a elas é
mais honesto do que manter uma lista aqui, que envelheceria calada no dia em que
uma conta em euro fosse aberta. `--moeda` cobre o caso de as fontes estarem fora
do ar.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from consolidado.cambio import MOEDA_BASE
from consolidado.leitor import consolidar
from consolidado.models import TaxaDeCambio
from consolidado.yahoo import FONTE, ColetaDeCambioError, buscar_serie

#: Quando a tabela está vazia e ninguém disse desde quando, começa aqui. É o
#: corte do caixa das corretoras (31/12/2025), e portanto o primeiro dia em que
#: existe patrimônio a converter.
PRIMEIRO_DIA_PADRAO = date(2025, 12, 31)


class Command(BaseCommand):
    help = "Busca os fechamentos diários de câmbio e preenche o que faltar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--moeda",
            action="append",
            default=[],
            help="Moeda a atualizar (repetível). Sem isto, as que as fontes estiverem mostrando.",
        )
        parser.add_argument(
            "--desde",
            default=None,
            help="Primeiro dia a buscar (AAAA-MM-DD). Sem isto, o dia seguinte à última taxa.",
        )

    def handle(self, *args, **opcoes):
        hoje = timezone.localdate()
        moedas = [m.upper() for m in opcoes["moeda"]] or self._moedas_em_uso()
        moedas = [m for m in dict.fromkeys(moedas) if m != MOEDA_BASE]
        if not moedas:
            self.stdout.write("Nada a atualizar: só há patrimônio em moeda base.")
            return

        inicio_pedido = None
        if opcoes["desde"]:
            try:
                inicio_pedido = date.fromisoformat(opcoes["desde"])
            except ValueError as erro:
                raise CommandError("--desde inválido: use AAAA-MM-DD.") from erro

        for moeda in moedas:
            self._atualizar(moeda, inicio_pedido, hoje)

    def _moedas_em_uso(self) -> list[str]:
        consolidado = consolidar()
        if not consolidado.fontes_que_responderam:
            raise CommandError(
                "Nenhuma fonte respondeu, então não dá para saber quais moedas atualizar. "
                "Informe com --moeda."
            )
        return [bloco["moeda"] for bloco in consolidado.totais_por_moeda]

    def _atualizar(self, moeda: str, inicio_pedido: date | None, hoje: date) -> None:
        ultimo_fechado = hoje - timedelta(days=1)
        ultima = TaxaDeCambio.objects.filter(moeda=moeda).order_by("-data").first()
        inicio = inicio_pedido or (
            ultima.data + timedelta(days=1) if ultima else PRIMEIRO_DIA_PADRAO
        )
        if inicio > ultimo_fechado:
            self.stdout.write(
                f"{moeda}: já está em dia (última em {ultima.data if ultima else 'nenhuma'})."
            )
            return

        try:
            fechamentos = buscar_serie(moeda, MOEDA_BASE, inicio, ultimo_fechado)
        except ColetaDeCambioError as erro:
            # Falha de coleta não é falha do aplicativo: a tela continua
            # mostrando os totais por moeda, e dizendo que não converteu.
            self.stderr.write(f"{moeda}: {erro}")
            return

        novas = 0
        ja_havia = 0
        for fechamento in fechamentos:
            # O período pedido já termina ontem, mas a resposta pode trazer o
            # dia corrente mesmo assim. A guarda fica aqui, onde se grava.
            if fechamento.data > ultimo_fechado:
                continue
            _linha, criada = TaxaDeCambio.objects.get_or_create(
                moeda=moeda,
                data=fechamento.data,
                fonte=FONTE,
                defaults={"taxa": fechamento.taxa},
            )
            novas += int(criada)
            ja_havia += int(not criada)

        recente = TaxaDeCambio.objects.filter(moeda=moeda).order_by("-data").first()
        self.stdout.write(
            f"{moeda}: {novas} nova{'s' if novas != 1 else ''}, {ja_havia} já existia"
            f"{'m' if ja_havia != 1 else ''} "
            f"(de {inicio} a {ultimo_fechado}; última agora: "
            f"{recente.data if recente else 'nenhuma'} = {recente.taxa if recente else '-'})"
        )
