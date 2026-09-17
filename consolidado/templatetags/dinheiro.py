"""Formatar dinheiro na tela, em pt-BR, com o símbolo da moeda que ele é.

A formatação em si vem de `sharedauth.formatting`, a mesma dos aplicativos
irmãos -- ela é o contrato compartilhado, e reimplementá-la aqui produziria
"R$ 1,234.56" num dia de pressa.

O símbolo vem da **moeda da linha**, nunca de um padrão: neste aplicativo há
mais de uma moeda na mesma tela por natureza, e um "R$" carimbado num valor em
dólar é o tipo de erro que ninguém revisa.
"""

from __future__ import annotations

from django import template
from sharedauth.formatting import moeda as formatar_moeda

register = template.Library()

#: Só o que os dois sistemas publicam hoje. Moeda desconhecida aparece com o
#: próprio código (`CHF 1.234,56`), que é feio e correto -- melhor do que
#: escolher um símbolo por conta própria.
SIMBOLOS = {"BRL": "R$", "USD": "US$", "EUR": "€"}


@register.filter
def dinheiro(valor, codigo_da_moeda: str = "BRL") -> str:
    return formatar_moeda(valor, simbolo=SIMBOLOS.get(codigo_da_moeda, codigo_da_moeda))
