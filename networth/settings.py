"""Configuração do NetWorth.

O aplicativo é **só de leitura sobre o que é dos outros**: ele não cadastra
conta, posição nem lançamento. O banco existe para três coisas — o login, a
série de câmbio e a foto diária do patrimônio — e nenhuma delas é cópia do dado
alheio.

A postura de segurança é a mesma dos aplicativos irmãos, pelo mesmo
`sharedauth`: segredo em arquivo sob o Compose, CSP fechada sem inline,
cabeçalhos defensivos, cookie de sessão fechado. O que ele **não** tem é matriz
de permissão: quem entra vê o patrimônio inteiro, porque um consolidado filtrado
esconderia parte do patrimônio sem avisar.
"""

import os
from pathlib import Path

from sharedauth.secrets import resolver_segredo
from sharedauth.ui import CAMINHO_ESTATICO

BASE_DIR = Path(__file__).resolve().parent.parent


def _segredo_obrigatorio(nome: str) -> str:
    """Mesma precedência dos irmãos: `NOME_FILE` antes de `NOME`.

    Sob o Compose a variável direta é recusada por completo: uma sobra no
    ambiente do processo substituiria em silêncio o segredo operacional.
    """
    exige_arquivo = os.environ.get("REQUIRE_FILE_SECRETS", "false").lower() == "true"
    return resolver_segredo(nome, aceitar_variavel=not exige_arquivo, obrigatorio=True)


SECRET_KEY = _segredo_obrigatorio("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    # Sem `django.contrib.admin`, pela mesma razão do Controle Bancário: ele
    # registraria o modelo de usuário e abriria uma porta de administração fora
    # das telas do produto. Este aplicativo tem menos ainda a administrar.
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "consolidado",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "consolidado.security.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "networth.urls"
WSGI_APPLICATION = "networth.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "networth"),
        "USER": os.environ.get("POSTGRES_USER", "networth"),
        "PASSWORD": _segredo_obrigatorio("POSTGRES_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 600,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# So pega o NOME, no boot. A garantia de verdade e `consolidado.papel_do_banco`,
# que pergunta `is_superuser` ao servidor em cada conexao: o POSTGRES_USER da
# imagem nasce superusuario com qualquer nome.
if DATABASES["default"]["USER"] == "postgres":
    raise RuntimeError(
        "POSTGRES_USER nao pode ser 'postgres': e o superusuario administrativo do "
        "cluster. Use uma conta dedicada da aplicacao."
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static", CAMINHO_ESTATICO]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "consolidado:dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", "3600"))
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

USE_HTTPS = os.environ.get("USE_HTTPS", "False").lower() == "true"
if USE_HTTPS:
    if not CSRF_TRUSTED_ORIGINS:
        raise RuntimeError(
            "USE_HTTPS=True exige CSRF_TRUSTED_ORIGINS com as origens https do servico."
        )
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"padrao": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "padrao"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
