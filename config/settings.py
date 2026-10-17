"""
Django settings for the health tracker API.

Every environment-specific value is read from the environment (or a local
`.env` file), so switching from SQLite to PostgreSQL only means changing
DATABASE_URL.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# Core

APP_NAME = "health-tracker-api"
APP_VERSION = "0.1.0"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "tracker",
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

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"


# Database
# SQLite by default. For PostgreSQL install `psycopg[binary]` and set e.g.
# DATABASE_URL=postgres://user:password@localhost:5432/health_tracker

DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
    )
}


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "America/La_Paz")
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST framework

REST_FRAMEWORK = {
    # MVP: open API for local development. Add authentication before deploying.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# OpenAPI docs (drf-spectacular): /api/schema/, /api/docs/ (Swagger UI), /api/redoc/

SPECTACULAR_SETTINGS = {
    "TITLE": "Health Tracker API",
    "DESCRIPTION": (
        "Turns free-text notes about habits, symptoms and states into structured records "
        "using Gemma. When a note is vague, the API returns follow-up questions; answer them "
        "with `POST /api/entries/{id}/answer/`."
    ),
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,  # hide the schema endpoint from the docs themselves
    "SCHEMA_PATH_PREFIX": r"/api/",  # group endpoints by resource: concepts, entries
    "COMPONENT_SPLIT_REQUEST": True,  # separate request/response models (read-only fields)
    "SWAGGER_UI_SETTINGS": {"tryItOutEnabled": True, "displayRequestDuration": True},
}


# LLM
# LLM_PROVIDER=fake works offline (tests, no API key); LLM_PROVIDER=gemma calls
# the Gemini API. GEMINI_API_KEY comes from Google AI Studio.

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "fake")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# minimal | high (the only levels Gemma accepts). Empty uses the model default.
LLM_THINKING_LEVEL = os.getenv("LLM_THINKING_LEVEL", "")
