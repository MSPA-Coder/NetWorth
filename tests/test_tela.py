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
    monkeypatch.setattr(views, "consolidar_v2", lambda *_args, **_kwargs: consolidado)


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


def test_composicao_so_aparece_quando_o_total_inteiro_pode_ser_convertido(logado, monkeypatch):
    """Uma barra sem a moeda que ficou de fora é só outro total enganoso."""
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(
                    fonte=CB, estado=leitor.OK, linhas=[linha("100.00", moeda="BRL")]
                ),
                leitor.Leitura(
                    fonte=CRV,
                    estado=leitor.OK,
                    linhas=[
                        leitor.Linha(
                            fonte=CRV.nome,
                            papel="investimento",
                            titular="Mariano",
                            instituicao="Avenue",
                            descricao="AAPL",
                            moeda="USD",
                            valor=Decimal("20.00"),
                            mercado="EUA",
                        )
                    ],
                ),
            ]
        ),
    )

    resposta = logado.get("/patrimonio/", {"data": "2026-09-16"})

    assert resposta.context["composicoes"] == []
    assert "Composição" not in resposta.content.decode()
    assert "US$ 20,00" in resposta.content.decode()


def test_composicoes_fecham_cem_por_cento_com_a_conversao_completa(logado, monkeypatch):
    from consolidado.models import TaxaDeCambio

    TaxaDeCambio.objects.create(
        moeda="USD", data=date(2026, 9, 16), taxa=Decimal("5.00"), fonte="yahoo"
    )
    # Uma posição em dólar dá a mesma metade do total; assim a composição
    # também confirma os agrupamentos por sistema, instituição e mercado.
    consolidado = leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("100.00")]),
            leitor.Leitura(
                fonte=CRV,
                estado=leitor.OK,
                linhas=[
                    leitor.Linha(
                        fonte=CRV.nome,
                        papel="investimento",
                        titular="Mariano",
                        instituicao="Avenue",
                        descricao="AAPL",
                        moeda="USD",
                        valor=Decimal("20.00"),
                        mercado="EUA",
                    )
                ],
            ),
        ]
    )
    com_consolidado(monkeypatch, consolidado)

    resposta = logado.get("/patrimonio/", {"data": "2026-09-16"})
    composicoes = resposta.context["composicoes"]

    assert "Composição" in resposta.content.decode()
    assert "Por mercado" in resposta.content.decode()
    assert all(sum(fatia["percentual"] for fatia in fatias) == Decimal("100.00") for _, fatias in composicoes)


def test_cartoes_e_composicao_nao_exigem_script_nem_estilo_embutido(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])]))

    corpo = logado.get("/patrimonio/").content.decode()

    assert '<script src="/static/js/networth-navigation.' in corpo
    assert ' defer></script>' in corpo
    assert "style=" not in corpo


def test_cartoes_usam_o_css_compartilhado(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])]))

    corpo = logado.get("/patrimonio/").content.decode()

    assert "sharedauth-ui." in corpo
    assert 'class="sa-cartao' in corpo
    assert 'class="sa-metrica ' in corpo or 'class="sa-metrica"' in corpo


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
    tabela_mensal = corrido.split("<h2>Fim de cada mês</h2>", 1)[1]
    assert "31/01/2026" in tabela_mensal
    assert "30/01/2026" not in tabela_mensal
    assert "R$ 230.000,00" in corrido  # patrimônio de fevereiro
    assert "R$ 220.000,00" in corrido  # patrimônio de janeiro
    # Dezembro de 2025 tem investimentos, mas não patrimônio: o caixa não existia.
    assert "R$ 150.000,00" in corrido


def test_historico_nao_tem_script_nem_estilo_embutido(logado):
    """A CSP é fechada: comportamento fica em arquivo externo, não inline."""
    _foto("2026-01-31", "1.00", "1.00")
    _foto("2026-02-27", "2.00", "2.00")

    corpo = logado.get("/patrimonio/historico/").content.decode()

    assert '<script src="/static/js/networth-navigation.' in corpo
    assert ' defer></script>' in corpo
    assert "<script>" not in corpo
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


def test_a_linha_com_link_leva_ao_sistema_de_origem(logado, monkeypatch):
    com_link = leitor.Linha(
        fonte=CB.nome,
        papel="caixa",
        titular="Mariano",
        instituicao="C6",
        descricao="Conta corrente",
        moeda="BRL",
        valor=Decimal("100.00"),
        link="http://cb.teste/transactions/?account_id=7&mode=realizado",
    )
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[com_link])]),
    )

    html = logado.get("/patrimonio/").content.decode()

    assert (
        '<a href="http://cb.teste/transactions/?account_id=7&amp;mode=realizado">Conta corrente</a>'
        in html
    )


def test_a_linha_sem_link_continua_so_texto(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])]),
    )

    html = logado.get("/patrimonio/").content.decode()

    assert '<span class="linha">Conta corrente · ' in html


def test_dashboard_reune_periodos_grafico_e_privacidade(logado, monkeypatch):
    _foto("2026-01-31", "100.00", "50.00")
    _foto("2026-02-28", "120.00", "55.00")
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("60.00")])]
        ),
    )

    corpo = logado.get(
        "/patrimonio/", {"periodo": "tudo", "data": "2026-03-01"}
    ).content.decode()

    assert "Dashboard" in corpo
    assert 'aria-label="Período do dashboard"' in corpo
    assert "1 mês" in corpo
    assert "5 anos" in corpo
    assert "Ocultar valores" in corpo
    assert "Contas e instituições" in corpo
    assert "Alocação por classe" in corpo
    assert "Patrimônio e investimentos" in corpo
    assert "31/01/2026" in corpo
    assert "01/03/2026" in corpo


def test_dashboard_mostra_variacao_patrimonial_sem_chamar_de_rentabilidade(
    logado, monkeypatch
):
    _foto("2026-01-31", "80.00", "20.00")
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("130.00")])]
        ),
    )

    corrido = " ".join(
        logado.get(
            "/patrimonio/", {"periodo": "tudo", "data": "2026-02-01"}
        ).content.decode().split()
    )

    assert "R$ 30,00" in corrido
    assert "30,00%" in corrido
    assert "desde 31/01/2026" in corrido
    assert "rentabilidade patrimonial" not in corrido.lower()


def test_dashboard_periodo_desconhecido_volta_para_um_ano(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    resposta = logado.get("/patrimonio/", {"periodo": "qualquer"})

    assert resposta.context["periodo"] == "1a"


def test_dashboard_consulta_o_mesmo_intervalo_v2_nas_duas_fontes(logado, monkeypatch):
    vistos = {}

    def consolidar(**parametros):
        vistos.update(parametros)
        return leitor.Consolidado(leituras=[])

    monkeypatch.setattr(views, "consolidar_v2", consolidar)

    logado.get("/patrimonio/", {"periodo": "3m", "data": "2026-09-18"})

    assert vistos == {
        "inicio": date(2026, 6, 18),
        "data": date(2026, 9, 18),
        "periodo": "all",
    }


def test_dashboard_exibe_analiticos_publicados_sem_recalcula_los(logado, monkeypatch):
    posicao = leitor.Linha(
        fonte=CRV.nome, papel="investimento", titular="Mariano", instituicao="Genial",
        descricao="WEGE3", moeda="BRL", valor=Decimal("150.00"),
        quantidade=Decimal("3"), exposicao_bruta=Decimal("150.00"),
        link="http://crv.teste/positions/1",
    )
    consolidado = leitor.Consolidado(
        leituras=[
            leitor.Leitura(
                fonte=CRV, estado=leitor.OK, linhas=[posicao],
                rendas=[{"moeda": "BRL", "total": Decimal("8"), "por_tipo": {"dividendo": Decimal("8")}}],
                ganhos_realizados=[{"moeda": "BRL", "resultado": Decimal("15"), "transacoes": 2, "instrumento": "acao"}],
                twr=[{"moeda": "BRL", "metodo": "TWR", "pontos": [{"data": date(2026, 9, 18), "retorno_acumulado": Decimal("0.10")}]}],
            )
        ]
    )
    com_consolidado(monkeypatch, consolidado)

    corrido = " ".join(
        logado.get("/patrimonio/", {"periodo": "1m", "data": "2026-09-18"})
        .content.decode()
        .split()
    )

    assert "Desempenho e renda" in corrido
    assert "10,00%" in corrido
    assert "Renda · BRL" in corrido
    assert "Resultado realizado · BRL" in corrido
    assert "Maiores posições" in corrido
    assert "WEGE3" in corrido


def test_contas_comecam_recolhidas_e_exibem_a_arvore_de_drilldown(logado, monkeypatch):
    posicao = leitor.Linha(
        fonte=CRV.nome,
        papel="investimento",
        titular="Mariano",
        instituicao="Genial",
        descricao="WEGE3",
        moeda="BRL",
        valor=Decimal("150.00"),
        id_de_origem="crv-pos-1",
        link="http://crv.teste/positions/1",
    )
    caixa = linha("100.00")
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[caixa]),
                leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[posicao]),
            ]
        ),
    )

    resposta = logado.get("/patrimonio/", {"visao": "investimentos", "periodo": "1m"})
    grupos = resposta.context["cartoes_de_contas"]
    corpo = resposta.content.decode()

    assert {grupo["nome"] for grupo in grupos} == {"Mariano", "Renda variável"}
    assert 'aria-expanded="false"' in corpo
    assert "Contas" in corpo
    assert "grupo=" in corpo
    assert "wf-account-child" not in corpo


def test_clicar_no_grupo_expande_contas_sem_nova_fonte(logado, monkeypatch):
    posicao = leitor.Linha(
        fonte=CRV.nome,
        papel="investimento",
        titular="Mariano",
        instituicao="Genial",
        descricao="WEGE3",
        moeda="BRL",
        valor=Decimal("150.00"),
        id_de_origem="crv-pos-1",
    )
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[posicao])]))

    inicial = logado.get("/patrimonio/")
    grupo = inicial.context["cartoes_de_contas"][0]
    expandida = logado.get("/patrimonio/", {"grupo": grupo["id"], "periodo": "3m", "visao": "investimentos"})
    corpo = expandida.content.decode()

    assert expandida.context["grupo_aberto"] == grupo["id"]
    assert 'aria-expanded="true"' in corpo
    assert "Genial" in corpo
    assert "wf-account-child" in corpo
    assert "wf-account-detail" not in corpo  # a posição só aparece no terceiro nível
    assert "periodo=3m" in corpo


def test_clicar_na_conta_abre_o_detalhe_da_mesma_arvore(logado, monkeypatch):
    posicao = leitor.Linha(
        fonte=CRV.nome,
        papel="investimento",
        titular="Mariano",
        instituicao="Genial",
        descricao="WEGE3",
        moeda="BRL",
        valor=Decimal("150.00"),
        id_de_origem="crv-pos-1",
        link="http://crv.teste/positions/1",
    )
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[posicao])]))

    inicial = logado.get("/patrimonio/")
    grupo = inicial.context["cartoes_de_contas"][0]
    conta = grupo["contas"][0]
    detalhe = logado.get("/patrimonio/", {"grupo": grupo["id"], "conta": conta["id"]})
    corpo = detalhe.content.decode()

    assert detalhe.context["conta_aberta"] == conta["id"]
    assert detalhe.context["detalhe_conta"]["nome"] == "Genial"
    assert 'aria-current="true"' in corpo
    assert "Detalhamento da conta" in corpo
    assert "WEGE3" in corpo
    assert "http://crv.teste/positions/1" in corpo


def test_id_de_drilldown_invalido_nao_expande_nada(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])]))

    resposta = logado.get("/patrimonio/", {"grupo": "grupo-inexistente", "conta": "conta-inexistente"})

    assert resposta.context["grupo_aberto"] == ""
    assert resposta.context["conta_aberta"] == ""
    assert 'aria-expanded="true"' not in resposta.content.decode()


def test_insights_reproduz_composicao_com_filtros_e_linhas_de_origem(logado, monkeypatch):
    posicao = leitor.Linha(
        fonte=CRV.nome,
        papel="investimento",
        titular="Mariano",
        instituicao="Genial",
        descricao="WEGE3",
        moeda="BRL",
        valor=Decimal("150.00"),
        classe="acao",
        mercado="B3",
        link="http://crv.teste/positions/1",
    )
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("50.00")]),
                leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[posicao]),
            ]
        ),
    )

    resposta = logado.get(
        "/patrimonio/",
        {"visao": "insights", "periodo": "1a", "data": "2026-09-18"},
    )
    assert resposta.status_code == 200
    corpo = " ".join(resposta.content.decode().split())
    assert "Portfolio Insights" in corpo
    assert "Composição e exposição" in corpo
    assert "Classe de ativo" in corpo
    assert "Setor" in corpo
    assert "Não classificado" in corpo
    assert "WEGE3" in corpo
    assert "http://crv.teste/positions/1" in corpo
    assert 'name="busca"' in corpo
    assert 'name="ordenar"' in corpo

    itens = resposta.context["insights"]["itens"]
    acao = next(item for item in itens if item["nome"] == "acao")
    filtrado = logado.get(
        "/patrimonio/",
        {
            "visao": "insights",
            "dimensao": "instituicao",
            "filtro_dimensao": "classe",
            "filtro": acao["id"],
        },
    )
    assert filtrado.status_code == 200
    assert "Filtro ativo:" in " ".join(filtrado.content.decode().split())
    assert filtrado.context["insights"]["filtro_nome"] == "acao"


def test_insights_nao_duplica_a_arvore_de_contas_ou_o_resumo_patrimonial(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("75.00")])]
        ),
    )

    corpo = " ".join(logado.get("/patrimonio/", {"visao": "insights"}).content.decode().split())

    assert "Portfolio Insights" in corpo
    assert 'aria-label="Contas e instituições"' not in corpo
    assert "Patrimônio total em BRL" not in corpo
    assert "Onde está o patrimônio" not in corpo


def test_navegacao_global_preserva_periodo_e_data_sem_carregar_drilldown(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[]))

    corpo = logado.get(
        "/patrimonio/",
        {"visao": "patrimonio", "periodo": "3m", "data": "2026-09-18", "grupo": "grupo-teste"},
    ).content.decode()

    assert "?visao=investimentos&amp;periodo=3m&amp;data=2026-09-18" in corpo
    assert "?visao=insights&amp;periodo=3m&amp;data=2026-09-18" in corpo
    assert "grupo-teste" not in corpo.split("tabs-principais", 1)[1].split("</nav>", 1)[0]


def test_gastos_nao_exibe_cartoes_de_patrimonio(logado, monkeypatch):
    com_consolidado(monkeypatch, leitor.Consolidado(leituras=[leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha()])]))

    corpo = " ".join(logado.get("/patrimonio/", {"visao": "gastos"}).content.decode().split())

    assert "Gastos e movimentações" in corpo
    assert "Onde está o patrimônio" not in corpo
    assert 'aria-label="Contas e instituições"' not in corpo


def test_insights_sinaliza_fontes_parciais_sem_apagar_as_linhas_disponiveis(logado, monkeypatch):
    com_consolidado(
        monkeypatch,
        leitor.Consolidado(
            leituras=[
                leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[linha("75.00")]),
                leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="timeout"),
            ]
        ),
    )

    resposta = logado.get("/patrimonio/", {"visao": "insights"})
    corpo = " ".join(resposta.content.decode().split())

    assert resposta.status_code == 200
    assert "A composição está parcial" in corpo
    assert "1 de 2 fontes responderam" in corpo
    assert "timeout" in corpo
    assert "C6" in corpo
