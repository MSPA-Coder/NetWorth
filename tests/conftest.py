"""A suíte não fala com a internet.

Este aplicativo existe para buscar coisas pela rede -- duas fontes de patrimônio
e uma série de câmbio. É exatamente por isso que a suíte precisa de uma trava:
um teste que esqueça de simular a resposta **passa** consultando o Yahoo de
verdade, verde e enganoso, até o dia em que a rede cair ou a resposta mudar. E
ele já aconteceu aqui: um teste de falha de coleta gravou, sem querer, a cotação
real do dólar.

A trava é autouse e global. Quem quiser simular resposta continua substituindo
`urlopen` no seu próprio teste -- a substituição dele vem depois desta e ganha.
"""

from __future__ import annotations

import urllib.request

import pytest


class RedeNaSuiteError(RuntimeError):
    """Um teste tentou sair para a internet sem simular a resposta."""


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    def recusar(*_args, **_kwargs):
        raise RedeNaSuiteError(
            "A suíte não acessa a rede. Substitua `urlopen` no teste e devolva a "
            "resposta que você quer exercitar."
        )

    monkeypatch.setattr(urllib.request, "urlopen", recusar)
