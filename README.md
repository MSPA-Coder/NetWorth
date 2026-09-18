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
deixa de aparecer -- taxa velha demais não é taxa, é chute. No VPS quem roda é
o `atualizar-cambio.timer` do repositório `manutencao`.

Para desenhar a curva de investimentos desde 2022 a série precisa alcançar
aquele período, e por padrão ela começa no corte do caixa (31/12/2025):

```bash
docker compose --env-file .env.docker -f compose.yaml run --rm web python manage.py atualizar_cambio --desde 2022-04-29
```

## O histórico, e a foto de cada dia

A tela **Histórico** desenha duas curvas: investimentos desde a primeira
posição (maio de 2022) e patrimônio -- caixa mais investimentos -- desde
01/01/2026, que é o corte do caixa. Antes dele o Controle Bancário responde com
zero contas, e desenhar aquele zero como patrimônio mostraria só a parte
investida com nome de total.

A curva sai de uma **foto por dia**, gravada por um comando. Sem as fotos,
desenhar anos de série exigiria perguntar uma data de cada vez às fontes a cada
abertura de tela:

```bash
docker compose --env-file .env.docker -f compose.yaml run --rm web python manage.py registrar_foto
```

Sem argumento ele **refaz os últimos sete dias fechados** -- uma despesa de
ontem pode ser lançada amanhã, e a foto de ontem precisa enxergá-la. Hoje nunca
é fotografado: o dia não fechou. E **só foto completa é gravada**: se uma fonte
não respondeu, ou deixou de fora uma posição sem cotação, aquele dia fica sem
foto e o comando termina com erro, para o timer alertar. Uma foto feita com
uma fonte fora do ar viraria um degrau permanente no gráfico.

| Para quê | Comando |
|---|---|
| a história inteira, uma vez | `registrar_foto --desde 2022-05-09` |
| um dia só | `registrar_foto --data 2026-09-16` |
| refazer depois de corrigir dado nas fontes | `registrar_foto --desde 2025-12-31 --refazer` |

Sem `--refazer`, a data que já tem foto é pulada -- então repetir a carga só
preenche o que faltou. No VPS quem roda é o `registrar-foto.timer` do
repositório `manutencao`, às 07:40, depois da coleta de câmbio.

## Produção (VPS1)

`https://networth-mspa.duckdns.org`, com o mesmo arranjo dos irmãos: o Nginx
do host termina TLS e encaminha para `127.0.0.1:5701`. O acesso SSH ao
servidor é pelo Tailscale. O código no servidor espelha o `main` e é implantado
com `~/deploy.sh networth`.

**Primeira publicação:**

1. Clonar com a deploy key (apelido `github-networth` no `~/.ssh/config`) em
   `~/apps/networth`.
2. Criar `.env.vps` a partir de `.env.vps.example`.
3. Criar `.secrets/`, com modo `700` e arquivos com modo `644`. O PostgreSQL e
   o Django rodam com usuários diferentes, e o Compose sem Swarm monta cada
   arquivo com as permissões do host. São quatro arquivos:
   - `django_secret_key` e `postgres_password`: gerados na hora;
   - `fonte_cb_token`: **o mesmo valor** do `.secrets/patrimonio_token` do
     Controle Bancário (lá ele pertence ao usuário do contêiner, então a
     leitura exige `sudo`);
   - `fonte_crv_token`: o mesmo valor do `.secrets/patrimonio_token` do
     Controle de Renda Variável.
4. Subir com `docker compose --env-file .env.vps -f compose.yaml up --build -d`.
5. Emitir o certificado e instalar o vhost `networth` pelo instalador central
   do Nginx (`manutencao/vps/nginx`).
6. Criar o login:
   `docker compose --env-file .env.vps -f compose.yaml exec web python manage.py createsuperuser`.
7. Preencher a série de câmbio uma vez com `atualizar_cambio`; dali em diante
   o timer cuida dela.
8. Preencher o histórico uma vez com `registrar_foto --desde 2022-05-09`; dali
   em diante o timer cuida dele. A carga são milhares de leituras das duas
   fontes e leva alguns minutos; repetir o comando preenche o que tiver
   faltado.

**Trocar um token** é uma operação dos dois lados ao mesmo tempo. Se só o
publicador mudar, a fonte passa a responder 401. A tela diz qual fonte falhou
e avisa, antes do número, que aquele total não é o patrimônio inteiro.

## Validação

```bash
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
```

[Controle Bancário]: ../ControleBancario
[Controle de Renda Variável]: ../ControleRendaVariavel
