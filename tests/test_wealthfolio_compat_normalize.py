from decimal import Decimal

import pytest

from consolidado.wealthfolio_compat import CompatibilityError, normalize_payload


def cb_payload(**changes):
    payload = {
        "contrato": "patrimonio/v1",
        "sistema": "controle-bancario",
        "papel": "caixa",
        "data_de_referencia": "2026-09-19",
        "gerado_em": "2026-09-19T12:00:00-03:00",
        "titulares": [{"id": "mariano", "nome": "Mariano"}],
        "instituicoes": [{"id": "c6", "nome": "C6", "tipo": "Banco"}],
        "contas": [{
            "id": "controle-bancario:conta:7",
            "titular": "mariano",
            "instituicao": "c6",
            "nome": "Conta principal",
            "moeda": "BRL",
            "saldo": "100.10",
            "endereco": "/contas/7",
        }],
        "totais_por_moeda": [{"moeda": "BRL", "total": "100.10", "linhas": 1}],
        "posicoes": [],
        "proventos": [],
        "ativos_alternativos": [],
    }
    payload.update(changes)
    return payload


def crv_v2_payload(**changes):
    payload = cb_payload(
        contrato="patrimonio/v2",
        sistema="controle-renda-variavel",
        papel="investimento",
        contas=[],
        instituicoes=[{"id": "genial", "nome": "Genial", "tipo": "Corretora"}],
        totais_por_moeda=[{"moeda": "USD", "total": "200.00", "linhas": 1}],
        posicoes=[{
            "id": "controle-renda-variavel:posicao:4",
            "titular": "mariano",
            "instituicao": "genial",
            "instrumento": "ETF",
            "moeda": "USD",
            "valor_a_mercado": "200.00",
            "quantidade": "2",
            "preco": "100.00",
            "custo_total": "180.00",
            "resultado_nao_realizado": "20.00",
            "situacao_do_preco": "fechamento",
            "endereco": "/positions/4",
        }],
    )
    payload.update({
        "fluxos": [{"data": "2026-09-01", "moeda": "USD", "natureza": "gerencial", "entradas": "20.00", "saidas": "5.00", "liquido": "15.00", "linhas": 2}],
        "renda_por_moeda": [{"moeda": "USD", "total": "3.00", "por_tipo": {"dividendo": "3.00"}}],
        "ganhos_realizados_por_moeda": [{"moeda": "USD", "instrumento": "acao", "resultado": "4.00", "transacoes": 1}],
        "desempenho_por_moeda": [{"moeda": "USD", "metodo": "TWR", "pontos": [{"data": "2026-09-01", "retorno_acumulado": "0.05"}]}],
        "qualidade": {"status": "ok"},
    })
    payload.update(changes)
    return payload


def test_normaliza_cb_v1_com_conta_e_capacidades():
    snapshot = normalize_payload(cb_payload(), expected_source="cb")

    assert snapshot.source == "controle-bancario"
    assert snapshot.as_of.isoformat() == "2026-09-19"
    assert snapshot.accounts[0].balance == snapshot.totals[0]
    assert snapshot.totals[0].amount == Decimal("100.10")
    assert snapshot.capabilities.accounts is True
    assert snapshot.capabilities.positions is False
    assert snapshot.capabilities.individual_activities is False
    assert snapshot.complete is True


def test_normaliza_proventos_legados_v1_em_renda():
    snapshot = normalize_payload(cb_payload(
        proventos=[{"moeda": "BRL", "tipo": "dividendo", "valor": "2.50"}],
    ))

    assert snapshot.income[0].value.amount == Decimal("2.50")
    assert snapshot.capabilities.income is True


def test_normaliza_crv_v2_analitico_sem_copiar_payload():
    payload = crv_v2_payload()
    snapshot = normalize_payload(payload, expected_source="crv")
    payload["posicoes"][0]["instrumento"] = "MUTADO"

    assert snapshot.positions[0].instrument == "ETF"
    assert snapshot.positions[0].value.amount == Decimal("200.00")
    assert snapshot.flows[0].value.amount == Decimal("15.00")
    assert snapshot.income[0].by_type[0][1].amount == Decimal("3.00")
    assert snapshot.realized_gains[0].value.amount == Decimal("4.00")
    assert snapshot.performance[0].points[0][1] == Decimal("0.05")
    assert snapshot.capabilities.performance is True
    assert snapshot.capabilities.quality is True


def test_normaliza_parcial_por_posicao_sem_cotacao():
    snapshot = normalize_payload(crv_v2_payload(omitidas={"sem_cotacao": 2}))

    assert snapshot.complete is False
    assert snapshot.coverage.status == "partial"
    assert snapshot.coverage.omissions


def test_payload_vazio_ou_sem_campos_v2_e_recusado():
    with pytest.raises(CompatibilityError):
        normalize_payload({})
    with pytest.raises(CompatibilityError):
        normalize_payload({"contrato": "patrimonio/v2", "sistema": "controle-renda-variavel", "data_de_referencia": "2026-09-19", "posicoes": []})


def test_payload_invalido_nao_vira_total_silencioso():
    with pytest.raises(CompatibilityError):
        normalize_payload(cb_payload(totais_por_moeda=[{"moeda": "BRL", "total": "NaN", "linhas": 1}]))
    with pytest.raises(CompatibilityError):
        normalize_payload(cb_payload(contas=[{"nome": "sem moeda", "saldo": "10"}]))


def test_multiplas_moedas_ficam_separadas():
    snapshot = normalize_payload(cb_payload(
        contas=[
            {"nome": "BRL", "moeda": "BRL", "saldo": "10"},
            {"nome": "USD", "moeda": "USD", "saldo": "5"},
        ],
        totais_por_moeda=[
            {"moeda": "BRL", "total": "10", "linhas": 1},
            {"moeda": "USD", "total": "5", "linhas": 1},
        ],
    ))

    assert snapshot.currencies == ("BRL", "USD")
    assert [money.amount for money in snapshot.totals] == [Decimal("10"), Decimal("5")]


def test_links_externos_sao_descartados_e_ids_sem_prefixo_ficam_opacos():
    snapshot = normalize_payload(cb_payload(contas=[{"id": "7", "nome": "Conta", "moeda": "BRL", "saldo": "1", "endereco": "https://evil.test"}], totais_por_moeda=[{"moeda": "BRL", "total": "1", "linhas": 1}]))

    assert snapshot.accounts[0].id.startswith("controle-bancario:conta:")
    assert snapshot.accounts[0].link == ""
