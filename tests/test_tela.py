"""A tela do patrimônio.

O leitor garante que o total nunca existe sem o estado das fontes; estes testes
garantem que a **tela** não esconde esse estado. As duas metades são
necessárias: um objeto honesto renderizado por um template desatento produz
exatamente o número enganoso que este aplicativo não pode mostrar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client

from consolidado import leitor, views
from consolidado.fontes import Fonte

pytestmark = pytest.mark.django_db

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")


def linha(valor="100.00", moeda="BRL") -> leitor.Linha:
    return leitor.Linha(
        fonte=CB.nome,
        papel="caixa",
        titular="Mariano",
        instituicao="C6",
        descricao="Conta corrente",
        moeda=moeda,
        valor=Decimal(valor),
    )


@pytest.fixture
def logado():
    User.objects.create_user(username="quem", password="senha-longa-o-suficiente")
    cliente = Client()
    cliente.force_login(User.objects.get(username="quem"))
    return cliente


def com_consolidado(monkeypatch, consolidado):
    monkeypatch.setattr(views, "consolidar", lambda *_args, **_kwargs: consolidado)


def test_sem_sessao_a_tela_nao_abre():
    resposta = Client().get("/patrimonio/")

    assert resposta.status_code == 302
    assert "/login" in resposta["Location"]


def test_total_incompleto_e_dito_na_tela(logado, monkeypatch):
    """O teste que importa.

    Uma fonte fora do ar produz um total menor. Ele pode ser mostrado -- o que
    ele não pode é ser mostrado calado.
    """
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()]),
                leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="não respondeu"),
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()
    # O template quebra linha entre os números; o que importa é a frase, não a
    # formatação dela.
    corrido = " ".join(corpo.split())

    assert "não é o patrimônio inteiro" in corrido
    assert "feito de 1 de 2 fontes" in corrido
    assert "não respondeu" in corrido


def test_com_todas_as_fontes_o_aviso_some(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()]),
                leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[linha("50.00")]),
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "não é o patrimônio inteiro" not in corpo


def test_moedas_aparecem_separadas_e_com_o_simbolo_de_cada_uma(logado, monkeypatch):
    """Um "R$" carimbado num valor em dólar é erro que ninguém revisa."""
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(
                    fonte=CB,
                    estado=leitor.OK,
                    linhas=[linha("1234.56"), linha("7641.72", moeda="USD")],
                )
            ]
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "R$ 1.234,56" in corpo
    assert "US$ 7.641,72" in corpo
    assert "R$ 7.641,72" not in corpo


def test_o_total_em_moeda_base_traz_a_data_e_a_fonte_da_taxa(logado, monkeypatch):
    """Número convertido sem data e sem fonte não é verificável por ninguém."""
    from consolidado.models import TaxaDeCambio

    TaxaDeCambio.objects.create(
        moeda="USD", data=date(2026, 9, 16), taxa=Decimal("5.20"), fonte="yahoo"
    )
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(
                    fonte=CB, estado=leitor.OK, linhas=[linha("100.00", moeda="USD")]
                )
            ]
        ),
    )

    corrido = " ".join(logado.get("/patrimonio/", {"data": "2026-09-16"}).content.decode().split())

    assert "Total em BRL" in corrido
    assert "R$ 520,00" in corrido
    assert "16/09/2026" in corrido
    assert "yahoo" in corrido


def test_sem_taxa_nao_aparece_total_em_moeda_base(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(
                    fonte=CB, estado=leitor.OK, linhas=[linha("100.00", moeda="USD")]
                )
            ]
        ),
    )

    corrido = " ".join(logado.get("/patrimonio/", {"data": "2026-09-16"}).content.decode().split())

    assert "Total em BRL" not in corrido
    assert "Sem total em BRL: sem taxa para USD" in corrido
    assert "US$ 100,00" in corrido


def test_fonte_configurada_pela_metade_aparece(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])],
            incompletas=["Renda Variável"],
        ),
    )

    corpo = logado.get("/patrimonio/").content.decode()

    assert "Configurada pela metade" in corpo
    assert "Renda Variável" in corpo


def test_data_invalida_avisa_em_vez_de_estourar(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    resposta = logado.get("/patrimonio/", {"data": "30/06/2026"})

    assert resposta.status_code == 200
    assert "Data inválida" in resposta.content.decode()


def test_a_tela_traz_os_cabecalhos_defensivos(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    resposta = logado.get("/patrimonio/")

    assert "default-src 'self'" in resposta["Content-Security-Policy"]
    assert resposta["X-Frame-Options"] == "DENY"


def test_lacuna_de_uma_fonte_aparece_no_estado_e_no_aviso(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()]),
                leitor.Leitura(
                    fonte=CRV,
                    estado=leitor.OK,
                    linhas=[linha("50.00")],
                    lacunas=["1 posição sem cotação ficou de fora"],
                ),
            ]
        ),
    )

    corrido = " ".join(logado.get("/patrimonio/").content.decode().split())

    assert "não é o patrimônio inteiro" in corrido
    assert "1 posição sem cotação ficou de fora" in corrido


# --- O histórico ---------------------------------------------------------------


def _foto(dia: str, investido: str, caixa: str = "0.00"):
    from django.utils import timezone

    from consolidado.models import FotoDoPatrimonio, ValorDaFoto

    foto = FotoDoPatrimonio.objects.create(data=date.fromisoformat(dia), tirada_em=timezone.now())
    ValorDaFoto.objects.create(
        foto=foto, fonte="CRV", papel="investimento", instituicao="Genial",
        moeda="BRL", total=Decimal(investido), linhas=1,
    )
    ValorDaFoto.objects.create(
        foto=foto, fonte="CB", papel="caixa", instituicao="C6",
        moeda="BRL", total=Decimal(caixa), linhas=1,
    )


def test_historico_exige_sessao():
    resposta = Client().get("/patrimonio/historico/")

    assert resposta.status_code == 302
    assert "/login" in resposta["Location"]


def test_historico_sem_foto_explica_como_elas_nascem(logado):
    corpo = logado.get("/patrimonio/historico/").content.decode()

    assert "Ainda não há foto" in corpo
    assert "registrar_foto" in corpo
    assert "<svg" not in corpo


def test_historico_desenha_as_curvas_e_lista_o_fim_de_cada_mes(logado):
    _foto("2025-12-31", "150000.00")
    _foto("2026-01-30", "160000.00", "50000.00")
    _foto("2026-01-31", "165000.00", "55000.00")
    _foto("2026-02-27", "170000.00", "60000.00")

    corpo = logado.get("/patrimonio/historico/").content.decode()
    corrido = " ".join(corpo.split())

    assert "<svg" in corpo
    assert 'class="curva curva-patrimonio"' in corpo
    assert 'class="curva curva-investimentos"' in corpo
    # Janeiro aparece uma vez, pela última foto do mês.
    assert "31/01/2026" in corrido
    assert "30/01/2026" not in corrido
    assert "R$ 230.000,00" in corrido  # patrimônio de fevereiro
    assert "R$ 220.000,00" in corrido  # patrimônio de janeiro
    # Dezembro de 2025 tem investimentos, mas não patrimônio: o caixa não existia.
    assert "R$ 150.000,00" in corrido


def test_historico_nao_tem_script_nem_estilo_embutido(logado):
    """A CSP é fechada: um `style=` ou um `<script>` seriam bloqueados."""
    _foto("2026-01-31", "1.00", "1.00")
    _foto("2026-02-27", "2.00", "2.00")

    corpo = logado.get("/patrimonio/historico/").content.decode()

    assert "<script" not in corpo
    assert "style=" not in corpo


def test_coordenadas_do_grafico_usam_ponto_decimal(logado):
    """Em pt-BR o Django escreve 123,4 -- e `x="123,4"` o SVG lê como duas
    coordenadas: o primeiro caractere do rótulo vai para 123, o segundo para 4.
    Os rótulos saíram picados assim na primeira versão."""
    import re

    _foto("2025-12-31", "150000.00")
    _foto("2026-02-27", "170000.00", "60000.00")

    corpo = logado.get("/patrimonio/historico/").content.decode()

    assert re.search(r'\s(?:x|y|x1|x2|y1|y2)="[^"]*,', corpo) is None
    assert re.search(r'\sd="[^"]*,', corpo) is None


@pytest.mark.parametrize("caminho", ["/patrimonio/", "/patrimonio/historico/"])
def test_comentario_de_template_nao_vaza_para_a_pagina(logado, monkeypatch, caminho):
    """`{# #}` do Django vale para UMA linha. Em várias linhas ele vira texto, e
    o topo da página mostrava o comentário do `base.html`."""
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    corpo = logado.get(caminho).content.decode()

    assert "{#" not in corpo
    assert "#}" not in corpo


def test_periodo_desconhecido_vira_tudo(logado):
    _foto("2025-06-30", "1.00")
    _foto("2026-06-30", "2.00", "1.00")

    corpo = logado.get("/patrimonio/historico/", {"periodo": "qualquer"}).content.decode()
    so_2026 = logado.get("/patrimonio/historico/", {"periodo": "desde-2026"}).content.decode()

    assert "30/06/2025" in corpo
    assert "30/06/2025" not in so_2026


def test_saude_responde_sem_sessao():
    resposta = Client().get("/health/")

    assert resposta.status_code == 200
    assert resposta.json() == {"servico": "networth", "status": "ok"}
