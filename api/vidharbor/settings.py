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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "ru"
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "vidharbor",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
CSRF_FAILURE_VIEW = "vidharbor.accounts.csrf_failure"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.environ.get("SECURE_COOKIES") == "1"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{validator}"}
    for validator in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

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

MEDIA_ROOT = Path("/var/lib/vidharbor")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8085").rstrip("/")
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "10800"))
if not 1 <= FFMPEG_TIMEOUT <= 10800:
    raise ValueError("FFMPEG_TIMEOUT must be between 1 second and 3 hours")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "5368709120"))
if not 1 <= MAX_UPLOAD_BYTES <= 5 * 1024**3:
    raise ValueError("MAX_UPLOAD_BYTES must be between 1 and 5368709120")
