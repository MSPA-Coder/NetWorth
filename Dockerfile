# syntax=docker/dockerfile:1.7
# Mesma forma dos aplicativos irmãos, com o mesmo raciocínio por trás de cada
# escolha -- só que menor, porque este projeto tem menos dentro.
#
# Base fixada por DIGEST do índice multi-arquitetura, e não pela tag:
# `python:3.14-slim` é alvo móvel, e a imagem servida precisa nascer da mesma
# base que a varredura de vulnerabilidade examinou.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip setuptools


# builder: resolve as dependências a partir do `uv.lock`, num venv isolado.
#
# `--locked` e não `--frozen`: o segundo usa o lock sem conferir o
# `pyproject.toml` e instala versões antigas em silêncio quando alguém edita a
# declaração e esquece de rodar `uv lock`. Importa mais aqui porque
# `sharedauth` vem de repositório Git, e o lock o prende ao COMMIT, não à tag.
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade "uv==0.12.10"

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
COPY consolidado ./consolidado
COPY networth ./networth
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable


# quality: Ruff e a suíte. Nunca é o estágio publicado.
FROM base AS quality

COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra dev

ENV DJANGO_SETTINGS_MODULE=networth.settings \
    PYTHONPATH=/workspace \
    RUFF_CACHE_DIR=/tmp/ruff-cache \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"

# Sem o manifesto, qualquer template com `{% static %}` estoura com "Missing
# staticfiles manifest entry" e a suíte não conseguiria renderizar uma tela.
RUN DJANGO_SECRET_KEY=build-only-nao-usada-em-execucao \
    POSTGRES_PASSWORD=build-only \
    python manage.py collectstatic --noinput --clear >/dev/null

CMD ["sh", "-c", "ruff check . && pytest"]


# runtime: só dependências e arquivos de execução.
FROM base AS runtime

ENV PATH="/opt/venv/bin:${PATH}"

RUN groupadd --system app \
    && useradd --system --gid app --no-create-home --home-dir /workspace app \
    && mkdir -p /workspace/staticfiles /workspace/logs \
    && chown app:app /workspace/staticfiles /workspace/logs

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=755 manage.py ./manage.py
COPY consolidado ./consolidado
COPY networth ./networth
COPY templates ./templates
COPY static ./static

# Tira `pip` e `setuptools` da imagem SERVIDA: são ferramenta de build e não
# têm uso aqui. A última linha é a própria verificação -- se `pip` continuar no
# PATH, o build falha em vez de entregar uma imagem que só parece limpa.
RUN set -eu; \
    for raiz in /usr/local/lib/python*/site-packages /opt/venv/lib/python*/site-packages; do \
      [ -d "$raiz" ] || continue; \
      rm -rf "$raiz"/pip "$raiz"/pip-*.dist-info \
             "$raiz"/setuptools "$raiz"/setuptools-*.dist-info \
             "$raiz"/pkg_resources "$raiz"/_distutils_hack \
             "$raiz"/distutils-precedence.pth \
             "$raiz"/wheel "$raiz"/wheel-*.dist-info; \
    done; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
          /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.*; \
    ! command -v pip

USER app

ENV DJANGO_SETTINGS_MODULE=networth.settings \
    PYTHONPATH=/workspace

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--worker-class", "gthread", "--timeout", "60", "--no-control-socket", "networth.wsgi:application"]
