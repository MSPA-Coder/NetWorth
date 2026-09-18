"""Quais sistemas publicam patrimônio, e como chegar em cada um.

POR QUE ISTO É CONFIGURAÇÃO, E NÃO CADASTRO EM TELA

São duas fontes — o Controle Bancário e o Controle de Renda Variável — e elas
mudam de ano em ano. Uma tela de cadastro de fonte convidaria este aplicativo a
virar um integrador genérico, que é exatamente o que o estudo pediu para ele não
ser: ele lê dois sistemas conhecidos e soma. Configuração por ambiente também
mantém o token onde os outros segredos deste projeto já moram, em arquivo
montado, e fora do banco.

O TOKEN É POR FONTE

Cada sistema tem o seu, com valores independentes. Um vazamento não abre os
dois, e a rotação de um não derruba o outro. O nome da variável segue o padrão
`FONTE_<APELIDO>_URL` / `FONTE_<APELIDO>_TOKEN`.

O ENDEREÇO QUE O NAVEGADOR ALCANÇA

O drilldown termina na tela do sistema de origem, e quem abre o link é o
navegador, não este servidor. Em produção os dois são o mesmo endereço público.
Na máquina local não são: o contêiner alcança a fonte por
`host.docker.internal`, que o navegador não resolve. Para esse caso existe
`FONTE_<APELIDO>_ENDERECO_PUBLICO`, opcional, que vale só para os links.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sharedauth.secrets import SegredoInvalidoError, resolver_segredo

#: Apelido, nome que aparece na tela, e o papel que ela ocupa no patrimônio.
#: A ordem é a de exibição.
FONTES_CONHECIDAS: tuple[tuple[str, str, str], ...] = (
    ("CB", "Controle Bancário", "caixa"),
    ("CRV", "Controle de Renda Variável", "investimento"),
)

CAMINHO_DO_RESUMO = "/patrimonio/v1/resumo"


@dataclass(frozen=True, slots=True)
class Fonte:
    apelido: str
    nome: str
    papel: str
    url: str
    token: str
    endereco_publico: str = ""

    @property
    def endereco_do_resumo(self) -> str:
        return f"{self.url.rstrip('/')}{CAMINHO_DO_RESUMO}"

    def link(self, caminho: str) -> str:
        """O endereço, para o navegador, de um caminho publicado pela fonte."""
        return f"{(self.endereco_publico or self.url).rstrip('/')}{caminho}"


def _segredo(nome: str) -> str:
    """O token da fonte, ou vazio quando não há um utilizável.

    Mesmo contrato dos outros aplicativos: sob o Compose
    (`REQUIRE_FILE_SECRETS=true`) o segredo vem de arquivo montado, e a variável
    direta só serve a comando local explícito.
    """
    exige_arquivo = os.environ.get("REQUIRE_FILE_SECRETS", "false").lower() == "true"
    try:
        return resolver_segredo(nome, aceitar_variavel=not exige_arquivo) or ""
    except SegredoInvalidoError:
        return ""


def fontes_configuradas() -> list[Fonte]:
    """As fontes que têm endereço e token. As demais simplesmente não existem.

    Uma fonte configurada pela metade — endereço sem token — é erro de
    implantação, e some daqui de propósito: a tela informa de quantas fontes o
    total é feito, então uma fonte ausente aparece como total incompleto, que é
    visível. Tentar falar com ela sem credencial só produziria um 401 por
    requisição, que é ruído.
    """
    configuradas = []
    for apelido, nome, papel in FONTES_CONHECIDAS:
        url = (os.environ.get(f"FONTE_{apelido}_URL") or "").strip()
        token = _segredo(f"FONTE_{apelido}_TOKEN")
        publico = (os.environ.get(f"FONTE_{apelido}_ENDERECO_PUBLICO") or "").strip()
        if url and token:
            configuradas.append(
                Fonte(
                    apelido=apelido,
                    nome=nome,
                    papel=papel,
                    url=url,
                    token=token,
                    endereco_publico=publico,
                )
            )
    return configuradas


def fontes_incompletas() -> list[str]:
    """Nomes das fontes com endereço **ou** token, mas não os dois.

    Existe para a tela poder dizer "o Renda Variável está configurado pela
    metade" em vez de fingir que ele nunca existiu. Erro de implantação tem de
    ser visível para quem implanta.
    """
    pendentes = []
    for apelido, nome, _papel in FONTES_CONHECIDAS:
        url = (os.environ.get(f"FONTE_{apelido}_URL") or "").strip()
        token = _segredo(f"FONTE_{apelido}_TOKEN")
        if bool(url) != bool(token):
            pendentes.append(nome)
    return pendentes
