"""
NAPSAM - Django ayarlari.

Tasarim kisitlari (napsam-app-prompt.md):
  - Uygulama anonim calisir; ilk acilista uyelik/izin YOK.
  - Konum saklanmaz (Bolum 4 + Bolum 15).
  - Butce kademeleri koda hardcode EDILMEZ; veritabanindan okunur (Bolum 7.2).
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Lokal gelistirmede .env / .env.local okunur. Vercel'de ortam degiskenleri
# zaten process'e enjekte edildigi icin bu cagrilar sessizce gecilir.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=True)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# Cekirdek
# --------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-local-development-key-do-not-deploy"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY tanimli degil. Uretim ortaminda calistirmadan once "
            "ortam degiskeni olarak ayarla."
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS") or ["localhost", "127.0.0.1"]

# Vercel her deployment icin ayri bir host adi uretir; onu otomatik ekle.
if vercel_url := os.environ.get("VERCEL_URL"):
    ALLOWED_HOSTS.append(vercel_url)
if vercel_branch_url := os.environ.get("VERCEL_BRANCH_URL"):
    ALLOWED_HOSTS.append(vercel_branch_url)
if vercel_project_url := os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"):
    ALLOWED_HOSTS.append(vercel_project_url)
if os.environ.get("VERCEL"):
    ALLOWED_HOSTS.append(".vercel.app")

CSRF_TRUSTED_ORIGINS = [
    f"https://{host.lstrip('.')}" for host in ALLOWED_HOSTS if host not in {"localhost", "127.0.0.1"}
]
if os.environ.get("VERCEL"):
    CSRF_TRUSTED_ORIGINS.append("https://*.vercel.app")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
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
    "core.middleware.AnonIdentityMiddleware",
]

ROOT_URLCONF = "napsam.urls"

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

WSGI_APPLICATION = "napsam.wsgi.application"

# --------------------------------------------------------------------------
# Veritabani
# --------------------------------------------------------------------------
# DATABASE_URL varsa Supabase Postgres, yoksa lokal SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    # Baglanti kurmak pahali: olculen degerler (Turkiye -> Frankfurt havuzu)
    # yeni baglanti + sorgu ~579 ms, acik baglantida ayni sorgu ~69 ms.
    # pgbouncer'in transaction modunda coğullanan sey SUNUCU baglantilaridir;
    # istemcinin baglantiyi acik tutmasi beklenen kullanimdir. Bu yuzden
    # kalici baglanti aciktir ve bayat baglantilara karsi saglik denetimi var.
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get("DJANGO_CONN_MAX_AGE", "600")),
            conn_health_checks=True,
            ssl_require=True,
        )
    }
    # Transaction modunda GEREKLI olan iki ayar: psycopg3 bes calistirmadan
    # sonra prepared statement'a gecer ve sunucu tarafi imlecler oturuma
    # bagimlidir; ikisi de havuzda kirilir.
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"]["prepare_threshold"] = None
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Yerellestirme
# --------------------------------------------------------------------------
LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Statik dosyalar
# --------------------------------------------------------------------------
# Vercel, STATIC_ROOT tanimliysa collectstatic'i build sirasinda kendisi
# calistirir ve dosyalari CDN'den sunar. WhiteNoise lokalde devrededir.
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# --------------------------------------------------------------------------
# Onbellek
# --------------------------------------------------------------------------
# Surec ici onbellek. Sunucusuz ortamda her ornek kendi kopyasini tutar;
# paylasimli bir Redis'e gerek yok cunku onbellekteki her sey yavas degisen
# ve kullaniciya ozel OLMAYAN yapilandirma (asgari ucret, butce kademeleri,
# haftalik tema). Kullanici verisi onbelleklenmez.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "napsam-config",
        "TIMEOUT": 300,
    }
}

# --------------------------------------------------------------------------
# Guvenlik
# --------------------------------------------------------------------------
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# --------------------------------------------------------------------------
# NAPSAM'a ozel ayarlar
# --------------------------------------------------------------------------
# Anonim kimlik cerezi - kisisel veri tasimaz, sadece rastgele bir UUID.
NAPSAM_ANON_COOKIE = "napsam_anon"
NAPSAM_ANON_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 yil

# Reddedilen oneri kac gun tekrar gosterilmesin (Bolum 9).
NAPSAM_DISMISS_COOLDOWN_DAYS = 30

# AI katmani (Faz 4). Anahtar yoksa uygulama tamamen seed havuzuyla calisir.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
NAPSAM_AI_DAILY_LIMIT = int(os.environ.get("NAPSAM_AI_DAILY_LIMIT", "5"))
NAPSAM_AI_CACHE_TTL_DAYS = 7
