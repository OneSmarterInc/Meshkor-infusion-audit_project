"""Django settings for the Infusion Drug Markup Audit API.

Price data lives in Parquet files built by the pipeline (data/mart, data/ref,
data/hpt/norm) and is read through DuckDB. SQLite stores users, roles, jobs,
reports, reviews, and audit events; bulk rates stay in Parquet.
"""
import os
import secrets
import sys
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_DIR = BASE_DIR.parent                              # infusion-audit/
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))                   # import pipeline.common

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is disabled.")
    SECRET_KEY = secrets.token_urlsafe(48)
ALLOWED_HOSTS = [host.strip() for host in
                 os.environ.get("DJANGO_ALLOWED_HOSTS",
                                "localhost,127.0.0.1,testserver" if DEBUG else "").split(",")
                 if host.strip()]
if not DEBUG:
    if len(SECRET_KEY) < 50 or SECRET_KEY.strip().casefold() in {
            "replace-with-a-random-secret-before-deployment", "django-insecure-secret-key"}:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a unique random value of at least 50 characters.")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to the exact production host names.")

INSTALLED_APPS = [
    "pricing.admin_config.MFAAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "pricing",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "auditapi.urls"
WSGI_APPLICATION = "auditapi.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DEFAULT_OPERATIONAL_DB_PATH = PROJECT_DIR / "data" / "operational" / "meshkor.sqlite3"
OPERATIONAL_DB_PATH = Path(os.environ.get("OPERATIONAL_DB_PATH", str(DEFAULT_OPERATIONAL_DB_PATH)))
OPERATIONAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if (os.name != "nt"
        and OPERATIONAL_DB_PATH.parent.resolve() == DEFAULT_OPERATIONAL_DB_PATH.parent.resolve()):
    # Keep SQLite, WAL, and SHM files private to the service account.
    OPERATIONAL_DB_PATH.parent.chmod(0o700)
    if OPERATIONAL_DB_PATH.exists():
        OPERATIONAL_DB_PATH.chmod(0o600)
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": str(OPERATIONAL_DB_PATH),
    "OPTIONS": {"timeout": 30},
}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "pricing.jwt_auth.AccessTokenAuthentication",
        "pricing.jwt_auth.MFASessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["pricing.permissions.CanView"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

SESSION_COOKIE_AGE = 8 * 60 * 60          # 8 hours
MFA_SESSION_AGE = SESSION_COOKIE_AGE
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = os.environ.get(
    "DJANGO_SECURE_SSL_REDIRECT", "0" if DEBUG else "1"
) == "1"
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG and SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
JWT_ACCESS_TOKEN_SECONDS = int(os.environ.get("JWT_ACCESS_TOKEN_SECONDS", "900"))
TOTP_ENCRYPTION_KEY = os.environ.get("TOTP_ENCRYPTION_KEY", "")
if not DEBUG:
    if not TOTP_ENCRYPTION_KEY:
        raise ImproperlyConfigured("Set TOTP_ENCRYPTION_KEY before running in production.")
    try:
        from cryptography.fernet import Fernet
        Fernet(TOTP_ENCRYPTION_KEY.encode("ascii"))
    except (ImportError, ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("TOTP_ENCRYPTION_KEY must be a valid Fernet key.") from exc
TOTP_PAST_WINDOW_STEPS = int(os.environ.get("TOTP_PAST_WINDOW_STEPS", "0"))
TOTP_FUTURE_WINDOW_STEPS = int(os.environ.get("TOTP_FUTURE_WINDOW_STEPS", "0"))
if TOTP_PAST_WINDOW_STEPS < 0 or TOTP_FUTURE_WINDOW_STEPS < 0:
    raise ImproperlyConfigured("TOTP verification windows must be non-negative integers.")

# Login attempts: this many failures per username + address in the window -> blocked.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# The Vite dev server is allowed only in debug mode unless production origins
# are explicitly configured.
dev_origins = ["http://localhost:5173", "http://127.0.0.1:5173",
               "http://localhost:5174", "http://127.0.0.1:5174",
               "http://localhost:5175", "http://127.0.0.1:5175",
               "http://localhost:5176", "http://127.0.0.1:5176"]
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get(
    "DJANGO_CORS_ALLOWED_ORIGINS", ",".join(dev_origins) if DEBUG else ""
).split(",") if origin.strip()]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get(
    "DJANGO_CSRF_TRUSTED_ORIGINS", ",".join(dev_origins) if DEBUG else ""
).split(",") if origin.strip()]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
