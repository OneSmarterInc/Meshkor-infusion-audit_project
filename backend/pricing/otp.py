"""Small RFC 6238 TOTP implementation for authenticator apps."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_ALGORITHM = hashlib.sha1
TOTP_ALGORITHM_NAME = "SHA1"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def code_at(secret: str, timestamp: int | float | None = None) -> tuple[str, int]:
    timestamp = time.time() if timestamp is None else timestamp
    step = int(timestamp // TOTP_PERIOD_SECONDS)
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), TOTP_ALGORITHM).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 10 ** TOTP_DIGITS:0{TOTP_DIGITS}d}", step


def verify_code(secret: str, supplied: str, timestamp: int | float | None = None) -> int | None:
    value = str(supplied or "").strip()
    if len(value) != TOTP_DIGITS or not value.isdigit():
        return None
    now = time.time() if timestamp is None else timestamp
    past_window = settings.TOTP_PAST_WINDOW_STEPS
    future_window = settings.TOTP_FUTURE_WINDOW_STEPS
    if (not isinstance(past_window, int) or isinstance(past_window, bool) or past_window < 0
            or not isinstance(future_window, int) or isinstance(future_window, bool)
            or future_window < 0):
        raise ImproperlyConfigured("TOTP verification windows must be non-negative integers.")
    current_step = int(now // TOTP_PERIOD_SECONDS)
    for step in range(current_step - past_window, current_step + future_window + 1):
        expected, _ = code_at(secret, step * TOTP_PERIOD_SECONDS)
        if hmac.compare_digest(expected, value):
            return step
    return None


def provisioning_uri(secret: str, username: str, issuer: str = "Infusion Audit") -> str:
    label = quote(f"{issuer}:{username}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm={TOTP_ALGORITHM_NAME}&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}")


def encrypt_secret(secret: str) -> str:
    key = getattr(settings, "TOTP_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("Set TOTP_ENCRYPTION_KEY before enabling authenticator OTP.")
    try:
        return Fernet(key.encode("ascii")).encrypt(secret.encode("ascii")).decode("ascii")
    except (ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("TOTP_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def decrypt_secret(stored: str) -> str:
    """Decrypt current records and allow legacy Base32 seeds to be re-encrypted."""
    if not stored.startswith("gAAAA"):
        return stored
    key = getattr(settings, "TOTP_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("Set the original TOTP_ENCRYPTION_KEY to verify authenticator codes.")
    try:
        return Fernet(key.encode("ascii")).decrypt(stored.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise ImproperlyConfigured("Unable to decrypt the saved authenticator seed; check TOTP_ENCRYPTION_KEY.") from exc
