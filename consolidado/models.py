"""O que este aplicativo guarda -- e é pouco, de propósito.

Ele não copia conta, posição nem lançamento: isso é dos outros dois sistemas, e
uma cópia aqui seria uma segunda verdade sobre o mesmo dinheiro. O que ele
guarda é o que **só ele** precisa: a taxa de câmbio de cada dia, com data e
fonte, porque converter é decisão dele e não dos publicadores.
"""

from __future__ import annotations

from django.db import models


class TaxaDeCambio(models.Model):
    """Quanto vale uma unidade de `moeda` em moeda base, num dia, por uma fonte.

    TRÊS COISAS QUE ESTA TABELA IMPÕE, E POR QUÊ

    1. **A data é do fechamento, não da consulta.** `obtida_em` registra quando
       a linha entrou aqui, e serve para auditar a coleta -- mas quem manda na
       conversão é `data`. Converter uma foto de março pela taxa de hoje
       reescreveria a história a cada dia;
    2. **A fonte fica na linha.** Yahoo hoje, PTAX quando houver declaração de
       imposto. As duas podem conviver para o mesmo dia, e por isso a unicidade
       inclui a fonte: sobrescrever uma pela outra apagaria a que sustentava um
       número já mostrado;
    3. **Taxa é positiva.** A `CheckConstraint` é a rede: nem toda escrita passa
       pelo comando de coleta.
    """

    moeda = models.CharField(max_length=3)
    data = models.DateField()
    # 8 casas: câmbio não é dinheiro, é razão entre dinheiros, e arredondá-lo a
    # dois centavos antes da multiplicação joga fora precisão que o total em
    # moeda base ainda vai usar.
    taxa = models.DecimalField(max_digits=18, decimal_places=8)
    fonte = models.CharField(max_length=20)
    obtida_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "taxa_de_cambio"
        ordering = ["moeda", "-data"]
        constraints = [
            models.UniqueConstraint(
                fields=["moeda", "data", "fonte"], name="uq_taxa_moeda_data_fonte"
            ),
            models.CheckConstraint(condition=models.Q(taxa__gt=0), name="ck_taxa_positiva"),
        ]
        indexes = [models.Index(fields=["moeda", "data"])]

    def __str__(self) -> str:
        return f"{self.moeda} {self.data}: {self.taxa} ({self.fonte})"
