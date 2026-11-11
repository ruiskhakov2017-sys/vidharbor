import os
from pathlib import Path


def read_secret(filename: str) -> str:
    value = (Path("/run/secrets") / filename).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Empty secret file: {filename}")
    return value


SECRET_KEY = read_secret("django_secret_key")
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
ROOT_URLCONF = "vidharbor.urls"
WSGI_APPLICATION = "vidharbor.wsgi.application"

INSTALLED_APPS = ["rest_framework"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": read_secret("postgres_password"),
        "HOST": "db",
        "PORT": 5432,
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 2},
    }
}

RABBITMQ_HOST = "rabbitmq"
RABBITMQ_USER = os.environ["RABBITMQ_USER"]
RABBITMQ_PASSWORD = read_secret("rabbitmq_password")
