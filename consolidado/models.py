"""O que este aplicativo guarda -- e é pouco, de propósito.

Ele não copia conta, posição nem lançamento: isso é dos outros dois sistemas, e
uma cópia aqui seria uma segunda verdade sobre o mesmo dinheiro. O que ele
guarda é o que **só ele** precisa:

- a taxa de câmbio de cada dia, com data e fonte, porque converter é decisão
  dele e não dos publicadores;
- a foto diária do patrimônio, em totais e nunca em detalhe, porque desenhar
  anos de série perguntando uma data de cada vez às fontes seria absurdo.
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


class FotoDoPatrimonio(models.Model):
    """O patrimônio no fim de um dia: completo, ou não existe.

    TRÊS REGRAS

    1. **Só entra foto completa.** Todas as fontes conhecidas responderam, para
       a data pedida, sem lacuna. Uma foto feita com o Renda Variável fora do ar
       viraria um degrau permanente no gráfico, com cara de queda real;
    2. **Guarda totais, não detalhe.** Por fonte, instituição e moeda. O
       detalhe é sempre buscado ao vivo, no sistema que é dono dele;
    3. **A foto pode ser refeita.** Ao contrário da taxa de câmbio, ela é um
       resumo do que as fontes sabem, e as fontes corrigem o passado: um saldo
       inicial acertado ou um lançamento registrado dias depois mudam o
       patrimônio de uma data já fotografada. Refazer troca a foto inteira, e
       só quando a leitura nova também está completa.

    A conversão para moeda base **não** é gravada: ela sai da série de câmbio
    na hora de mostrar, pela taxa da data da foto.
    """

    data = models.DateField(unique=True)
    tirada_em = models.DateTimeField()

    class Meta:
        db_table = "foto_do_patrimonio"
        ordering = ["data"]

    def __str__(self) -> str:
        return f"foto de {self.data}"


class ValorDaFoto(models.Model):
    """Um total dentro de uma foto: quanto uma fonte publicou numa instituição,
    numa moeda."""

    foto = models.ForeignKey(FotoDoPatrimonio, on_delete=models.CASCADE, related_name="valores")
    fonte = models.CharField(max_length=10)
    papel = models.CharField(max_length=20)
    instituicao = models.CharField(max_length=120)
    moeda = models.CharField(max_length=3)
    # Dinheiro: duas casas, como publicado. Os publicadores já arredondam cada
    # linha, e somar linhas arredondadas mantém as duas casas.
    total = models.DecimalField(max_digits=20, decimal_places=2)
    linhas = models.PositiveIntegerField()

    class Meta:
        db_table = "valor_da_foto"
        ordering = ["foto", "fonte", "instituicao", "moeda"]
        constraints = [
            models.UniqueConstraint(
                fields=["foto", "fonte", "instituicao", "moeda"],
                name="uq_valor_foto_fonte_instituicao_moeda",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.foto.data} {self.fonte} {self.instituicao} {self.moeda} {self.total}"
