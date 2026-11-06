"""Isolated settings for automated tests; operational records use SQLite."""
import os
from .settings import *  # noqa: F403

TEST_DB_PATH = os.environ.get("MESHKOR_TEST_DB_PATH")
if not TEST_DB_PATH:
    TEST_DB_PATH = str(PROJECT_DIR / "tmp_pytest" / "test_meshkor.sqlite3")

DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": TEST_DB_PATH,
    "OPTIONS": {
        "timeout": 30,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000; PRAGMA synchronous=NORMAL",
    },
    "TEST": {
        "NAME": TEST_DB_PATH,
    },
}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# API tests create temporary users repeatedly; avoid production-strength hashing cost here.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

from django.db.backends.signals import connection_created
from django.dispatch import receiver

@receiver(connection_created)
def _configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")


