"""Django settings. All environment-specific values come from env vars with dev-safe defaults."""

import os
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "drf_spectacular",
    "quakes",
    "ingestion",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aftershock.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "aftershock.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "NAME": os.environ.get("POSTGRES_DB", "aftershock"),
        "USER": os.environ.get("POSTGRES_USER", "aftershock"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "aftershock"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# runserver serves static files itself while DEBUG=True (dev-only setup, see README).
STATIC_URL = "static/"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Public, read-only data. Stated explicitly rather than relying on DRF's default.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "aftershock.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Aftershock API",
    "DESCRIPTION": "USGS earthquake events, ingested into PostGIS and served with filtering.",
    "VERSION": "1.0.0",
}

# Celery: at-least-once delivery. Tasks are acked only after they finish, so a worker
# dying mid-task causes redelivery; tasks must therefore be idempotent.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_IGNORE_RESULT = True
# Used only by the optional `beat` service (compose profile "scheduler"). If a run is still going
# when the next one is due, the ingest lock makes the second one exit without doing anything.
CELERY_BEAT_SCHEDULE = {
    "ingest-daily": {
        "task": "ingestion.tasks.ingest_task",
        "schedule": crontab(hour=3, minute=0),  # 03:00 UTC
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
