"""A foto diária do patrimônio, e as duas curvas que saem dela.

O DEFEITO QUE ESTE ARQUIVO EXISTE PARA IMPEDIR

Uma foto tirada com o Renda Variável fora do ar vira um degrau permanente no
gráfico: o patrimônio "caiu" trinta por cento num dia e nunca mais voltou
àquela data. Na tela de hoje, a fonte fora do ar é dita ao lado do número; num
gráfico de anos, ninguém lê o estado de cada ponto. Por isso a foto incompleta
não é gravada -- nem para trocar uma foto boa que já existia.

O segundo defeito é desenhar um patrimônio antes de o caixa existir. Antes de
2026 o Controle Bancário responde com zero contas, e a "curva do patrimônio"
seria só a parte investida.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from consolidado import fotos, leitor
from consolidado.fontes import Fonte
from consolidado.management.commands import registrar_foto
from consolidado.models import FotoDoPatrimonio, TaxaDeCambio, ValorDaFoto

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(
    apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t"
)


def linha(fonte: Fonte, valor: str, moeda: str = "BRL", instituicao: str = "Genial"):
    return leitor.Linha(
        fonte=fonte.nome,
        papel=fonte.papel,
        titular="Maridito",
        instituicao=instituicao,
        descricao="algo",
        moeda=moeda,
        valor=Decimal(valor),
    )


def respondeu(fonte: Fonte, dia: date, *linhas, lacunas=()):
    return leitor.Leitura(
        fonte=fonte,
        estado=leitor.OK,
        data_de_referencia=dia,
        linhas=list(linhas),
        lacunas=list(lacunas),
    )


def fontes_respondem(monkeypatch, montar):
    """`montar(dia)` devolve as leituras daquele dia; as datas pedidas ficam na lista."""
    pedidas: list[date] = []

    def consolidar(dia):
        pedidas.append(dia)
        return leitor.Consolidado(leituras=montar(dia))

    monkeypatch.setattr(fotos, "consolidar", consolidar)
    return pedidas


def completas(dia):
    return [
        respondeu(CB, dia, linha(CB, "1000.00", instituicao="C6"), linha(CB, "10.00", "USD", "Avenue")),
        respondeu(
            CRV,
            dia,
            linha(CRV, "300.00"),
            linha(CRV, "200.00"),
            linha(CRV, "5.00", "USD", "Avenue"),
        ),
    ]


def hoje_e(monkeypatch, dia: date):
    monkeypatch.setattr(registrar_foto.timezone, "localdate", lambda: dia)


# --- Ler e gravar -----------------------------------------------------------


def test_foto_completa_guarda_totais_por_fonte_instituicao_e_moeda(monkeypatch):
    fontes_respondem(monkeypatch, completas)

    lida = fotos.ler(date(2026, 9, 16))
    assert fotos.gravar(lida, refazer=False) == "gravada"

    valores = {
        (v.fonte, v.papel, v.instituicao, v.moeda): (v.total, v.linhas)
        for v in ValorDaFoto.objects.filter(foto__data=date(2026, 9, 16))
    }
    assert valores == {
        ("CB", "caixa", "Avenue", "USD"): (Decimal("10.00"), 1),
        ("CB", "caixa", "C6", "BRL"): (Decimal("1000.00"), 1),
        ("CRV", "investimento", "Avenue", "USD"): (Decimal("5.00"), 1),
        ("CRV", "investimento", "Genial", "BRL"): (Decimal("500.00"), 2),
    }


def test_fonte_fora_do_ar_nao_produz_foto(monkeypatch):
    """O teste central: esta foto seria um degrau permanente no gráfico."""
    fontes_respondem(
        monkeypatch,
        lambda dia: [
            respondeu(CB, dia, linha(CB, "1000.00")),
            leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="não respondeu"),
        ],
    )

    lida = fotos.ler(date(2026, 9, 16))

    assert not lida.completa
    assert "Renda Variável: não respondeu" in lida.motivo
    with pytest.raises(ValueError):
        fotos.gravar(lida, refazer=True)
    assert not FotoDoPatrimonio.objects.exists()


def test_fonte_que_nem_esta_configurada_tambem_impede_a_foto(monkeypatch):
    """Sem endereço nem token, a fonte não aparece em lugar nenhum -- e a foto
    sairia feita só da outra, com cara de completa."""
    fontes_respondem(monkeypatch, lambda dia: [respondeu(CB, dia, linha(CB, "1000.00"))])

    lida = fotos.ler(date(2026, 9, 16))

    assert "Controle de Renda Variável não está configurada" in lida.motivo


def test_posicao_sem_cotacao_impede_a_foto(monkeypatch):
    fontes_respondem(
        monkeypatch,
        lambda dia: [
            respondeu(CB, dia, linha(CB, "1000.00")),
            respondeu(CRV, dia, linha(CRV, "10.00"), lacunas=["1 posição sem cotação ficou de fora"]),
        ],
    )

    lida = fotos.ler(date(2026, 9, 16))

    assert not lida.completa
    assert "sem cotação" in lida.motivo


def test_refazer_troca_a_foto_inteira(monkeypatch):
    dia = date(2026, 9, 16)
    fontes_respondem(monkeypatch, completas)
    fotos.gravar(fotos.ler(dia), refazer=False)

    fontes_respondem(monkeypatch, lambda d: [respondeu(CB, d, linha(CB, "7.00")), respondeu(CRV, d)])
    assert fotos.gravar(fotos.ler(dia), refazer=False) == "mantida"
    assert ValorDaFoto.objects.filter(foto__data=dia).count() == 4

    assert fotos.gravar(fotos.ler(dia), refazer=True) == "refeita"
    (valor,) = ValorDaFoto.objects.filter(foto__data=dia)
    assert valor.total == Decimal("7.00")
    assert FotoDoPatrimonio.objects.count() == 1


# --- As curvas ---------------------------------------------------------------


def foto(dia: str, *valores):
    registro = FotoDoPatrimonio.objects.create(data=date.fromisoformat(dia), tirada_em=timezone.now())
    for fonte, papel, moeda, total in valores:
        ValorDaFoto.objects.create(
            foto=registro,
            fonte=fonte,
            papel=papel,
            instituicao="X",
            moeda=moeda,
            total=Decimal(total),
            linhas=1,
        )


def taxa(dia: str, valor: str):
    TaxaDeCambio.objects.create(
        moeda="USD", data=date.fromisoformat(dia), taxa=Decimal(valor), fonte="yahoo"
    )


def test_antes_do_caixa_so_existe_a_curva_de_investimentos():
    foto("2025-12-30", ("CB", "caixa", "BRL", "0.00"), ("CRV", "investimento", "BRL", "500.00"))
    foto("2026-01-02", ("CB", "caixa", "BRL", "100.00"), ("CRV", "investimento", "BRL", "500.00"))

    antes, depois = fotos.curvas()

    assert antes.investimentos == Decimal("500.00")
    assert antes.patrimonio is None
    assert depois.investimentos == Decimal("500.00")
    assert depois.patrimonio == Decimal("600.00")


def test_cada_foto_e_convertida_pela_taxa_do_seu_dia():
    """Converter março pela taxa de hoje faria março mudar toda manhã."""
    taxa("2026-03-31", "5.00")
    taxa("2026-09-16", "6.00")
    foto("2026-03-31", ("CRV", "investimento", "USD", "10.00"))
    foto("2026-09-16", ("CRV", "investimento", "USD", "10.00"))

    marco, setembro = fotos.curvas()

    assert marco.investimentos == Decimal("50.00")
    assert setembro.investimentos == Decimal("60.00")


def test_sem_taxa_valida_o_dia_fica_sem_numero():
    taxa("2026-01-02", "5.00")
    foto("2026-03-31", ("CB", "caixa", "BRL", "100.00"), ("CRV", "investimento", "USD", "10.00"))

    (ponto,) = fotos.curvas()

    assert ponto.investimentos is None
    assert ponto.patrimonio is None


def test_caixa_em_dolar_entra_no_patrimonio_e_nao_nos_investimentos():
    taxa("2026-03-31", "5.00")
    foto("2026-03-31", ("CB", "caixa", "USD", "2.00"), ("CRV", "investimento", "BRL", "100.00"))

    (ponto,) = fotos.curvas()

    assert ponto.investimentos == Decimal("100.00")
    assert ponto.patrimonio == Decimal("110.00")


def test_recorte_por_periodo():
    foto("2025-06-30", ("CRV", "investimento", "BRL", "1.00"))
    foto("2026-06-30", ("CRV", "investimento", "BRL", "2.00"))

    assert [p.data for p in fotos.curvas(desde=date(2026, 1, 1))] == [date(2026, 6, 30)]


# --- O comando ---------------------------------------------------------------


def test_sem_argumento_refaz_os_ultimos_sete_dias_fechados(monkeypatch):
    hoje_e(monkeypatch, date(2026, 9, 17))
    pedidas = fontes_respondem(monkeypatch, completas)

    call_command("registrar_foto")

    assert pedidas == [date(2026, 9, dia) for dia in range(10, 17)]
    assert FotoDoPatrimonio.objects.count() == 7


def test_hoje_nao_tem_foto(monkeypatch):
    """O dia não fechou: a foto teria o valor do instante."""
    hoje_e(monkeypatch, date(2026, 9, 17))
    fontes_respondem(monkeypatch, completas)

    with pytest.raises(CommandError, match="ainda não fechou"):
        call_command("registrar_foto", "--data", "2026-09-17")


def test_intervalo_pula_o_que_ja_tem_foto_e_refazer_troca(monkeypatch):
    hoje_e(monkeypatch, date(2026, 9, 17))
    pedidas = fontes_respondem(monkeypatch, completas)
    call_command("registrar_foto", "--data", "2026-09-10")
    pedidas.clear()

    call_command("registrar_foto", "--desde", "2026-09-10", "--ate", "2026-09-11")
    assert pedidas == [date(2026, 9, 11)]

    pedidas.clear()
    call_command("registrar_foto", "--desde", "2026-09-10", "--ate", "2026-09-11", "--refazer")
    assert pedidas == [date(2026, 9, 10), date(2026, 9, 11)]


def test_dia_sem_foto_termina_em_erro_e_nao_apaga_a_foto_boa(monkeypatch):
    """O erro é o que faz o timer do servidor alertar. E a foto boa que já
    existia continua lá: refazer só troca por outra completa."""
    hoje_e(monkeypatch, date(2026, 9, 17))
    fontes_respondem(monkeypatch, completas)
    call_command("registrar_foto", "--data", "2026-09-16")

    fontes_respondem(
        monkeypatch,
        lambda dia: [
            respondeu(CB, dia),
            leitor.Leitura(fonte=CRV, estado=leitor.FALHOU, motivo="token recusado pela fonte"),
        ],
    )
    with pytest.raises(CommandError, match="sem foto"):
        call_command("registrar_foto", "--dias", "1")

    assert ValorDaFoto.objects.filter(foto__data=date(2026, 9, 16)).count() == 4
