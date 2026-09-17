# NetWorth

Responde **"quanto eu tenho"**, somando o caixa do [Controle Bancário] com os
investimentos do [Controle de Renda Variável].

Ele é só de leitura. Não cadastra conta, posição, lançamento nem categoria, e
não recalcula nada: saldo é do Controle Bancário, valor a mercado é do Controle
de Renda Variável. Cada um deles publica uma foto do que sabe, numa rota
autenticada por token (`GET /patrimonio/v1/resumo`), e este aplicativo lê as
duas e soma **dentro de cada moeda**.

## O que ele garante

- **um total nunca aparece sem dizer de quantas fontes ele é feito.** Uma fonte
  fora do ar não pode produzir um patrimônio menor em silêncio -- o número
  continuaria plausível, e ninguém desconfiaria;
- **a conversão usa a taxa do dia da foto**, nunca a de hoje, e mostra a data e a
  fonte de cada taxa usada. Sem taxa, ou com taxa velha demais, o total em moeda
  base simplesmente não aparece: um número redondo e errado é pior que dois
  números certos;
- **ele não escreve nos outros sistemas**, e não conhece o banco de nenhum deles.

## Rodar local

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Configuração mínima (ambiente):

| Variável | Para quê |
|---|---|
| `DJANGO_SECRET_KEY` | segredo do Django |
| `POSTGRES_*` | banco: login, série de câmbio e foto diária |
| `FONTE_CB_URL` / `FONTE_CB_TOKEN` | Controle Bancário |
| `FONTE_CRV_URL` / `FONTE_CRV_TOKEN` | Controle de Renda Variável |

Uma fonte sem endereço **ou** sem token simplesmente não é consultada, e a tela
diz que está configurada pela metade. Erro de implantação tem de ser visível.

```bash
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py createsuperuser
.venv\Scripts\python.exe manage.py runserver
```

## A série de câmbio

O total em moeda base depende de uma série diária, e ela é preenchida por um
comando -- não por consulta ao vivo a cada tela:

```bash
docker compose --env-file .env.docker -f compose.yaml run --rm web python manage.py atualizar_cambio
```

Ele busca no Yahoo (a mesma fonte que o Controle de Renda Variável usa para
cotação), pega as moedas que as fontes estiverem mostrando e preenche o que
faltar. É idempotente e **nunca reescreve uma taxa já gravada**: aquele número
pode ter sustentado um patrimônio que alguém já olhou.

Vale rodar diariamente. Sem coleta por mais de uma semana, o total em moeda base
deixa de aparecer -- taxa velha demais não é taxa, é chute.

## Validação

```bash
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
```

[Controle Bancário]: ../ControleBancario
[Controle de Renda Variável]: ../ControleRendaVariavel
