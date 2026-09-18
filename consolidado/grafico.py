"""O gráfico das curvas, desenhado no servidor como SVG.

POR QUE SVG, E NÃO UMA BIBLIOTECA DE GRÁFICOS

Este aplicativo não tem nenhum JavaScript, e a CSP dele é fechada. Uma curva
de linha não precisa de mais que um `<path>`: montá-lo aqui mantém a página sem
script, sem biblioteca para atualizar e sem os defeitos de canvas que o
Controle Bancário pagou (medida herdada entre trocas e canvas vazando a cada
filtro). Os números exatos ficam na tabela, logo abaixo do gráfico.

Módulo puro: recebe pontos, devolve geometria. Nada de banco nem de Django.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

LARGURA = 960
ALTURA = 360
MARGEM_ESQUERDA = 72
MARGEM_DIREITA = 16
MARGEM_TOPO = 16
MARGEM_BAIXO = 32

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


@dataclass(frozen=True, slots=True)
class PontoDaCurva:
    x: float
    y: float
    data: date
    valor: Decimal


@dataclass(frozen=True, slots=True)
class Curva:
    rotulo: str
    classe: str
    caminho: str
    pontos: tuple[PontoDaCurva, ...] = ()


@dataclass(frozen=True, slots=True)
class Marca:
    posicao: float
    rotulo: str


@dataclass(frozen=True, slots=True)
class Grafico:
    largura: int
    altura: int
    curvas: tuple[Curva, ...]
    eixo_x: tuple[Marca, ...]
    eixo_y: tuple[Marca, ...]
    esquerda: int
    direita: int
    topo: int
    base: int


def _passo_redondo(maximo: float, divisoes: int = 5) -> float:
    """O menor passo "redondo" (1, 2, 2,5 ou 5 vezes uma potência de 10) que
    cobre `maximo` em até `divisoes` intervalos."""
    if maximo <= 0:
        return 1.0
    bruto = maximo / divisoes
    potencia = 10 ** (len(str(int(bruto))) - 1) if bruto >= 1 else 1
    for fator in (1, 2, 2.5, 5, 10):
        if bruto <= fator * potencia:
            return fator * potencia
    return 10.0 * potencia


def _rotulo_de_valor(valor: float) -> str:
    """Rótulo curto do eixo: "150 mil", "1,2 mi"."""
    if abs(valor) >= 1_000_000:
        texto = f"{valor / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{texto.replace('.', ',')} mi"
    if abs(valor) >= 1_000:
        return f"{valor / 1_000:.0f} mil"
    return f"{valor:.0f}"


def _marcas_de_data(inicio: date, fim: date) -> list[date]:
    """Primeiro dia de cada ano, em série longa; de cada mês (espaçados), em
    série curta."""
    if (fim - inicio).days > 540:
        return [date(ano, 1, 1) for ano in range(inicio.year + 1, fim.year + 1)]
    meses = (fim.year - inicio.year) * 12 + fim.month - inicio.month
    passo = max(1, -(-meses // 8))
    marcas = []
    ano, mes = inicio.year, inicio.month
    while True:
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
        dia = date(ano, mes, 1)
        if dia > fim:
            return marcas[::passo]
        marcas.append(dia)


def _rotulo_de_data(dia: date, longa: bool) -> str:
    return str(dia.year) if longa else f"{MESES[dia.month - 1]}/{dia.year % 100:02d}"


def montar(pontos, series: tuple[tuple[str, str, str], ...]) -> Grafico | None:
    """Monta o gráfico. `series` é uma sequência de (atributo, rótulo, classe CSS).

    Um valor `None` interrompe a linha: o buraco aparece, em vez de uma reta
    ligando os dois lados como se o intervalo tivesse existido.
    """
    datas = [ponto.data for ponto in pontos]
    valores = [
        float(getattr(ponto, atributo))
        for ponto in pontos
        for atributo, _rotulo, _classe in series
        if getattr(ponto, atributo) is not None
    ]
    if not datas or not valores:
        return None

    inicio, fim = datas[0], datas[-1]
    passo = _passo_redondo(max(max(valores), 0.0))
    teto = passo * max(1, -(-max(max(valores), 0.0) // passo))
    piso = min(0.0, passo * (min(valores) // passo))
    esquerda, direita = MARGEM_ESQUERDA, LARGURA - MARGEM_DIREITA
    topo, base = MARGEM_TOPO, ALTURA - MARGEM_BAIXO
    extensao = max((fim - inicio).days, 1)

    def x(dia: date) -> float:
        return esquerda + (dia - inicio).days / extensao * (direita - esquerda)

    def y(valor: float) -> float:
        return base - (valor - piso) / (teto - piso) * (base - topo)

    curvas = []
    for atributo, rotulo, classe in series:
        comandos = []
        pontos_da_curva = []
        caneta_no_papel = False
        for ponto in pontos:
            valor = getattr(ponto, atributo)
            if valor is None:
                caneta_no_papel = False
                continue
            letra = "L" if caneta_no_papel else "M"
            comandos.append(f"{letra}{x(ponto.data):.1f} {y(float(valor)):.1f}")
            pontos_da_curva.append(
                PontoDaCurva(
                    x=round(x(ponto.data), 1),
                    y=round(y(float(valor)), 1),
                    data=ponto.data,
                    valor=valor,
                )
            )
            caneta_no_papel = True
        if comandos:
            curvas.append(
                Curva(
                    rotulo=rotulo,
                    classe=classe,
                    caminho=" ".join(comandos),
                    pontos=tuple(pontos_da_curva),
                )
            )

    longa = (fim - inicio).days > 540
    eixo_x = tuple(
        Marca(posicao=round(x(dia), 1), rotulo=_rotulo_de_data(dia, longa))
        for dia in _marcas_de_data(inicio, fim)
    )
    eixo_y = []
    valor = piso
    while valor <= teto + passo / 2:
        eixo_y.append(Marca(posicao=round(y(valor), 1), rotulo=_rotulo_de_valor(valor)))
        valor += passo
    return Grafico(
        largura=LARGURA,
        altura=ALTURA,
        curvas=tuple(curvas),
        eixo_x=eixo_x,
        eixo_y=tuple(eixo_y),
        esquerda=esquerda,
        direita=direita,
        topo=topo,
        base=base,
    )


def ultimo_de_cada_mes(pontos) -> list:
    """O último ponto de cada mês, do mais recente para o mais antigo."""
    por_mes = {}
    for ponto in pontos:
        por_mes[(ponto.data.year, ponto.data.month)] = ponto
    return [por_mes[chave] for chave in sorted(por_mes, reverse=True)]


def variacao(atual: Decimal | None, anterior: Decimal | None) -> Decimal | None:
    if atual is None or anterior is None or anterior == 0:
        return None
    return (atual - anterior) / abs(anterior) * 100
