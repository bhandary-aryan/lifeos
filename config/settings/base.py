"""Shared settings. Environment-specific values live in dev.py / prod.py."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Load .env if present (never committed; see .env.example).
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Product name is configurable so the app can be renamed later.
SITE_NAME = env("LIFEOS_SITE_NAME", default="LifeOS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.goals",
    "apps.planner",
    "apps.finance",
    "apps.future_plans",
    "apps.interests",
    "apps.reflections",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.UserTimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
                "apps.core.context_processors.site",
            ],
        },
    },
]

# PostgreSQL only. Example: postgres://user:pass@localhost:5432/lifeos
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Used for login throttling. The default is per-process memory; in production with several workers set
# CACHE_URL to a shared cache (e.g. redis://host:6379/1 or memcache://...) so limits apply across them.
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://lifeos")}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-gb"
# Datetimes are stored in UTC and shown in each user's own timezone.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded images live outside static files and are NEVER served publicly: they go through an
# authenticated, owner-checked view. Point MEDIA_ROOT at persistent storage in production.
MEDIA_ROOT = env("LIFEOS_MEDIA_ROOT", default=str(BASE_DIR / "media"))
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14

PASSWORD_RESET_TIMEOUT = 60 * 60 * 3  # reset links valid for 3 hours

# Only enable behind a proxy that OVERWRITES X-Forwarded-For (see apps/accounts/throttle.py).
THROTTLE_TRUST_X_FORWARDED_FOR = env.bool("LIFEOS_TRUST_X_FORWARDED_FOR", default=False)

# Never log request bodies or credentials.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
