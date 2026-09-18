"""Lê o resumo publicado por cada fonte e diz, sem rodeio, o que conseguiu ler.

A REGRA QUE SUSTENTA A CONFIANÇA NA TELA

Uma fonte fora do ar **não pode produzir um número menor sem dizer**. É o pior
defeito possível neste aplicativo: o patrimônio "caiu" trinta por cento porque o
Renda Variável estava reiniciando, e ninguém percebe — o número continua
plausível, e é justamente por isso que ninguém desconfia.

Daí a forma deste módulo: ele nunca devolve só números. Devolve `Consolidado`,
que carrega o estado de **cada** fonte, e a tela é obrigada a passar por ele
para chegar aos totais. Não existe caminho neste código que produza um total
sem produzir, junto, de quantas fontes ele é feito.

O QUE ELE NÃO FAZ

Não recalcula nada. Saldo é do Controle Bancário, valor a mercado é do Controle
de Renda Variável. Este módulo transporta e soma **dentro de cada moeda**;
converter entre moedas é outra decisão, tomada com a série de câmbio datada.

E não confia no que chega: o que vem pela rede é dado, não instrução. Todo valor
monetário é convertido para `Decimal` a partir de texto, e uma linha malformada
derruba a fonte inteira para o estado "respondeu errado" em vez de virar um
total silenciosamente torto.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from consolidado.fontes import Fonte, fontes_configuradas, fontes_incompletas

CONTRATO_ACEITO = "patrimonio/v1"
TEMPO_LIMITE_SEGUNDOS = 8
TAMANHO_MAXIMO_BYTES = 8 * 1024 * 1024

logger = logging.getLogger(__name__)

OK = "ok"
FALHOU = "falhou"
NAO_RESPONDEU = "nao_respondeu"


@dataclass(frozen=True, slots=True)
class Linha:
    """Uma coisa que vale dinheiro, seja ela conta ou posição."""

    fonte: str
    papel: str
    titular: str
    instituicao: str
    descricao: str
    moeda: str
    valor: Decimal
    detalhe: str = ""


@dataclass
class Leitura:
    """O que uma fonte respondeu, ou por que não respondeu."""

    fonte: Fonte
    estado: str = NAO_RESPONDEU
    motivo: str = ""
    data_de_referencia: date | None = None
    linhas: list[Linha] = field(default_factory=list)
    lacunas: list[str] = field(default_factory=list)
    """O que a fonte respondeu que deixou de fora por não conseguir avaliar.

    A fonte respondeu, e o que veio está certo -- mas não é tudo. Uma posição
    sem cotação que some do total faz o patrimônio encolher com cara de
    completo, então a lacuna torna o consolidado incompleto, como uma fonte
    fora do ar.
    """

    @property
    def respondeu(self) -> bool:
        return self.estado == OK


@dataclass
class Consolidado:
    """Totais por moeda **e** de quantas fontes eles são feitos.

    As duas coisas vêm juntas de propósito. Separá-las permitiria mostrar o
    total sem mostrar que ele está incompleto, e é essa a falha que este
    aplicativo não pode ter.
    """

    leituras: list[Leitura] = field(default_factory=list)
    incompletas: list[str] = field(default_factory=list)

    @property
    def linhas(self) -> list[Linha]:
        return [linha for leitura in self.leituras for linha in leitura.linhas]

    @property
    def fontes_que_responderam(self) -> list[Leitura]:
        return [leitura for leitura in self.leituras if leitura.respondeu]

    @property
    def completo(self) -> bool:
        """Todas as fontes conhecidas responderam, por inteiro, e nenhuma está
        configurada pela metade."""
        return (
            bool(self.leituras)
            and not self.incompletas
            and all(leitura.respondeu and not leitura.lacunas for leitura in self.leituras)
        )

    @property
    def totais_por_moeda(self) -> list[dict]:
        """Uma linha por moeda. Nunca somadas entre si -- converter é decidir."""
        acumulado: dict[str, dict] = {}
        for linha in self.linhas:
            bloco = acumulado.setdefault(
                linha.moeda, {"moeda": linha.moeda, "total": Decimal("0.00"), "linhas": 0}
            )
            bloco["total"] += linha.valor
            bloco["linhas"] += 1
        return [acumulado[moeda] for moeda in sorted(acumulado)]

    def por_instituicao(self) -> list[dict]:
        acumulado: dict[tuple[str, str], dict] = {}
        for linha in self.linhas:
            chave = (linha.instituicao, linha.moeda)
            bloco = acumulado.setdefault(
                chave,
                {
                    "instituicao": linha.instituicao,
                    "moeda": linha.moeda,
                    "total": Decimal("0.00"),
                    "linhas": [],
                },
            )
            bloco["total"] += linha.valor
            bloco["linhas"].append(linha)
        return [acumulado[chave] for chave in sorted(acumulado)]


def _para_decimal(bruto, onde: str) -> Decimal:
    if not isinstance(bruto, str):
        # O contrato manda texto justamente para isto: `float` não representa
        # 0,10, e aceitar número aqui deixaria a imprecisão entrar em silêncio.
        raise ValueError(f"{onde}: valor monetário tem de vir como texto, veio {type(bruto).__name__}")
    try:
        return Decimal(bruto)
    except InvalidOperation as erro:
        raise ValueError(f"{onde}: {bruto!r} não é um número") from erro


def _texto(dados: dict, chave: str, onde: str) -> str:
    valor = dados.get(chave)
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{onde}: falta '{chave}'")
    return valor.strip()


def _nomes_por_id(colecao) -> dict[str, str]:
    """Do identificador normalizado para o nome legível, para a tela."""
    if not isinstance(colecao, list):
        return {}
    nomes = {}
    for item in colecao:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            nomes[item["id"]] = str(item.get("nome") or item["id"])
    return nomes


def interpretar(fonte: Fonte, corpo: dict) -> Leitura:
    """Transforma o envelope publicado em linhas, ou recusa o envelope inteiro.

    Recusar inteiro é deliberado: meia leitura viraria meio total, e meio total
    é indistinguível de um total menor.
    """
    contrato = corpo.get("contrato")
    if contrato != CONTRATO_ACEITO:
        return Leitura(
            fonte=fonte,
            estado=FALHOU,
            motivo=f"contrato desconhecido: {contrato!r} (esperado {CONTRATO_ACEITO!r})",
        )

    try:
        referencia = date.fromisoformat(_texto(corpo, "data_de_referencia", fonte.nome))
        titulares = _nomes_por_id(corpo.get("titulares"))
        instituicoes = _nomes_por_id(corpo.get("instituicoes"))
        papel = str(corpo.get("papel") or fonte.papel)

        linhas: list[Linha] = []
        for indice, conta in enumerate(corpo.get("contas") or []):
            onde = f"{fonte.nome}/contas[{indice}]"
            linhas.append(
                Linha(
                    fonte=fonte.nome,
                    papel=papel,
                    titular=titulares.get(conta.get("titular"), str(conta.get("titular") or "—")),
                    instituicao=instituicoes.get(
                        conta.get("instituicao"), str(conta.get("instituicao") or "—")
                    ),
                    descricao=_texto(conta, "nome", onde),
                    moeda=_texto(conta, "moeda", onde),
                    valor=_para_decimal(conta.get("saldo"), onde),
                )
            )
        for indice, posicao in enumerate(corpo.get("posicoes") or []):
            onde = f"{fonte.nome}/posicoes[{indice}]"
            linhas.append(
                Linha(
                    fonte=fonte.nome,
                    papel=papel,
                    titular=titulares.get(posicao.get("titular"), str(posicao.get("titular") or "—")),
                    instituicao=instituicoes.get(
                        posicao.get("instituicao"), str(posicao.get("instituicao") or "—")
                    ),
                    descricao=_texto(posicao, "instrumento", onde),
                    moeda=_texto(posicao, "moeda", onde),
                    valor=_para_decimal(posicao.get("valor_a_mercado"), onde),
                    detalhe=str(posicao.get("preco_em") or ""),
                )
            )
        lacunas = _lacunas(corpo.get("omitidas"), fonte.nome)
    except ValueError as erro:
        return Leitura(fonte=fonte, estado=FALHOU, motivo=str(erro))

    return Leitura(
        fonte=fonte, estado=OK, data_de_referencia=referencia, linhas=linhas, lacunas=lacunas
    )


def _lacunas(omitidas, onde: str) -> list[str]:
    """O que a fonte deixou de fora sem ter como avaliar.

    O envelope conta três omissões, e só uma é lacuna. Carteira simulada não é
    patrimônio, e opção fica de fora por decisão até ser publicada com o mesmo
    cuidado das ações: as duas ficam de fora em toda data, então não abrem um
    buraco na série. Posição **sem cotação** é outra coisa: ela vale dinheiro, e
    o total sem ela é menor do que o patrimônio.
    """
    if omitidas is None:
        return []
    if not isinstance(omitidas, dict):
        raise ValueError(f"{onde}: 'omitidas' tem de ser um objeto")
    sem_cotacao = omitidas.get("sem_cotacao", 0)
    if isinstance(sem_cotacao, bool) or not isinstance(sem_cotacao, int) or sem_cotacao < 0:
        raise ValueError(f"{onde}: 'omitidas.sem_cotacao' tem de ser um inteiro")
    if not sem_cotacao:
        return []
    return [
        f"{sem_cotacao} posição sem cotação ficou de fora"
        if sem_cotacao == 1
        else f"{sem_cotacao} posições sem cotação ficaram de fora"
    ]


def buscar(fonte: Fonte, referencia: date | None = None) -> Leitura:
    """Uma requisição, com tempo limite curto. Falhar aqui é normal, não é erro.

    O tempo limite é curto de propósito: a tela precisa abrir mesmo com uma
    fonte inacessível, e dizer que ela não respondeu vale mais do que esperar
    trinta segundos por ela.
    """
    endereco = fonte.endereco_do_resumo
    if referencia is not None:
        endereco = f"{endereco}?data={referencia.isoformat()}"
    pedido = urllib.request.Request(
        endereco,
        headers={"Authorization": f"Bearer {fonte.token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE_SEGUNDOS) as resposta:
            bruto = resposta.read(TAMANHO_MAXIMO_BYTES + 1)
    except urllib.error.HTTPError as erro:
        motivo = {
            401: "token recusado pela fonte",
            503: "a fonte respondeu que a integração não está configurada nela",
        }.get(erro.code, f"a fonte respondeu HTTP {erro.code}")
        return Leitura(fonte=fonte, estado=FALHOU, motivo=motivo)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        logger.warning("Fonte %s não respondeu: %s", fonte.apelido, erro)
        return Leitura(fonte=fonte, estado=NAO_RESPONDEU, motivo="não respondeu")

    if len(bruto) > TAMANHO_MAXIMO_BYTES:
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta grande demais")
    try:
        corpo = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta não é JSON")
    if not isinstance(corpo, dict):
        return Leitura(fonte=fonte, estado=FALHOU, motivo="resposta não é um objeto JSON")
    leitura = interpretar(fonte, corpo)
    if (
        referencia is not None
        and leitura.respondeu
        and leitura.data_de_referencia != referencia
    ):
        # Uma fonte que ignora `?data=` responde o patrimônio de hoje para uma
        # pergunta sobre março, e o número passa por histórico. É a forma mais
        # silenciosa de inventar o passado, então a leitura inteira é recusada.
        return Leitura(
            fonte=fonte,
            estado=FALHOU,
            motivo=(
                f"a fonte respondeu a data {leitura.data_de_referencia:%d/%m/%Y} "
                f"para a pergunta sobre {referencia:%d/%m/%Y}"
            ),
        )
    return leitura


def consolidar(referencia: date | None = None) -> Consolidado:
    """O patrimônio como ele está agora, com o estado de cada fonte junto."""
    return Consolidado(
        leituras=[buscar(fonte, referencia) for fonte in fontes_configuradas()],
        incompletas=fontes_incompletas(),
    )
