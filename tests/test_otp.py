import time
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.test import Client, override_settings

from pricing.mfa import MFA_USER_ID_KEY, MFA_VERIFIED_AT_KEY, mark_mfa_verified
from pricing.models import AdminOTPDevice
from pricing.otp import code_at, encrypt_secret, new_secret, provisioning_uri, verify_code


def _enroll(client, user):
    password_response = client.post("/api/v1/auth/login/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json")
    assert password_response.status_code == 200
    challenge = password_response.json()["challenge"]
    setup = client.post("/api/v1/auth/otp/setup/", {"challenge": challenge}, content_type="application/json")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    confirmed = client.post("/api/v1/auth/otp/confirm/", {
        "challenge": challenge, "code": code_at(secret)[0],
    }, content_type="application/json")
    assert confirmed.status_code == 200
    return secret, confirmed.json()["recovery_codes"]


def test_totp_code_window_and_provisioning_uri():
    secret = "JBSWY3DPEHPK3PXP"
    now = 1_800_000_000
    code, step = code_at(secret, timestamp=now)
    assert len(code) == 6 and verify_code(secret, code, timestamp=now) == step
    assert verify_code(secret, code_at(secret, now - 30)[0], timestamp=now) is None
    assert verify_code(secret, code_at(secret, now + 30)[0], timestamp=now) is None
    assert verify_code(secret, "abc123", timestamp=now) is None
    assert provisioning_uri(secret, "audit-admin").startswith("otpauth://totp/")
    assert "algorithm=SHA1&digits=6&period=30" in provisioning_uri(secret, "audit-admin")


def test_totp_window_is_configurable_but_strict_by_default():
    secret = "JBSWY3DPEHPK3PXP"
    now = 1_800_000_000
    previous_code = code_at(secret, now - 30)[0]
    next_code = code_at(secret, now + 30)[0]
    with override_settings(TOTP_PAST_WINDOW_STEPS=1, TOTP_FUTURE_WINDOW_STEPS=0):
        assert verify_code(secret, previous_code, timestamp=now) is not None
        assert verify_code(secret, next_code, timestamp=now) is None
    with override_settings(TOTP_PAST_WINDOW_STEPS=0, TOTP_FUTURE_WINDOW_STEPS=1):
        assert verify_code(secret, previous_code, timestamp=now) is None
        assert verify_code(secret, next_code, timestamp=now) is not None


@pytest.mark.django_db
def test_expired_sign_in_challenge_is_rejected():
    now = time.time()
    with patch("django.core.signing.time.time", return_value=now - 301):
        challenge = signing.dumps({"user_id": 1, "purpose": "login"}, salt="user-otp-login")
    response = Client().post("/api/v1/authentication/totp/sign-in-verification/", {
        "challenge": challenge, "code": "123456",
    }, content_type="application/json")
    assert response.status_code == 401
    assert response.json()["code"] == "challenge_expired"
    assert "challenge expired" in response.json()["error"]


@override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc=")
@pytest.mark.django_db
def test_canonical_password_totp_and_session_refresh_flow():
    user = get_user_model().objects.create_user(
        username="canonical-mfa-user", password="test-only-password", is_active=True,
    )
    user.groups.add(Group.objects.get_or_create(name="ANALYST")[0])
    secret = new_secret()
    AdminOTPDevice.objects.create(
        user=user, secret=encrypt_secret(secret), enabled=True, last_used_step=-1,
    )
    client = Client(enforce_csrf_checks=True)
    csrf = client.get("/api/v1/authentication/csrf-token/").cookies["csrftoken"].value
    signed_in = client.post("/api/v1/authentication/sign-in/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert signed_in.status_code == 200
    assert signed_in.json()["otp_required"] is True

    csrf = client.cookies["csrftoken"].value
    now = 1_800_000_000
    current_code = code_at(secret, now)[0]
    with patch("pricing.auth_views.verify_code",
               side_effect=lambda seed, value: verify_code(seed, value, timestamp=now)):
        verified = client.post("/api/v1/authentication/totp/sign-in-verification/", {
            "challenge": signed_in.json()["challenge"], "code": current_code,
        }, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        csrf = client.cookies["csrftoken"].value
        replayed = client.post("/api/v1/authentication/totp/sign-in-verification/", {
            "challenge": signed_in.json()["challenge"], "code": current_code,
        }, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert verified.status_code == 200, verified.content
    assert verified.json()["access"]
    assert replayed.status_code == 401

    csrf = client.cookies["csrftoken"].value
    refreshed = client.post("/api/v1/authentication/access-token/refresh/", "{}",
                            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert refreshed.status_code == 200, refreshed.content
    assert refreshed.json()["access"]


@override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc=")
def test_admin_authenticator_is_required_after_password_and_code_cannot_replay(db, built):
    user = get_user_model().objects.create_superuser(
        username="otp-test-admin", email="otp-test@example.invalid", password="test-only-password"
    )
    client = Client()
    secret, recovery_codes = _enroll(client, user)
    assert len(recovery_codes) == 10
    assert client.get("/api/v1/auth/otp/").json()["enabled"] is True

    assert AdminOTPDevice.objects.get(user=user).secret.startswith("gAAAA")
    fresh_client = Client()
    password_response = fresh_client.post("/api/v1/auth/login/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json")
    assert password_response.status_code == 200
    assert password_response.json()["otp_required"] is True

    challenge = password_response.json()["challenge"]
    verified = fresh_client.post("/api/v1/auth/otp/complete/", {
        "challenge": challenge, "recovery_code": recovery_codes[0],
    }, content_type="application/json")
    assert verified.status_code == 200
    assert verified.json()["access"]
    replay = fresh_client.post("/api/v1/auth/otp/complete/", {
        "challenge": challenge, "recovery_code": recovery_codes[0],
    }, content_type="application/json")
    assert replay.status_code == 401

    recovery_client = Client()
    recovery_challenge = recovery_client.post("/api/v1/auth/login/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json").json()["challenge"]
    recovered = recovery_client.post("/api/v1/auth/otp/complete/", {
        "challenge": recovery_challenge, "recovery_code": recovery_codes[1],
    }, content_type="application/json")
    assert recovered.status_code == 200
    duplicate_recovery = Client().post("/api/v1/auth/otp/complete/", {
        "challenge": recovery_challenge, "recovery_code": recovery_codes[1],
    }, content_type="application/json")
    assert duplicate_recovery.status_code == 401


@override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc=")
def test_mfa_boundary_blocks_admin_sessions_and_legacy_tokens(db, built):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="mfa-boundary-admin", email="mfa-boundary@example.invalid", password="test-only-password"
    )
    password_only = Client()
    assert password_only.post("/admin/login/", {
        "username": user.username, "password": "test-only-password",
    }).status_code == 403
    password_only.force_login(user)  # Simulates a legacy/password-only Django session.
    assert password_only.get("/api/v1/user-accounts/").status_code == 401
    assert password_only.post("/api/v1/auth/refresh/").status_code == 401
    admin_redirect = password_only.get("/admin/")
    assert admin_redirect.status_code == 302
    assert password_only.get(admin_redirect["Location"]).status_code == 403

    verified = Client()
    secret, recovery_codes = _enroll(verified, user)
    challenge = verified.post("/api/v1/auth/login/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json").json()["challenge"]
    complete = verified.post("/api/v1/auth/otp/complete/", {
        "challenge": challenge, "recovery_code": recovery_codes[1],
    }, content_type="application/json")
    assert complete.status_code == 200
    access = complete.json()["access"]
    assert verified.get("/api/v1/user-accounts/").status_code == 200
    assert verified.get("/admin/").status_code == 200
    assert verified.post("/api/v1/auth/refresh/").status_code == 200

    legacy = jwt.encode({"sub": str(user.pk), "iat": time.time(), "exp": time.time() + 60,
                         "type": "access"}, settings.SECRET_KEY, algorithm="HS256")
    assert Client().get("/api/v1/user-accounts/", HTTP_AUTHORIZATION=f"Bearer {legacy}").status_code == 401

    session = verified.session
    session[MFA_VERIFIED_AT_KEY] = int(time.time()) - 8 * 60 * 60 - 1
    session.save()
    assert verified.get("/api/v1/user-accounts/").status_code == 401
    assert verified.post("/api/v1/auth/refresh/").status_code == 401

    verified = Client()
    recovery_challenge = verified.post("/api/v1/auth/login/", {
        "username": user.username, "password": "test-only-password",
    }, content_type="application/json").json()["challenge"]
    renewed = verified.post("/api/v1/auth/otp/complete/", {
        "challenge": recovery_challenge, "recovery_code": recovery_codes[0],
    }, content_type="application/json")
    assert renewed.status_code == 200
    access = renewed.json()["access"]
    assert verified.post("/api/v1/auth/logout/").status_code == 200
    assert Client().get("/api/v1/user-accounts/", HTTP_AUTHORIZATION=f"Bearer {access}").status_code == 401


@override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc=")
def test_bearer_logout_revokes_only_its_verified_session(db, built):
    User = get_user_model()
    users = [User.objects.create_superuser(
        username=f"logout-owner-{index}", email=f"logout-{index}@example.invalid",
        password="test-only-password",
    ) for index in (1, 2)]
    clients, tokens = [], []
    for user in users:
        browser = Client()
        _enroll(browser, user)
        token_response = browser.post("/api/v1/auth/refresh/")
        assert token_response.status_code == 200
        clients.append(browser)
        tokens.append(token_response.json()["access"])

    assert Client().post("/api/v1/auth/logout/").status_code == 401
    assert Client().post("/api/v1/auth/logout/", HTTP_AUTHORIZATION="Bearer invalid").status_code == 401

    # A's bearer token must not flush B's unrelated cookie session.
    mixed = Client()
    mixed.cookies[settings.SESSION_COOKIE_NAME] = clients[1].cookies[settings.SESSION_COOKIE_NAME].value
    revoked = mixed.post("/api/v1/auth/logout/", HTTP_AUTHORIZATION=f"Bearer {tokens[0]}")
    assert revoked.status_code == 200, revoked.content
    assert Client().get("/api/v1/user-accounts/", HTTP_AUTHORIZATION=f"Bearer {tokens[0]}").status_code == 401
    assert Client().get("/api/v1/user-accounts/", HTTP_AUTHORIZATION=f"Bearer {tokens[1]}").status_code == 200
    assert clients[1].get("/api/v1/user-accounts/").status_code == 200

    # Cookie-backed logout still revokes the session as well as its issued JWT.
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.cookies[settings.SESSION_COOKIE_NAME] = clients[1].cookies[settings.SESSION_COOKIE_NAME].value
    csrf_client.get("/api/v1/authentication/csrf-token/")
    assert csrf_client.post("/api/v1/auth/logout/").status_code == 403
    csrf_token = csrf_client.cookies[settings.CSRF_COOKIE_NAME].value
    cookie_logout = csrf_client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=csrf_token)
    assert cookie_logout.status_code == 200
    assert Client().get("/api/v1/user-accounts/", HTTP_AUTHORIZATION=f"Bearer {tokens[1]}").status_code == 401


@override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc=")
def test_mfa_session_evidence_is_bound_to_the_authenticated_user_and_admin_staff_status(db, built):
    User = get_user_model()
    staff_user = User.objects.create_superuser(
        username="mfa-staff", email="mfa-staff@example.invalid", password="test-only-password"
    )
    client = Client()
    _enroll(client, staff_user)
    session = client.session
    session[MFA_USER_ID_KEY] = str(staff_user.pk + 1)
    session.save()
    assert client.get("/api/v1/user-accounts/").status_code == 401

    non_staff = User.objects.create_user(
        username="mfa-non-staff", password="test-only-password", is_active=True
    )
    non_staff.groups.add(Group.objects.create(name="ANALYST"))
    device = AdminOTPDevice.objects.create(user=non_staff, secret="test", enabled=True)
    non_staff_client = Client()
    non_staff_client.force_login(non_staff)
    session = non_staff_client.session
    mark_mfa_verified(session, non_staff, device)
    session.save()
    assert non_staff_client.get("/api/v1/sources/").status_code == 200
    assert non_staff_client.get("/admin/").status_code == 302
