"""O leitor das fontes — e a regra que sustenta a confiança na tela.

O DEFEITO QUE ESTE ARQUIVO EXISTE PARA IMPEDIR

O Renda Variável reinicia, a requisição falha, e o patrimônio aparece trinta por
cento menor. O número continua plausível, ninguém desconfia, e a decisão que
alguém tomar olhando para ele será tomada sobre um total incompleto.

Por isso os testes aqui não medem só soma: medem que **é impossível obter um
total sem obter, junto, de quantas fontes ele é feito**. E medem que uma
resposta estranha derruba a fonte inteira para "falhou" em vez de virar um total
silenciosamente torto.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest

from consolidado import leitor
from consolidado.fontes import Fonte

CB = Fonte(apelido="CB", nome="Controle Bancário", papel="caixa", url="http://cb.teste", token="t-cb")
CRV = Fonte(
    apelido="CRV", nome="Renda Variável", papel="investimento", url="http://crv.teste", token="t-crv"
)


def envelope(**alteracoes) -> dict:
    corpo = {
        "contrato": "patrimonio/v1",
        "sistema": "controle-bancario",
        "papel": "caixa",
        "data_de_referencia": "2026-09-16",
        "titulares": [{"id": "mariano", "nome": "Mariano"}],
        "instituicoes": [{"id": "c6", "nome": "C6", "tipo": "Banco"}],
        "contas": [
            {
                "id": "controle-bancario:conta:7",
                "titular": "mariano",
                "instituicao": "c6",
                "nome": "ag: 0001 cta: 1257564-0",
                "moeda": "BRL",
                "saldo": "104.47",
                "saldo_inicial_em": "2025-12-31",
            }
        ],
        "totais_por_moeda": [{"moeda": "BRL", "saldo": "104.47", "contas": 1}],
        "posicoes": [],
        "proventos": [],
        "ativos_alternativos": [],
    }
    corpo.update(alteracoes)
    return corpo


# --- Interpretar o envelope ------------------------------------------------


def test_conta_publicada_vira_linha_legivel():
    leitura = leitor.interpretar(CB, envelope())

    assert leitura.respondeu
    assert leitura.data_de_referencia == date(2026, 9, 16)
    (linha,) = leitura.linhas
    assert linha.titular == "Mariano"
    assert linha.instituicao == "C6"
    assert linha.moeda == "BRL"
    assert linha.valor == Decimal("104.47")


def test_posicao_publicada_tambem_vira_linha():
    corpo = envelope(
        sistema="controle-renda-variavel",
        papel="investimento",
        contas=[],
        posicoes=[
            {
                "id": "controle-renda-variavel:posicao:1",
                "titular": "mariano",
                "instituicao": "genial",
                "instrumento": "WEGE3",
                "moeda": "BRL",
                "valor_a_mercado": "15630.00",
                "preco_em": "2026-09-16T17:31:02-03:00",
            }
        ],
        instituicoes=[{"id": "genial", "nome": "Genial", "tipo": "Corretora"}],
    )

    leitura = leitor.interpretar(CRV, corpo)

    (linha,) = leitura.linhas
    assert linha.descricao == "WEGE3"
    assert linha.valor == Decimal("15630.00")
    assert linha.papel == "investimento"
    assert linha.detalhe.startswith("2026-09-16")


def test_valor_como_numero_json_derruba_a_fonte_inteira():
    """O contrato manda texto justamente por isto.

    `float` não representa 0,10. Aceitar número aqui deixaria a imprecisão
    entrar em silêncio e reaparecer como centavos que ninguém lançou.
    """
    corpo = envelope()
    corpo["contas"][0]["saldo"] = 104.47

    leitura = leitor.interpretar(CB, corpo)

    assert not leitura.respondeu
    assert leitura.estado == leitor.FALHOU
    assert "texto" in leitura.motivo
    assert leitura.linhas == []


def test_contrato_desconhecido_e_recusado():
    leitura = leitor.interpretar(CB, envelope(contrato="patrimonio/v2"))

    assert leitura.estado == leitor.FALHOU
    assert "contrato desconhecido" in leitura.motivo


def test_linha_sem_moeda_derruba_a_fonte_inteira():
    """Meia leitura viraria meio total, e meio total é indistinguível de um
    total menor."""
    corpo = envelope()
    del corpo["contas"][0]["moeda"]

    leitura = leitor.interpretar(CB, corpo)

    assert leitura.estado == leitor.FALHOU
    assert leitura.linhas == []


# --- Consolidar ------------------------------------------------------------


def consolidado_com(*leituras, incompletas=()) -> leitor.Consolidado:
    return leitor.Consolidado(leituras=list(leituras), incompletas=list(incompletas))


def test_uma_fonte_fora_do_ar_nao_produz_um_total_menor_em_silencio():
    """O teste central deste arquivo."""
    boa = leitor.interpretar(CB, envelope())
    ruim = leitor.Leitura(fonte=CRV, estado=leitor.NAO_RESPONDEU, motivo="não respondeu")

    consolidado = consolidado_com(boa, ruim)

    assert consolidado.completo is False
    assert len(consolidado.fontes_que_responderam) == 1
    assert len(consolidado.leituras) == 2
    # O total existe -- e ele é acompanhado, no mesmo objeto, do fato de estar
    # incompleto. Não há caminho que devolva um sem o outro.
    assert consolidado.totais_por_moeda == [
        {"moeda": "BRL", "total": Decimal("104.47"), "linhas": 1}
    ]


def test_completo_so_quando_todas_responderam():
    boa = leitor.interpretar(CB, envelope())
    outra = leitor.interpretar(CRV, envelope(sistema="controle-renda-variavel"))

    assert consolidado_com(boa, outra).completo is True


def test_posicao_sem_cotacao_torna_o_consolidado_incompleto():
    """A fonte respondeu, mas deixou de fora algo que vale dinheiro: o total é
    menor que o patrimônio, e isso tem de ser dito como numa fonte fora do ar."""
    boa = leitor.interpretar(CB, envelope())
    furada = leitor.interpretar(
        CRV,
        envelope(
            sistema="controle-renda-variavel",
            omitidas={"simuladas": 10, "opcoes": 5, "sem_cotacao": 2},
        ),
    )

    assert furada.respondeu
    assert furada.lacunas == ["2 posições sem cotação ficaram de fora"]
    assert consolidado_com(boa, furada).completo is False


def test_simuladas_e_opcoes_nao_sao_lacuna():
    """As duas ficam de fora por decisão, e em toda data: não abrem buraco."""
    leitura = leitor.interpretar(
        CRV,
        envelope(
            sistema="controle-renda-variavel",
            omitidas={"simuladas": 10, "opcoes": 5, "sem_cotacao": 0},
        ),
    )

    assert leitura.lacunas == []


def test_omitidas_malformada_derruba_a_fonte():
    leitura = leitor.interpretar(CRV, envelope(omitidas={"sem_cotacao": "2"}))

    assert leitura.estado == leitor.FALHOU
    assert "sem_cotacao" in leitura.motivo


def test_fonte_configurada_pela_metade_torna_o_consolidado_incompleto():
    boa = leitor.interpretar(CB, envelope())

    consolidado = consolidado_com(boa, incompletas=["Renda Variável"])

    assert consolidado.completo is False


def test_sem_fonte_nenhuma_nao_e_completo():
    """Zero fontes somam zero, e zero não é "nada a dever": é "nada a mostrar"."""
    assert consolidado_com().completo is False


def test_moedas_nunca_sao_somadas_entre_si():
    em_reais = leitor.interpretar(CB, envelope())
    em_dolar = leitor.interpretar(
        CB,
        envelope(
            contas=[
                {
                    "id": "controle-bancario:conta:27",
                    "titular": "mariano",
                    "instituicao": "avenue",
                    "nome": "Conta em dólar",
                    "moeda": "USD",
                    "saldo": "7641.72",
                    "saldo_inicial_em": "2025-12-31",
                }
            ],
            instituicoes=[{"id": "avenue", "nome": "Avenue", "tipo": "Corretora"}],
        ),
    )

    totais = consolidado_com(em_reais, em_dolar).totais_por_moeda

    assert [t["moeda"] for t in totais] == ["BRL", "USD"]
    assert totais[0]["total"] == Decimal("104.47")
    assert totais[1]["total"] == Decimal("7641.72")


def test_agrupamento_por_instituicao_separa_moedas():
    em_reais = leitor.interpretar(CB, envelope())

    blocos = consolidado_com(em_reais).por_instituicao()

    assert len(blocos) == 1
    assert blocos[0]["instituicao"] == "C6"
    assert blocos[0]["moeda"] == "BRL"


# --- A rede ----------------------------------------------------------------


class RespostaFalsa:
    def __init__(self, corpo: bytes):
        self._corpo = corpo

    def read(self, _tamanho=None):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_busca_bem_sucedida(monkeypatch):
    def urlopen(pedido, timeout=None):
        assert pedido.get_header("Authorization") == "Bearer t-cb"
        assert pedido.full_url == "http://cb.teste/patrimonio/v1/resumo"
        return RespostaFalsa(json.dumps(envelope()).encode("utf-8"))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    leitura = leitor.buscar(CB)

    assert leitura.respondeu
    assert len(leitura.linhas) == 1


def test_data_pedida_viaja_na_consulta(monkeypatch):
    vistos = []

    def urlopen(pedido, timeout=None):
        vistos.append(pedido.full_url)
        corpo = envelope(data_de_referencia="2026-06-30")
        return RespostaFalsa(json.dumps(corpo).encode("utf-8"))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    leitura = leitor.buscar(CB, date(2026, 6, 30))

    assert vistos == ["http://cb.teste/patrimonio/v1/resumo?data=2026-06-30"]
    assert leitura.respondeu


def test_fonte_que_responde_outra_data_e_recusada(monkeypatch):
    """Uma fonte que ignora `?data=` devolve o patrimônio de hoje para uma
    pergunta sobre junho, e o número passa por histórico."""

    def urlopen(pedido, timeout=None):
        return RespostaFalsa(json.dumps(envelope()).encode("utf-8"))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    leitura = leitor.buscar(CB, date(2026, 6, 30))

    assert leitura.estado == leitor.FALHOU
    assert "16/09/2026" in leitura.motivo
    assert leitura.linhas == []


def test_sem_data_pedida_a_data_da_fonte_vale(monkeypatch):
    def urlopen(pedido, timeout=None):
        return RespostaFalsa(json.dumps(envelope()).encode("utf-8"))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    assert leitor.buscar(CB).respondeu


@pytest.mark.parametrize(
    ("codigo", "trecho"),
    [(401, "token recusado"), (503, "não está configurada"), (500, "HTTP 500")],
)
def test_erro_http_vira_motivo_legivel(monkeypatch, codigo, trecho):
    def urlopen(pedido, timeout=None):
        raise urllib.error.HTTPError(pedido.full_url, codigo, "erro", {}, BytesIO(b""))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    leitura = leitor.buscar(CB)

    assert leitura.estado == leitor.FALHOU
    assert trecho in leitura.motivo


def test_fonte_inacessivel_nao_derruba_a_leitura(monkeypatch):
    """A tela precisa abrir mesmo com uma fonte fora do ar."""

    def urlopen(pedido, timeout=None):
        raise urllib.error.URLError("conexão recusada")

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    leitura = leitor.buscar(CB)

    assert leitura.estado == leitor.NAO_RESPONDEU
    assert leitura.linhas == []


def test_resposta_que_nao_e_json_e_recusada(monkeypatch):
    def urlopen(pedido, timeout=None):
        return RespostaFalsa(b"<html>login</html>")

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    assert leitor.buscar(CB).motivo == "resposta não é JSON"


def test_resposta_grande_demais_e_recusada(monkeypatch):
    def urlopen(pedido, timeout=None):
        return RespostaFalsa(b"x" * (leitor.TAMANHO_MAXIMO_BYTES + 1))

    monkeypatch.setattr(leitor.urllib.request, "urlopen", urlopen)

    assert leitor.buscar(CB).motivo == "resposta grande demais"


# --- O link de cada linha ----------------------------------------------------


def _conta_com_endereco(endereco) -> dict:
    corpo = envelope()
    corpo["contas"][0]["endereco"] = endereco
    return corpo


def test_a_linha_leva_a_tela_de_origem():
    leitura = leitor.interpretar(CB, _conta_com_endereco("/transactions/?account_id=7&mode=realizado"))

    (linha,) = leitura.linhas
    assert linha.link == "http://cb.teste/transactions/?account_id=7&mode=realizado"


def test_sem_endereco_publicado_a_linha_nao_tem_link():
    """Posição encerrada vai com `endereco` nulo; publicador antigo nem manda a chave."""
    (sem_chave,) = leitor.interpretar(CB, envelope()).linhas
    (nulo,) = leitor.interpretar(CB, _conta_com_endereco(None)).linhas

    assert sem_chave.link == ""
    assert nulo.link == ""


@pytest.mark.parametrize(
    "endereco",
    [
        "https://outro-lugar.teste/x",
        "//outro-lugar.teste/x",
        "/\\outro-lugar.teste/x",
        "javascript:alert(1)",
        "transactions/",
        "/com espaco",
        7,
        "/" + "a" * 3000,
    ],
)
def test_endereco_que_sairia_da_fonte_e_descartado_sem_derrubar_a_fonte(endereco):
    """O que vem pela rede é dado: um link para fora dos dois sistemas não entra."""
    leitura = leitor.interpretar(CB, _conta_com_endereco(endereco))

    assert leitura.respondeu
    (linha,) = leitura.linhas
    assert linha.link == ""
    assert linha.valor == Decimal("104.47")


def test_a_posicao_tambem_leva_a_tela_de_origem():
    corpo = envelope(
        sistema="controle-renda-variavel",
        papel="investimento",
        contas=[],
        posicoes=[
            {
                "id": "controle-renda-variavel:posicao:1",
                "titular": "mariano",
                "instituicao": "genial",
                "instrumento": "WEGE3",
                "moeda": "BRL",
                "valor_a_mercado": "15630.00",
                "endereco": "/?broker=Genial&expanded=1",
            }
        ],
        instituicoes=[{"id": "genial", "nome": "Genial", "tipo": "Corretora"}],
    )

    (linha,) = leitor.interpretar(CRV, corpo).linhas

    assert linha.link == "http://crv.teste/?broker=Genial&expanded=1"
