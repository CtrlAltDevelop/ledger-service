"""Settings for the ledger service, read from the environment.

Everything that differs between a laptop and production comes from an
environment variable; see `.env.example` for the full list.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, *, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _env_bool("DJANGO_DEBUG")
# Empty is allowed at import time so tooling (mypy, collectstatic) can load the
# settings; Django refuses to serve a request or sign anything without a key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "ninja",
    "ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

DATABASES = {
    "default": dj_database_url.config(
        default="postgres://postgres:postgres@localhost:5432/ledger",
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    ),
}
# Postgres' default READ COMMITTED is kept on purpose: money moves under
# explicit row locks instead of serializable retries. See docs/adr/0002.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# --- Ledger ------------------------------------------------------------------

# Take a balance snapshot once an account has this many entries past its last
# one, so a balance read never sums more than this many rows.
LEDGER_SNAPSHOT_EVERY = int(os.environ.get("LEDGER_SNAPSHOT_EVERY", "500"))

# Where the outbox worker sends events: "log" or "redis" (a Redis stream).
LEDGER_OUTBOX_PUBLISHER = os.environ.get("LEDGER_OUTBOX_PUBLISHER", "log")
LEDGER_OUTBOX_STREAM = os.environ.get("LEDGER_OUTBOX_STREAM", "ledger.events")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
