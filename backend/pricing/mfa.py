"""Server-side MFA session evidence shared by web, API, and JWT authentication."""
from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.utils.crypto import constant_time_compare, salted_hmac

MFA_USER_ID_KEY = "pricing_mfa_user_id"
MFA_VERIFIED_AT_KEY = "pricing_mfa_verified_at"
MFA_SESSION_BINDING_KEY = "pricing_mfa_session_binding"
MFA_DEVICE_UPDATED_AT_KEY = "pricing_mfa_device_updated_at"


def _binding(session_key: str) -> str:
    return salted_hmac("pricing.mfa.session.v1", session_key).hexdigest()


def mark_mfa_verified(session, user, device) -> None:
    """Persist MFA evidence only after a successful OTP or recovery-code check."""
    if not session.session_key:
        session.save()
    session[MFA_USER_ID_KEY] = str(user.pk)
    session[MFA_VERIFIED_AT_KEY] = int(time.time())
    session[MFA_SESSION_BINDING_KEY] = _binding(session.session_key)
    session[MFA_DEVICE_UPDATED_AT_KEY] = device.updated_at.isoformat()


def mfa_session_is_valid(session, user, *, session_key: str | None = None,
                         verified_at: int | None = None) -> bool:
    """Validate that this live authenticated session has current MFA evidence."""
    if not user or not user.is_authenticated:
        return False
    session_key = session_key or session.session_key
    if not session_key or session.get(SESSION_KEY) != str(user.pk):
        return False
    session_hash = session.get(HASH_SESSION_KEY, "")
    if not session_hash or not constant_time_compare(session_hash, user.get_session_auth_hash()):
        return False
    try:
        recorded_at = int(session.get(MFA_VERIFIED_AT_KEY))
    except (TypeError, ValueError):
        return False
    if verified_at is not None and recorded_at != verified_at:
        return False
    if recorded_at > time.time() or time.time() - recorded_at > settings.MFA_SESSION_AGE:
        return False
    binding = session.get(MFA_SESSION_BINDING_KEY, "")
    if not binding or not constant_time_compare(binding, _binding(session_key)):
        return False
    if session.get(MFA_USER_ID_KEY) != str(user.pk):
        return False
    from .models import AdminOTPDevice

    device = AdminOTPDevice.objects.filter(user=user, enabled=True).only("updated_at").first()
    return bool(device and session.get(MFA_DEVICE_UPDATED_AT_KEY) == device.updated_at.isoformat())


def mfa_claims_for_session(session, user) -> dict[str, int | str] | None:
    if not mfa_session_is_valid(session, user):
        return None
    return {"sid": session.session_key, "mfa_at": int(session[MFA_VERIFIED_AT_KEY])}


def token_mfa_session_is_valid(user, session_key: str, verified_at: int) -> bool:
    """Check a bearer token's MFA evidence against its still-live Django session."""
    if not isinstance(session_key, str) or not session_key or not isinstance(verified_at, int):
        return False
    engine = __import__(settings.SESSION_ENGINE, fromlist=["SessionStore"])
    session = engine.SessionStore(session_key=session_key)
    return mfa_session_is_valid(session, user, session_key=session_key, verified_at=verified_at)


def revoke_mfa_session(user, session_key: str, verified_at: int) -> bool:
    """Delete a backing session only when its live MFA evidence belongs to this user/token."""
    if not token_mfa_session_is_valid(user, session_key, verified_at):
        return False
    engine = __import__(settings.SESSION_ENGINE, fromlist=["SessionStore"])
    engine.SessionStore(session_key=session_key).delete()
    return True
