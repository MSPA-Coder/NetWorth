"""A coleta da série de câmbio.

Duas coisas são protegidas aqui, e as duas são sobre **não inventar**: dia sem
negócio não vira taxa interpolada, e taxa já gravada não é reescrita por uma
coleta posterior -- aquele número já pode ter sustentado um patrimônio que
alguém olhou.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from consolidado import yahoo
from consolidado.models import TaxaDeCambio

pytestmark = pytest.mark.django_db


def instante(dia: str) -> int:
    """O carimbo que o Yahoo usa para um dia.

    Calculado a partir da data, e não escrito à mão: número mágico de época é
    ilegível, e um dígito trocado desloca o dia inteiro sem que o teste pareça
    errado -- foi o que aconteceu na primeira versão deste arquivo.
    """
    return int(datetime.combine(date.fromisoformat(dia), time.min, tzinfo=UTC).timestamp())


def resposta_crua(
    instantes: list[int], fechamentos: list[float | None], fuso: str | None = "Europe/London"
) -> bytes:
    """O formato que o Yahoo devolve, com o `meta` que diz o fuso da bolsa."""
    serie = {
        "timestamp": instantes,
        "indicators": {"quote": [{"close": fechamentos}]},
    }
    if fuso is not None:
        serie["meta"] = {"exchangeTimezoneName": fuso}
    return json.dumps({"chart": {"result": [serie]}}).encode("utf-8")


def resposta_do_yahoo(pares: list[tuple[str, float | None]]) -> bytes:
    return resposta_crua([instante(dia) for dia, _ in pares], [valor for _, valor in pares])


class RespostaFalsa:
    def __init__(self, corpo: bytes):
        self._corpo = corpo

    def read(self, _tamanho=None):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def responder(monkeypatch, corpo: bytes):
    monkeypatch.setattr(
        yahoo.urllib.request, "urlopen", lambda *_args, **_kwargs: RespostaFalsa(corpo)
    )



DIA_16 = "2026-09-16"
DIA_17 = "2026-09-17"


def test_o_simbolo_e_o_par_de_moedas():
    assert yahoo.simbolo("usd", "brl") == "USDBRL=X"


def test_serie_vira_fechamentos(monkeypatch):
    responder(monkeypatch, resposta_do_yahoo([(DIA_16, 5.2), (DIA_17, 5.3)]))

    serie = yahoo.buscar_serie("USD", "BRL", date(2026, 9, 16), date(2026, 9, 17))

    assert [(f.data, f.taxa) for f in serie] == [
        (date(2026, 9, 16), Decimal("5.2")),
        (date(2026, 9, 17), Decimal("5.3")),
    ]


def test_dia_sem_negocio_e_pulado_e_nao_interpolado(monkeypatch):
    """Inventar a taxa de um dia sem mercado é inventar mercado."""
    responder(monkeypatch, resposta_do_yahoo([(DIA_16, None), (DIA_17, 5.3)]))

    serie = yahoo.buscar_serie("USD", "BRL", date(2026, 9, 16), date(2026, 9, 17))

    assert [f.data for f in serie] == [date(2026, 9, 17)]


def test_no_verao_de_londres_o_carimbo_das_23h_utc_e_do_dia_seguinte(monkeypatch):
    """Carimbos reais de setembro/2026: a meia-noite de Londres é 23:00 UTC.

    Convertidos em UTC, 5,1428 virava taxa de 15/09, quando é o fechamento de
    16/09 -- a série inteira andava um dia para trás de abril a outubro.
    """
    responder(monkeypatch, resposta_crua([1789426800, 1789513200], [5.138, 5.1429]))

    serie = yahoo.buscar_serie("USD", "BRL", date(2026, 9, 15), date(2026, 9, 16))

    assert [(f.data, f.taxa) for f in serie] == [
        (date(2026, 9, 15), Decimal("5.138")),
        (date(2026, 9, 16), Decimal("5.1429")),
    ]


def test_no_inverno_de_londres_o_carimbo_e_a_meia_noite_utc(monkeypatch):
    responder(monkeypatch, resposta_crua([instante("2026-01-15")], [5.40]))

    serie = yahoo.buscar_serie("USD", "BRL", date(2026, 1, 15), date(2026, 1, 15))

    assert [f.data for f in serie] == [date(2026, 1, 15)]


def test_serie_sem_fuso_e_recusada(monkeypatch):
    """Adivinhar UTC foi exatamente o erro que deslocava a série."""
    responder(monkeypatch, resposta_crua([instante(DIA_16)], [5.2], fuso=None))

    with pytest.raises(yahoo.ColetaDeCambioError):
        yahoo.buscar_serie("USD", "BRL", date(2026, 9, 16), date(2026, 9, 16))


def test_resposta_estranha_vira_erro_de_coleta(monkeypatch):
    responder(monkeypatch, b'{"chart": {"result": []}}')

    with pytest.raises(yahoo.ColetaDeCambioError):
        yahoo.buscar_serie("USD", "BRL", date(2026, 9, 16), date(2026, 9, 17))


def test_periodo_invertido_nao_chama_a_rede(monkeypatch):
    def nao_deveria(*_args, **_kwargs):
        raise AssertionError("não era para chamar a rede")

    monkeypatch.setattr(yahoo.urllib.request, "urlopen", nao_deveria)

    assert yahoo.buscar_serie("USD", "BRL", date(2026, 9, 17), date(2026, 9, 16)) == []


# --- O comando -------------------------------------------------------------


@pytest.fixture(autouse=True)
def hoje_e_dia_18(monkeypatch):
    """O comando depende do dia em que roda, e o teste não pode depender.

    Sem isto, os testes abaixo davam um resultado no dia 17/09/2026 e outro em
    qualquer dia depois dele. Com o dia 18, 16 e 17 já estão fechados.
    """
    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.timezone.localdate",
        lambda *_args, **_kwargs: date(2026, 9, 18),
    )


def rodar(**opcoes) -> str:
    saida = StringIO()
    call_command("atualizar_cambio", stdout=saida, stderr=saida, **opcoes)
    return saida.getvalue()


def test_comando_grava_a_serie(monkeypatch):
    responder(monkeypatch, resposta_do_yahoo([(DIA_16, 5.2), (DIA_17, 5.3)]))

    relatorio = rodar(moeda=["USD"], desde="2026-09-16")

    assert TaxaDeCambio.objects.count() == 2
    assert "2 nova" in relatorio


def test_comando_nao_reescreve_taxa_ja_gravada(monkeypatch):
    """Aquele número já pode ter sustentado um patrimônio que alguém olhou."""
    TaxaDeCambio.objects.create(
        moeda="USD", data=date(2026, 9, 16), taxa=Decimal("5.20"), fonte="yahoo"
    )
    responder(monkeypatch, resposta_do_yahoo([(DIA_16, 9.99), (DIA_17, 5.3)]))

    relatorio = rodar(moeda=["USD"], desde="2026-09-16")

    guardada = TaxaDeCambio.objects.get(moeda="USD", data=date(2026, 9, 16))
    assert guardada.taxa == Decimal("5.20")
    assert "1 nova" in relatorio
    assert "1 já existia" in relatorio


def test_o_dia_corrente_nunca_vira_taxa(monkeypatch):
    """O Yahoo devolve o dia de hoje com o preço do momento, não o fechamento.

    Gravado, aquele valor parcial nunca mais seria corrigido, porque uma taxa
    gravada não é reescrita. O dia só entra depois de fechado.
    """
    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.timezone.localdate",
        lambda *_args, **_kwargs: date(2026, 9, 17),
    )
    pedidos = []

    def serie_com_o_dia_de_hoje(moeda, base, inicio, fim):
        pedidos.append((inicio, fim))
        return [
            yahoo.Fechamento(date(2026, 9, 16), Decimal("5.20")),
            yahoo.Fechamento(date(2026, 9, 17), Decimal("5.31")),
        ]

    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.buscar_serie",
        serie_com_o_dia_de_hoje,
    )

    rodar(moeda=["USD"], desde="2026-09-16")

    assert pedidos == [(date(2026, 9, 16), date(2026, 9, 16))]
    assert list(TaxaDeCambio.objects.values_list("data", flat=True)) == [date(2026, 9, 16)]


def test_em_dia_ate_ontem_nao_chama_a_rede(monkeypatch):
    TaxaDeCambio.objects.create(
        moeda="USD", data=date(2026, 9, 17), taxa=Decimal("5.20"), fonte="yahoo"
    )

    def nao_deveria(*_args, **_kwargs):
        raise AssertionError("não era para chamar a rede")

    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.buscar_serie", nao_deveria
    )

    assert "já está em dia" in rodar(moeda=["USD"])


def test_rodar_duas_vezes_nao_muda_nada(monkeypatch):
    responder(monkeypatch, resposta_do_yahoo([(DIA_16, 5.2), (DIA_17, 5.3)]))
    rodar(moeda=["USD"], desde="2026-09-16")

    antes = sorted(TaxaDeCambio.objects.values_list("moeda", "data", "taxa", "fonte"))
    rodar(moeda=["USD"], desde="2026-09-16")

    assert sorted(TaxaDeCambio.objects.values_list("moeda", "data", "taxa", "fonte")) == antes


def test_falha_de_coleta_nao_derruba_o_comando(monkeypatch):
    def estourar(*_args, **_kwargs):
        raise yahoo.ColetaDeCambioError("O Yahoo não respondeu.")

    # O alvo é o nome IMPORTADO pelo comando, não o do módulo de origem: o
    # comando fez `from consolidado.yahoo import buscar_serie`, então trocar o
    # atributo em `yahoo` não alcança a referência que ele guarda. A primeira
    # versão deste teste trocava o lugar errado -- e, sem a trava de rede da
    # `conftest`, passou consultando o Yahoo de verdade.
    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.buscar_serie", estourar
    )

    relatorio = rodar(moeda=["USD"], desde="2026-09-16")

    assert "não respondeu" in relatorio
    assert TaxaDeCambio.objects.count() == 0


def test_moeda_base_e_ignorada(monkeypatch):
    relatorio = rodar(moeda=["BRL"])

    assert "só há patrimônio em moeda base" in relatorio


def test_data_invalida_para_o_comando():
    with pytest.raises(CommandError, match="AAAA-MM-DD"):
        rodar(moeda=["USD"], desde="16/09/2026")


def test_sem_moeda_e_sem_fonte_no_ar_o_comando_recusa(monkeypatch):
    """Ele não chuta uma lista de moedas: pergunta às fontes, ou exige --moeda."""
    from consolidado import leitor

    monkeypatch.setattr(
        "consolidado.management.commands.atualizar_cambio.consolidar",
        lambda *_a, **_k: leitor.Consolidado(leituras=[]),
    )

    with pytest.raises(CommandError, match="--moeda"):
        rodar()
