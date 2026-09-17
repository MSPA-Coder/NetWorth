"""A tela que responde "quanto eu tenho".

Ela mostra os totais **por moeda**, e não um número só: somar reais com dólares
exige uma taxa, e taxa é decisão datada — ela entra na etapa do câmbio, com data
e fonte visíveis em cada número convertido. Até lá, dois números certos valem
mais que um número redondo e errado.

E ela nunca mostra um total sem dizer de quantas fontes ele é feito. O estado de
cada fonte vem no mesmo objeto que os totais, de propósito: é a única defesa
contra o defeito que importa aqui — o patrimônio "cair" porque um dos sistemas
estava reiniciando, com o número continuando plausível.
"""

from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from consolidado.cambio import MOEDA_BASE, converter_totais
from consolidado.leitor import consolidar


@login_required
def patrimonio_view(request):
    bruto = (request.GET.get("data") or "").strip()
    referencia = None
    data_invalida = False
    if bruto:
        try:
            referencia = date.fromisoformat(bruto)
        except ValueError:
            data_invalida = True

    consolidado = consolidar(referencia)
    # A taxa é a do dia da foto, não a de hoje: converter o passado pela taxa de
    # hoje faria o patrimônio de março mudar toda manhã.
    conversao = converter_totais(consolidado.totais_por_moeda, referencia or timezone.localdate())
    return render(
        request,
        "consolidado/patrimonio.html",
        {
            "consolidado": consolidado,
            "conversao": conversao,
            "moeda_base": MOEDA_BASE,
            "referencia": referencia,
            "data_invalida": data_invalida,
            "por_instituicao": consolidado.por_instituicao(),
        },
    )
