import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "local-only-insecure-key-do-not-deploy"
    else:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a unique, random secret in production.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,healthcheck.railway.app")
railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
railway_environment = os.getenv("RAILWAY_ENVIRONMENT", "").strip()
on_railway = bool(railway_domain or railway_environment or os.getenv("RAILWAY_PROJECT_ID", "").strip())
if railway_domain:
    ALLOWED_HOSTS.append(railway_domain)
# Preserve order while removing duplicates.
ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt", "rest_framework_simplejwt.token_blacklist",
    "django_filters", "corsheaders",
    "core", "patients", "laboratory", "billing", "radiology", "integrations",
    "referrals", "communications", "workflows", "portals", "inventory", "reporting",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.MustChangePasswordMiddleware",
    "core.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context.workspace_access",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
        ssl_require=not DEBUG and os.getenv("DB_SSL_REQUIRE", "false").lower() == "true",
    )}
elif DEBUG:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    raise ImproperlyConfigured("DATABASE_URL is required outside local DEBUG mode. Attach Railway PostgreSQL.")

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["core.auth_backend.LockoutBackend"]
LOGIN_FAILURE_LIMIT = int(os.getenv("LOGIN_FAILURE_LIMIT", "3"))
LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip():
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["AWS_STORAGE_BUCKET_NAME"].strip(),
            "region_name": os.getenv("AWS_S3_REGION_NAME", "").strip(),
            "querystring_auth": True,
            "default_acl": "private",
            "file_overwrite": False,
        },
    }
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media"))).resolve()
# When using a Railway volume (not S3), set SERVE_MEDIA=True so authenticated
# Django views can still resolve /media/ URLs. Prefer private S3 for multi-replica.
SERVE_MEDIA = env_bool("SERVE_MEDIA", DEBUG) and not bool(os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip())
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024)))

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(8 * 60 * 60)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", True)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

csrf_origins = env_list("CSRF_TRUSTED_ORIGINS")
if railway_domain:
    csrf_origins.append(f"https://{railway_domain}")
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(csrf_origins))
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
# Railway terminates TLS at the edge and forwards client IP; trust XFF only
# when we know we are behind that controlled proxy (or an explicit override).
AUDIT_TRUST_X_FORWARDED_FOR = env_bool("AUDIT_TRUST_X_FORWARDED_FOR", on_railway)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/minute", "user": "180/minute"},
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "10"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "1"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "results@hfdn.local")
SMS_PROVIDER_URL = os.getenv("SMS_PROVIDER_URL", "").strip()
SMS_PROVIDER_TOKEN = os.getenv("SMS_PROVIDER_TOKEN", "").strip()
WHATSAPP_PROVIDER_URL = os.getenv("WHATSAPP_PROVIDER_URL", "").strip()
WHATSAPP_PROVIDER_TOKEN = os.getenv("WHATSAPP_PROVIDER_TOKEN", "").strip()
NOTIFICATION_LOG_PROVIDER = env_bool("NOTIFICATION_LOG_PROVIDER", DEBUG)
CRITICAL_ALERT_EMAIL = os.getenv("CRITICAL_ALERT_EMAIL", "").strip()
PATIENT_PORTAL_URL = os.getenv("PATIENT_PORTAL_URL", "https://www.example.com/patients").strip()
_default_public_url = f"https://{railway_domain}" if railway_domain else "http://127.0.0.1:8000"
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", _default_public_url).strip()
ORTHANC_URL = os.getenv("ORTHANC_URL", "").strip()
ORTHANC_USERNAME = os.getenv("ORTHANC_USERNAME", "").strip()
ORTHANC_PASSWORD = os.getenv("ORTHANC_PASSWORD", "").strip()
RIS_MWL_AE_TITLE = os.getenv("RIS_MWL_AE_TITLE", "HFDN_MWL").strip()[:16] or "HFDN_MWL"
RIS_MWL_PORT = int(os.getenv("RIS_MWL_PORT", "11112"))
RIS_CALLING_AE = os.getenv("RIS_CALLING_AE", "HFDN_SCU").strip()[:16] or "HFDN_SCU"
RIS_MWL_ENABLED = env_bool("RIS_MWL_ENABLED", False)
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "").strip()
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL).strip()
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", not bool(CELERY_BROKER_URL))
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    "loggers": {"django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
