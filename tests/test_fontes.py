"""Quais fontes existem, e o que fazer com uma configurada pela metade.

Erro de implantação tem de ser **visível**. Uma fonte com endereço e sem token
não é consultada -- tentar falaria 401 por requisição, o que é ruído -- mas ela
também não some: aparece como "configurada pela metade", e o consolidado deixa
de ser completo por causa dela.
"""

from __future__ import annotations

import pytest

from consolidado import fontes


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for apelido, _nome, _papel in fontes.FONTES_CONHECIDAS:
        for sufixo in ("URL", "TOKEN", "TOKEN_FILE", "ENDERECO_PUBLICO"):
            monkeypatch.delenv(f"FONTE_{apelido}_{sufixo}", raising=False)
    monkeypatch.delenv("REQUIRE_FILE_SECRETS", raising=False)


def test_sem_configuracao_nao_ha_fonte():
    assert fontes.fontes_configuradas() == []
    assert fontes.fontes_incompletas() == []


def test_fonte_completa_e_usada(monkeypatch):
    monkeypatch.setenv("FONTE_CB_URL", "http://cb.teste")
    monkeypatch.setenv("FONTE_CB_TOKEN", "um-token-qualquer")

    (fonte,) = fontes.fontes_configuradas()

    assert fonte.apelido == "CB"
    assert fonte.papel == "caixa"
    assert fonte.endereco_do_resumo == "http://cb.teste/patrimonio/v1/resumo"
    assert fontes.fontes_incompletas() == []


def test_barra_no_fim_do_endereco_nao_duplica(monkeypatch):
    monkeypatch.setenv("FONTE_CB_URL", "http://cb.teste/")
    monkeypatch.setenv("FONTE_CB_TOKEN", "um-token-qualquer")

    (fonte,) = fontes.fontes_configuradas()

    assert fonte.endereco_do_resumo == "http://cb.teste/patrimonio/v1/resumo"


def test_endereco_sem_token_nao_e_consultado_mas_aparece(monkeypatch):
    monkeypatch.setenv("FONTE_CRV_URL", "http://crv.teste")

    assert fontes.fontes_configuradas() == []
    assert fontes.fontes_incompletas() == ["Controle de Renda Variável"]


def test_token_sem_endereco_tambem_aparece(monkeypatch):
    monkeypatch.setenv("FONTE_CRV_TOKEN", "um-token-qualquer")

    assert fontes.fontes_configuradas() == []
    assert fontes.fontes_incompletas() == ["Controle de Renda Variável"]


def test_sob_o_compose_o_token_vem_de_arquivo(monkeypatch, tmp_path):
    """`REQUIRE_FILE_SECRETS=true` é o contrato do Compose, igual ao dos irmãos.

    Sem esta trava, uma sobra de `FONTE_CB_TOKEN` no ambiente do processo
    substituiria em silêncio o segredo montado como arquivo.
    """
    arquivo = tmp_path / "fonte_cb_token"
    arquivo.write_text("um-token-de-arquivo", encoding="utf-8")
    monkeypatch.setenv("REQUIRE_FILE_SECRETS", "true")
    monkeypatch.setenv("FONTE_CB_URL", "http://cb.teste")
    monkeypatch.setenv("FONTE_CB_TOKEN", "sobra-no-ambiente")

    assert fontes.fontes_configuradas() == []

    monkeypatch.setenv("FONTE_CB_TOKEN_FILE", str(arquivo))
    (fonte,) = fontes.fontes_configuradas()

    assert fonte.token == "um-token-de-arquivo"


def test_a_ordem_das_fontes_e_a_da_lista(monkeypatch):
    for apelido in ("CB", "CRV"):
        monkeypatch.setenv(f"FONTE_{apelido}_URL", f"http://{apelido.lower()}.teste")
        monkeypatch.setenv(f"FONTE_{apelido}_TOKEN", "um-token-qualquer")

    assert [f.apelido for f in fontes.fontes_configuradas()] == ["CB", "CRV"]


def test_o_link_usa_o_endereco_da_fonte_quando_nao_ha_publico(monkeypatch):
    monkeypatch.setenv("FONTE_CB_URL", "https://cb.teste/")
    monkeypatch.setenv("FONTE_CB_TOKEN", "um-token-qualquer")

    (fonte,) = fontes.fontes_configuradas()

    assert fonte.link("/transactions/?account_id=7") == "https://cb.teste/transactions/?account_id=7"


def test_o_endereco_publico_vale_so_para_o_link(monkeypatch):
    """Local, o contêiner lê por `host.docker.internal` e o navegador abre `localhost`."""
    monkeypatch.setenv("FONTE_CB_URL", "http://host.docker.internal:5201")
    monkeypatch.setenv("FONTE_CB_ENDERECO_PUBLICO", "http://localhost:5201")
    monkeypatch.setenv("FONTE_CB_TOKEN", "um-token-qualquer")

    (fonte,) = fontes.fontes_configuradas()

    assert fonte.endereco_do_resumo == "http://host.docker.internal:5201/patrimonio/v1/resumo"
    assert fonte.link("/x") == "http://localhost:5201/x"
