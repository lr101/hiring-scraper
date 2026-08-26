import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-only")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    host for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "jobs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
                "jobs.context_processors.workspace_users",
            ]
        },
    }
]

WORKSPACE_USER_COOKIE = "workspace_user"

if database_url := os.getenv("DATABASE_URL"):
    from urllib.parse import urlparse

    parsed = urlparse(database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
COLLECTION_STALE_RUN_SECONDS = int(os.getenv("COLLECTION_STALE_RUN_SECONDS", "43200"))
COMPANY_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("COMPANY_DISCOVERY_TIMEOUT_SECONDS", "15"))
COMPANY_DISCOVERY_USER_AGENT = os.getenv(
    "COMPANY_DISCOVERY_USER_AGENT", "hiring-scraper/0.1 (self-hosted company discovery)"
)
LOCATION_API_URL = os.getenv("LOCATION_API_URL", "https://nominatim.openstreetmap.org/search")
LOCATION_LOOKUP_TIMEOUT_SECONDS = float(os.getenv("LOCATION_LOOKUP_TIMEOUT_SECONDS", "10"))
LOCATION_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("LOCATION_MIN_REQUEST_INTERVAL_SECONDS", "1")
)
LOCATION_RATE_LIMIT_STATE_PATH = os.getenv(
    "LOCATION_RATE_LIMIT_STATE_PATH", "/tmp/hiring-scraper-location-rate-limit"
)
LOCATION_USER_AGENT = os.getenv(
    "LOCATION_USER_AGENT", "hiring-scraper/0.1 (self-hosted location lookup)"
)
CELERY_BEAT_SCHEDULE = {
    "collect-jobs-daily": {
        "task": "jobs.tasks.collect_all_sources",
        "schedule": 86400.0,
    }
}
