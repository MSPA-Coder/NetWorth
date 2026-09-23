"""O patrimônio projetado: leitura do contrato do CB, soma e tela.

As regras que mudam o número são as mesmas que a tela anuncia em "Premissas":
aporte não é perda, moedas não se misturam sem taxa, e sem todas as fontes não
há patrimônio projetado -- só o caixa.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from consolidado import leitor, projecao, projecao_views, wealthfolio_views
from consolidado.fontes import Fonte
from consolidado.models import TaxaDeCambio

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t", endereco_publico="https://cb.exemplo")
CRV = Fonte(apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t")
HOJE = date(2026, 3, 15)


def envelope(**mudancas):
    """Um envelope como o CB publica: 950 hoje, aporte de 300 em 10/04."""
    corpo = {
        "contrato": "patrimonio/v3",
        "recurso": "projecao",
        "sistema": "controle-bancario",
        "data_base": "2026-03-15",
        "fim": "2026-06-30",
        "vencidos_incluidos": True,
        "horizonte": {"meses_configurados": 6, "ultima_recorrencia": "2026-04-05"},
        "saldos_iniciais": [{"moeda": "BRL", "saldo": "950.00"}],
        "vencidos": [{"moeda": "BRL", "entradas": "0.00", "saidas": "100.00", "quantidade": 1}],
        "contas": [
            {
                "id": "controle-bancario:conta:abc", "nome": "Conta corrente", "moeda": "BRL",
                "titular": "mariano", "instituicao": "c6", "deep_link": "/banking/accounts/1/",
                "saldo_inicial": "950.00", "saldo_final": "850.00",
                "menor_saldo": {"valor": "-50.00", "data": "2026-03-20"},
            }
        ],
        "menor_saldo": [{"moeda": "BRL", "data": "2026-03-20", "valor": "450.00"}],
        "serie": [
            {"moeda": "BRL", "data": "2026-03-15", "saldo": "850.00", "investido_acumulado": "0.00"},
            {"moeda": "BRL", "data": "2026-03-20", "saldo": "450.00", "investido_acumulado": "0.00"},
            {"moeda": "BRL", "data": "2026-04-05", "saldo": "1350.00", "investido_acumulado": "0.00"},
            {"moeda": "BRL", "data": "2026-04-10", "saldo": "1050.00", "investido_acumulado": "300.00"},
        ],
        "meses": [
            {
                "mes": mes, "moeda": "BRL", "entradas": entradas, "saidas": saidas,
                "transferencias_entrada": "0.00", "transferencias_saida": "0.00",
                "investimentos_entrada": "0.00", "investimentos_saida": aporte,
                "saldo_final": final, "deep_link": f"/reports/projections/?start_month={mes}",
            }
            for mes, entradas, saidas, aporte, final in (
                ("2026-03", "0.00", "400.00", "0.00", "450.00"),
                ("2026-04", "900.00", "0.00", "300.00", "1050.00"),
                ("2026-05", "0.00", "0.00", "0.00", "1050.00"),
                ("2026-06", "0.00", "0.00", "0.00", "1050.00"),
            )
        ],
    }
    corpo.update(mudancas)
    return corpo


def _patrimonio_por_dia(projetado):
    return {ponto.data: ponto.patrimonio["BRL"] for ponto in projetado.pontos}


# --- Leitura do contrato ------------------------------------------------------


def test_interpreta_o_envelope_e_monta_links_pela_fonte():
    lida = projecao.interpretar(CB, envelope())

    assert lida.data_base == HOJE
    assert lida.saldos_iniciais == {"BRL": Decimal("950.00")}
    assert lida.vencidos[0].quantidade == 1
    assert lida.meses[0].link == "https://cb.exemplo/reports/projections/?start_month=2026-03"
    assert lida.contas[0].link == "https://cb.exemplo/banking/accounts/1/"
    assert lida.fim_da_projecao == date(2026, 4, 5)


def test_valor_como_numero_json_recusa_a_projecao_inteira():
    corpo = envelope()
    corpo["serie"][1]["saldo"] = 450.0

    with pytest.raises(ValueError, match="texto"):
        projecao.interpretar(CB, corpo)


def test_contrato_desconhecido_e_recusado():
    with pytest.raises(ValueError, match="contrato"):
        projecao.interpretar(CB, envelope(recurso="atividades"))


def test_link_para_fora_da_fonte_e_descartado_sem_derrubar_a_leitura():
    corpo = envelope()
    corpo["contas"][0]["deep_link"] = "https://outro-lugar.exemplo/"

    assert projecao.interpretar(CB, corpo).contas[0].link == ""


def test_fonte_sem_a_rota_vira_motivo_legivel(monkeypatch):
    import urllib.error

    def responde_404(*_args, **_kwargs):
        raise urllib.error.HTTPError("http://cb.teste", 404, "Not Found", {}, None)

    monkeypatch.setattr(projecao.urllib.request, "urlopen", responde_404)

    leitura = projecao.buscar(CB, HOJE)
    assert leitura.projecao is None
    assert "ainda não publica" in leitura.motivo


# --- A soma ------------------------------------------------------------------


def test_aporte_programado_nao_derruba_o_patrimonio():
    projetado = projecao.montar(projecao.interpretar(CB, envelope()), {"BRL": Decimal("5000.00")}, date(2026, 6, 30))

    por_dia = _patrimonio_por_dia(projetado)
    # Em 10/04 o caixa cai 300 e o investido sobe 300: o patrimônio fica igual.
    assert por_dia[date(2026, 4, 5)] == por_dia[date(2026, 4, 10)] == Decimal("6350.00")
    assert por_dia[HOJE] == Decimal("5850.00")


def test_meses_trazem_caixa_e_patrimonio_no_fim():
    projetado = projecao.montar(projecao.interpretar(CB, envelope()), {"BRL": Decimal("5000.00")}, date(2026, 6, 30))

    abril = next(linha for linha in projetado.meses if linha.mes == date(2026, 4, 1))
    assert abril.para_investir == Decimal("300.00")
    assert abril.caixa_no_fim == Decimal("1050.00")
    assert abril.patrimonio_no_fim == Decimal("6350.00")


def test_horizonte_curto_corta_a_curva_e_os_meses():
    projetado = projecao.montar(projecao.interpretar(CB, envelope()), {"BRL": Decimal("0.00")}, date(2026, 3, 31))

    assert projetado.pontos[-1].data == date(2026, 3, 31)
    assert [linha.mes for linha in projetado.meses] == [date(2026, 3, 1)]
    assert projetado.pontos[-1].caixa["BRL"] == Decimal("450.00")


def test_sem_investimentos_confiaveis_nao_ha_patrimonio_so_caixa():
    projetado = projecao.montar(projecao.interpretar(CB, envelope()), None, date(2026, 6, 30))

    assert not projetado.tem_patrimonio
    assert all(ponto.patrimonio is None for ponto in projetado.pontos)
    assert projetado.meses[0].patrimonio_no_fim is None


def test_consolidado_parcial_nao_entrega_investimentos():
    parcial = leitor.Consolidado(leituras=[leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="fora do ar")])

    assert projecao.investimentos_por_moeda(parcial) is None


@pytest.mark.django_db
def test_moeda_sem_taxa_nao_vira_total_em_reais():
    corpo = envelope()
    corpo["saldos_iniciais"].append({"moeda": "USD", "saldo": "100.00"})
    projetado = projecao.montar(projecao.interpretar(CB, corpo), {"BRL": Decimal("0.00")}, date(2026, 6, 30))

    assert projetado.em_moeda_base(projetado.pontos[-1].caixa) is None


@pytest.mark.django_db
def test_moeda_estrangeira_usa_a_taxa_de_hoje_nas_datas_futuras():
    TaxaDeCambio.objects.create(moeda="USD", data=HOJE, taxa=Decimal("5.00"), fonte="yahoo")
    corpo = envelope()
    corpo["saldos_iniciais"].append({"moeda": "USD", "saldo": "100.00"})
    projetado = projecao.montar(projecao.interpretar(CB, corpo), {"BRL": Decimal("0.00")}, date(2026, 6, 30))

    # 1050 em reais + 100 dólares a 5,00.
    assert projetado.em_moeda_base(projetado.pontos[-1].caixa) == Decimal("1550.00")


# --- A tela ------------------------------------------------------------------


def _consolidado():
    return leitor.Consolidado(
        leituras=[
            leitor.Leitura(fonte=CB, estado=leitor.OK, linhas=[
                leitor.Linha(fonte=CB.nome, papel="caixa", titular="Pessoa", instituicao="Banco",
                             descricao="Conta", moeda="BRL", valor=Decimal("950.00")),
            ]),
            leitor.Leitura(fonte=CRV, estado=leitor.OK, linhas=[
                leitor.Linha(fonte=CRV.nome, papel="investimento", titular="Pessoa", instituicao="Corretora",
                             descricao="ETF", moeda="BRL", valor=Decimal("5000.00")),
            ]),
        ]
    )


@pytest.fixture
def cliente(monkeypatch):
    usuario = get_user_model().objects.create_user("projecao", password="senha-longa-o-suficiente")
    client = Client()
    client.force_login(usuario)
    monkeypatch.setattr(wealthfolio_views.leitor, "consolidar_v2", lambda **_kwargs: _consolidado())
    monkeypatch.setattr(projecao_views, "fontes_configuradas", lambda: [CB, CRV])
    monkeypatch.setattr(wealthfolio_views, "fontes_configuradas", lambda: [CB, CRV])
    return client


def _com_projecao(monkeypatch, corpo=None):
    lida = projecao.interpretar(CB, corpo or envelope())
    # A tela pede a projeção a partir de hoje de verdade; o envelope fixo é
    # reancorado para a data de hoje, preservando os intervalos entre os pontos.
    deslocamento = date.today() - lida.data_base

    def mover(dia):
        return dia + deslocamento

    corpo_movido = copy.deepcopy(corpo or envelope())
    corpo_movido["data_base"] = mover(lida.data_base).isoformat()
    corpo_movido["fim"] = mover(lida.fim).isoformat()
    corpo_movido["horizonte"]["ultima_recorrencia"] = mover(lida.ultima_recorrencia).isoformat()
    for ponto in corpo_movido["serie"]:
        ponto["data"] = mover(date.fromisoformat(ponto["data"])).isoformat()
    for conta in corpo_movido["contas"]:
        conta["menor_saldo"]["data"] = mover(date.fromisoformat(conta["menor_saldo"]["data"])).isoformat()
    meses = sorted({date.fromisoformat(p["data"]).replace(day=1) for p in corpo_movido["serie"]} | {mover(lida.fim).replace(day=1)})
    modelo = corpo_movido["meses"][-1]
    corpo_movido["meses"] = [{**modelo, "mes": mes.strftime("%Y-%m")} for mes in meses]
    movida = projecao.interpretar(CB, corpo_movido)
    monkeypatch.setattr(projecao, "buscar", lambda _fonte, _fim: projecao.LeituraDaProjecao(CB.nome, projecao=movida))


@pytest.mark.django_db
def test_tela_mostra_metricas_premissas_e_aviso_de_conta_negativa(cliente, monkeypatch):
    _com_projecao(monkeypatch)

    resposta = cliente.get("/projecao/", {"horizonte": "tudo"})

    assert resposta.status_code == 200
    pagina = resposta.context["wf_page"]
    assert pagina["state"] == "ready"
    assert pagina["tem_patrimonio"] is True
    assert pagina["metricas"]["hoje"] == "R$ 5.950,00"
    assert pagina["contas_negativas"][0]["nome"] == "Conta corrente"
    assert pagina["contas_negativas"][0]["link"] == "https://cb.exemplo/banking/accounts/1/"
    assert any("sem estimar rendimento" in linha for linha in pagina["premissas"])
    assert any("vencido" in linha for linha in pagina["premissas"])
    assert any("Proventos anunciados" in linha for linha in pagina["premissas"])
    # A fonte de atividades não responde no arnês: a lista mostra o motivo.
    assert pagina["proximos"]["disponivel"] is False
    conteudo = resposta.content.decode()
    assert "Patrimônio projetado" in conteudo
    # Em pt-BR o Django escreveria "120,3"; no SVG isso é uma LISTA de
    # coordenadas e espalha cada letra dos rótulos pelo gráfico.
    inicio = conteudo.index('<figure class="wf2p-proj-chart"')
    svg = conteudo[inicio : conteudo.index("</figure>", inicio)]
    assert "<path" in svg
    assert re.search(r'(?:x|y|x1|x2|y1|y2)="-?\d+,\d', svg) is None
    assert 'aria-current="page" data-wf2-nav="projection"' in conteudo.replace("\n       ", " ")


@pytest.mark.django_db
def test_tela_sem_projecao_mostra_o_motivo(cliente, monkeypatch):
    monkeypatch.setattr(
        projecao, "buscar",
        lambda _fonte, _fim: projecao.LeituraDaProjecao(CB.nome, motivo="a fonte ainda não publica a projeção"),
    )

    resposta = cliente.get("/projecao/")

    assert resposta.status_code == 200
    assert resposta.context["wf_page"]["state"] == "error"
    assert "ainda não publica" in resposta.context["wf_page"]["error_message"]


@pytest.mark.django_db
def test_tela_so_responde_a_get(cliente):
    assert cliente.post("/projecao/").status_code == 405


@pytest.mark.django_db
def test_horizonte_desconhecido_volta_ao_padrao(cliente, monkeypatch):
    _com_projecao(monkeypatch)

    resposta = cliente.get("/projecao/", {"horizonte": "10-anos"})

    ativo = [item for item in resposta.context["wf_page"]["horizontes"] if item["ativo"]]
    assert [item["rotulo"] for item in ativo] == ["6 meses"]


@pytest.mark.django_db
def test_painel_mostra_o_cartao_da_projecao(cliente, monkeypatch):
    _com_projecao(monkeypatch)

    resposta = cliente.get("/dashboard/", {"tab": "net-worth"})

    assert resposta.status_code == 200
    cartao = resposta.context["wf_projection_card"]
    assert cartao["url"] == "/projecao/"
    assert cartao["valor"].startswith("R$")


@pytest.mark.django_db
def test_painel_sem_projecao_nao_mostra_cartao(cliente, monkeypatch):
    monkeypatch.setattr(
        projecao, "buscar",
        lambda _fonte, _fim: projecao.LeituraDaProjecao(CB.nome, motivo="a fonte não respondeu"),
    )

    resposta = cliente.get("/dashboard/", {"tab": "net-worth"})

    assert resposta.status_code == 200
    assert resposta.context["wf_projection_card"] is None
