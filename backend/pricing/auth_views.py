import hashlib
import secrets
from datetime import datetime, timedelta, timezone as datetime_timezone

import jwt

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, password_validation
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .mfa import (mark_mfa_verified, mfa_claims_for_session,
                  mfa_session_is_valid, revoke_mfa_session)
from .models import (AdminOTPDevice, AuthChallenge, AuthThrottleBucket, AuditEvent,
                     DrugUnitReview, ReportRecord)
from .otp import decrypt_secret, encrypt_secret, new_secret, provisioning_uri, verify_code
from .permissions import (ADMIN_GROUP, VIEWER_GROUP, APPLICATION_ROLES, SUPER_ADMIN_GROUP,
                         CLIENT_GROUP, REVIEWER_GROUP, CanAdminister, CanReview, has_any_role)


def _role_names(user):
    if user.is_superuser:
        return [SUPER_ADMIN_GROUP]
    return sorted(user.groups.filter(name__in=(*APPLICATION_ROLES, ADMIN_GROUP, VIEWER_GROUP))
                  .values_list("name", flat=True))


def _user_payload(user):
    return {
        "id": user.pk,
        "username": user.get_username(),
        "display_name": user.get_full_name() or user.get_username(),
        "email": user.email,
        "roles": _role_names(user),
    }


def _access_token(request, user):
    mfa_claims = mfa_claims_for_session(request.session, user)
    if not mfa_claims:
        raise PermissionDenied("Multi-factor authentication is required.")
    now = datetime.now(datetime_timezone.utc)
    return jwt.encode({
        "sub": str(user.pk),
        "iat": now,
        "exp": now + timedelta(seconds=settings.JWT_ACCESS_TOKEN_SECONDS),
        "type": "access",
        **mfa_claims,
    }, settings.SECRET_KEY, algorithm="HS256")


def _auth_throttle_keys(user_id, ip, scope):
    return sorted({
        hashlib.sha256(f"{scope}|account|{user_id}".encode()).hexdigest(),
        hashlib.sha256(f"{scope}|source|{ip}".encode()).hexdigest(),
    })


def _locked_throttle_buckets(user_id, ip, scope, now):
    buckets = []
    for key in _auth_throttle_keys(user_id, ip, scope):
        AuthThrottleBucket.objects.get_or_create(key=key)
        bucket = AuthThrottleBucket.objects.select_for_update().get(key=key)
        if (bucket.window_started_at is None
                or (now - bucket.window_started_at).total_seconds() >= settings.LOGIN_WINDOW_SECONDS):
            bucket.failure_count = 0
            bucket.window_started_at = now
            bucket.save(update_fields=["failure_count", "window_started_at", "updated_at"])
        buckets.append(bucket)
    return buckets


def _reserve_auth_attempt(user_id, ip, scope):
    now = timezone.now()
    with transaction.atomic():
        AuthThrottleBucket.objects.filter(updated_at__lt=now - timedelta(days=30)).delete()
        buckets = _locked_throttle_buckets(user_id, ip, scope, now)
        retries = [max(1, settings.LOGIN_WINDOW_SECONDS - int((now - row.window_started_at).total_seconds()))
                   for row in buckets if row.failure_count >= min(settings.LOGIN_MAX_FAILURES, 5)]
        retry_after = max(retries, default=0)
        if not retry_after:
            for bucket in buckets:
                bucket.failure_count += 1
                bucket.save(update_fields=["failure_count", "updated_at"])
        return retry_after


def _release_auth_attempt(user_id, ip, scope):
    """Remove this successful request without erasing previous failures."""
    with transaction.atomic():
        buckets = (AuthThrottleBucket.objects.select_for_update()
                   .filter(key__in=_auth_throttle_keys(user_id, ip, scope)))
        for bucket in buckets:
            if bucket.failure_count:
                bucket.failure_count -= 1
                if not bucket.failure_count:
                    bucket.window_started_at = None
                bucket.save(update_fields=["failure_count", "window_started_at", "updated_at"])


def _issue_challenge(user, purpose, lifetime_seconds):
    """Issue a signed challenge whose database row enforces one-time use."""
    now = timezone.now()
    AuthChallenge.objects.filter(
        Q(expires_at__lte=now) | Q(consumed_at__lte=now - timedelta(days=1))
    ).delete()
    token = signing.dumps({"user_id": user.pk, "purpose": purpose,
                           "nonce": secrets.token_urlsafe(24),
                           "auth_state": user.get_session_auth_hash()}, salt="user-otp-login")
    AuthChallenge.objects.create(
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        user=user, purpose=purpose,
        expires_at=now + timedelta(seconds=lifetime_seconds),
    )
    return token


def _active_challenge(token, purpose, *, lock=False):
    try:
        payload = signing.loads(str(token or ""), salt="user-otp-login", max_age=600)
    except (signing.BadSignature, signing.SignatureExpired):
        return None, None
    if payload.get("purpose") != purpose or not payload.get("nonce"):
        return None, None
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    queryset = AuthChallenge.objects.select_related("user")
    if lock:
        queryset = queryset.select_for_update()
    record = queryset.filter(
        token_sha256=digest, purpose=purpose, consumed_at__isnull=True,
        expires_at__gt=timezone.now(), user_id=payload.get("user_id"), user__is_active=True,
    ).first()
    if not record or not secrets.compare_digest(
            str(payload.get("auth_state", "")), record.user.get_session_auth_hash()):
        return None, None
    return record.user, record


def _consume_challenge(record):
    record.consumed_at = timezone.now()
    record.save(update_fields=["consumed_at"])


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@ensure_csrf_cookie
@never_cache
def csrf(request):
    return Response({"csrfToken": get_token(request)})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
@never_cache
def login_view(request):
    username = str(request.data.get("username", "")).strip()
    password = request.data.get("password", "")
    ip = request.META.get("REMOTE_ADDR", "unknown")
    throttle_identity = f"username:{username.casefold()}"
    retry_after = _reserve_auth_attempt(throttle_identity, ip, "password-login")
    if retry_after:
        return Response({"error": "Too many failed sign-in attempts. Wait for the cooldown to expire, then try again.",
                         "retry_after_seconds": retry_after}, status=429)

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        AuditEvent.objects.create(action="login_failed", entity_type="user", entity_id=username[:120])
        return Response({"error": "Invalid username or password."}, status=401)
    _release_auth_attempt(throttle_identity, ip, "password-login")
    if not has_any_role(user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP}):
        AuditEvent.objects.create(actor=user, action="login_no_role", entity_type="user", entity_id=str(user.pk))
        return Response({"error": "This account has no application role."}, status=403)
    device = AdminOTPDevice.objects.filter(user=user, enabled=True).first()
    if not device:
        challenge = _issue_challenge(user, "enroll", 600)
        AuditEvent.objects.create(actor=user, action="otp_enrollment_required", entity_type="user",
                                  entity_id=str(user.pk))
        return Response({"otp_setup_required": True, "challenge": challenge, "expires_in": 600})
    challenge = _issue_challenge(user, "login", 300)
    AuditEvent.objects.create(actor=user, action="otp_challenge_issued", entity_type="user",
                              entity_id=str(user.pk))
    return Response({"otp_required": True, "challenge": challenge, "expires_in": 300})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_otp_status(request):
    if not has_any_role(request.user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP}):
        return Response({"error": "This account has no application role."}, status=403)
    device = AdminOTPDevice.objects.filter(user=request.user).first()
    return Response({"applicable": True, "enabled": bool(device and device.enabled),
                     "setup_pending": bool(device and not device.enabled),
                     "recovery_codes_remaining": len(device.recovery_code_hashes) if device else 0})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
@never_cache
def admin_otp_setup(request):
    user = _challenge_user(request.data.get("challenge"), "enroll")
    if not user:
        return Response({"error": "This authenticator setup challenge expired. Sign in again.",
                         "code": "challenge_expired"}, status=401)
    if not has_any_role(user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP}):
        return Response({"error": "This account has no application role."}, status=403)
    device = AdminOTPDevice.objects.filter(user=user).first()
    if device and device.enabled:
        return Response({"error": "Authenticator OTP is already enabled for this account."}, status=409)
    secret = new_secret()
    try:
        stored_secret = encrypt_secret(secret)
    except ImproperlyConfigured as exc:
        return Response({"error": str(exc)}, status=503)
    if device:
        device.secret = stored_secret
        device.last_used_step = -1
        device.recovery_code_hashes = []
        device.save(update_fields=["secret", "last_used_step", "recovery_code_hashes", "updated_at"])
    else:
        AdminOTPDevice.objects.create(user=user, secret=stored_secret)
    AuditEvent.objects.create(actor=user, action="otp_setup_started",
                              entity_type="user", entity_id=str(user.pk))
    return Response({"secret": secret,
                     "otpauth_url": provisioning_uri(secret, user.get_username())})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
@transaction.atomic
@never_cache
def admin_otp_confirm(request):
    user, challenge_record = _active_challenge(request.data.get("challenge"), "enroll", lock=True)
    if not user:
        return Response({"error": "This authenticator setup challenge expired. Sign in again.",
                         "code": "challenge_expired"}, status=401)
    if not has_any_role(user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP}):
        return Response({"error": "This account has no application role."}, status=403)
    challenge = str(request.data.get("challenge", ""))
    ip = request.META.get("REMOTE_ADDR", "unknown")
    retry_after = _reserve_auth_attempt(user.pk, ip, "otp-enroll")
    if retry_after:
        return Response({"error": "Too many invalid authenticator setup codes. Wait before retrying.",
                         "retry_after_seconds": retry_after}, status=429)
    device = AdminOTPDevice.objects.select_for_update().filter(user=user, enabled=False).first()
    if not device:
        return Response({"error": "Start authenticator setup first."}, status=409)
    step = verify_code(decrypt_secret(device.secret), request.data.get("code"))
    if step is None:
        AuditEvent.objects.create(actor=user, action="otp_verification_failed",
                                  entity_type="user", entity_id=str(user.pk))
        return Response({"error": "That code is invalid or expired. Try the latest code from your authenticator."}, status=400)
    recovery_codes = [secrets.token_hex(6).upper() for _ in range(10)]
    device.enabled = True
    device.last_used_step = step
    device.recovery_code_hashes = [make_password(code) for code in recovery_codes]
    device.save(update_fields=["enabled", "last_used_step", "recovery_code_hashes", "updated_at"])
    _consume_challenge(challenge_record)
    _release_auth_attempt(user.pk, ip, "otp-enroll")
    AuditEvent.objects.create(actor=user, action="otp_enabled",
                              entity_type="user", entity_id=str(user.pk))
    response = {"enabled": True, "recovery_codes": recovery_codes}
    login(request, user)
    mark_mfa_verified(request.session, user, device)
    response.update({"user": _user_payload(user), "access": _access_token(request, user),
                     "token_type": "Bearer", "expires_in": settings.JWT_ACCESS_TOKEN_SECONDS})
    AuditEvent.objects.create(actor=user, action="login_succeeded", entity_type="user",
                              entity_id=str(user.pk), details={"otp": "enrollment"})
    return Response(response)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def admin_otp_disable(request):
    return Response({"error": "Authenticator OTP is required for every account and cannot be disabled."}, status=403)


def _challenge_user(challenge, purpose):
    user, _record = _active_challenge(challenge, purpose)
    return user


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
@never_cache
def complete_otp_login(request):
    challenge = str(request.data.get("challenge", ""))
    ip = request.META.get("REMOTE_ADDR", "unknown")
    user, challenge_record = _active_challenge(challenge, "login")
    if not user:
        try:
            signing.loads(challenge, salt="user-otp-login", max_age=300)
        except (signing.BadSignature, signing.SignatureExpired):
            return Response({"error": "This sign-in challenge expired. Enter your password again.",
                             "code": "challenge_expired"}, status=401)
        return Response({"error": "This sign-in challenge is invalid or already used. Enter your password again.",
                         "code": "challenge_invalid"}, status=401)
    if not user or not has_any_role(user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP}):
        return Response({"error": "This authenticator sign-in is no longer valid. Enter your password again.",
                         "code": "authentication_state_invalid"}, status=401)
    retry_after = _reserve_auth_attempt(user.pk, ip, "otp-login")
    if retry_after:
        return Response({"error": "Too many invalid OTP attempts. Wait before retrying.",
                         "retry_after_seconds": retry_after}, status=429)
    with transaction.atomic():
        user, challenge_record = _active_challenge(challenge, "login", lock=True)
        if not user:
            return Response({"error": "This sign-in challenge is invalid or already used. Enter your password again.",
                             "code": "challenge_invalid"}, status=401)
        device = AdminOTPDevice.objects.select_for_update().filter(user=user, enabled=True).first()
        if not device:
            return Response({"error": "This authenticator sign-in is no longer valid. Enter your password again.",
                             "code": "authentication_state_invalid"}, status=401)
        step = verify_code(decrypt_secret(device.secret), request.data.get("code"))
        recovery_hash_index = None
        supplied_recovery = str(request.data.get("recovery_code", "")).strip().upper()
        if step is None and supplied_recovery:
            for i, password_hash in enumerate(device.recovery_code_hashes):
                if check_password(supplied_recovery, password_hash):
                    recovery_hash_index = i
                    break
        if (step is None and recovery_hash_index is None) or (step is not None and step <= device.last_used_step):
            AuditEvent.objects.create(actor=user, action="otp_verification_failed", entity_type="user",
                                      entity_id=str(user.pk))
            return Response({"error": "That code is invalid, expired, or already used."}, status=401)
        if recovery_hash_index is not None:
            hashes = list(device.recovery_code_hashes)
            hashes.pop(recovery_hash_index)
            device.recovery_code_hashes = hashes
            device.save(update_fields=["recovery_code_hashes", "updated_at"])
            auth_method = "recovery_code"
        else:
            device.last_used_step = step
            device.save(update_fields=["last_used_step", "updated_at"])
            auth_method = "totp"
        _consume_challenge(challenge_record)
    _release_auth_attempt(user.pk, ip, "otp-login")
    login(request, user)
    mark_mfa_verified(request.session, user, device)
    AuditEvent.objects.create(actor=user, action="login_succeeded", entity_type="user",
                              entity_id=str(user.pk), details={"otp": auth_method})
    return Response({"user": _user_payload(user), "access": _access_token(request, user),
                     "token_type": "Bearer", "expires_in": settings.JWT_ACCESS_TOKEN_SECONDS})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def refresh_access(request):
    """Issue a short-lived JWT using the existing HttpOnly Django login session."""
    return Response({"access": _access_token(request, request.user), "token_type": "Bearer",
                     "expires_in": settings.JWT_ACCESS_TOKEN_SECONDS})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    user = request.user
    claims = request.auth if isinstance(request.auth, dict) else None
    if claims is not None:
        if (str(claims.get("sub")) != str(user.pk)
                or not revoke_mfa_session(user, claims.get("sid"), claims.get("mfa_at"))):
            return Response({"error": "The authenticated session could not be revoked."}, status=401)
    # A bearer token can accompany a different user's cookie. Flush only a
    # cookie session whose MFA evidence is bound to the authenticated user.
    if mfa_session_is_valid(request.session, user):
        logout(request)
    AuditEvent.objects.create(actor=user, action="logout", entity_type="user", entity_id=str(user.pk))
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    if not request.user.is_authenticated:
        return Response({"authenticated": False})
    return Response({"authenticated": True, "user": _user_payload(request.user)})


@api_view(["GET", "POST"])
@permission_classes([CanAdminister])
@transaction.atomic
def user_management(request):
    """List application accounts or create one with exactly one application role."""
    User = get_user_model()
    if request.method == "GET":
        users = User.objects.prefetch_related("groups").order_by("username")
        return Response([{
            "id": user.pk, "username": user.get_username(),
            "display_name": user.get_full_name() or user.get_username(),
            "email": user.email, "roles": _role_names(user), "is_active": user.is_active,
            "last_login": user.last_login,
        } for user in users])

    username = str(request.data.get("username", "")).strip()
    role = str(request.data.get("role", "")).strip()
    password = request.data.get("password", "")
    if not username or len(username) > 150:
        return Response({"error": "Enter a username of 1 to 150 characters."}, status=400)
    if role not in APPLICATION_ROLES:
        return Response({"error": "Choose SUPER_ADMIN, ANALYST, REVIEWER, or CLIENT."}, status=400)
    if not isinstance(password, str) or len(password) < 10:
        return Response({"error": "Set an initial password of at least 10 characters."}, status=400)
    if User.objects.filter(username__iexact=username).exists():
        return Response({"error": "That username is already in use."}, status=409)
    user = User(username=username,
                first_name=str(request.data.get("first_name", "")).strip()[:150],
                last_name=str(request.data.get("last_name", "")).strip()[:150],
                email=str(request.data.get("email", "")).strip()[:254])
    try:
        password_validation.validate_password(password, user)
    except ValidationError as exc:
        return Response({"error": " ".join(exc.messages)}, status=400)
    user.set_password(password)
    user.save()
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    AuditEvent.objects.create(actor=request.user, action="application_user_created", entity_type="user",
                              entity_id=str(user.pk), details={"role": role})
    return Response({"id": user.pk, "username": user.get_username(), "roles": [role],
                     "is_active": user.is_active}, status=201)


@api_view(["POST"])
@permission_classes([CanAdminister])
@transaction.atomic
def manage_user(request, user_id: int):
    """Update an account's single role, active status, or password."""
    from django.shortcuts import get_object_or_404

    User = get_user_model()
    user = get_object_or_404(User.objects.prefetch_related("groups"), pk=user_id)
    if user.is_superuser:
        return Response({"error": "Superuser accounts must be managed outside application roles."}, status=409)
    target_role = request.data.get("role")
    if target_role is not None and (not isinstance(target_role, str)
                                    or target_role not in APPLICATION_ROLES):
        return Response({"error": "Choose SUPER_ADMIN, ANALYST, REVIEWER, or CLIENT."}, status=400)
    requested_active = request.data.get("is_active", user.is_active)
    if not isinstance(requested_active, bool):
        return Response({"error": "is_active must be true or false."}, status=400)
    if user.pk == request.user.pk and (
            (target_role is not None and target_role != SUPER_ADMIN_GROUP) or not requested_active):
        return Response({"error": "You cannot remove or deactivate your own Super Admin access."}, status=409)
    proposed_active = requested_active
    proposed_role = target_role or next((r for r in APPLICATION_ROLES
                                         if has_any_role(user, {r})), CLIENT_GROUP)
    current_admins = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name__in=(ADMIN_GROUP, SUPER_ADMIN_GROUP))).distinct().count()
    stays_admin = proposed_active and proposed_role == SUPER_ADMIN_GROUP
    loses_admin = user.is_active and has_any_role(user, {SUPER_ADMIN_GROUP})
    proposed_admin_count = current_admins - int(loses_admin) + int(stays_admin)
    required_admins = min(2, current_admins)
    if proposed_admin_count < required_admins:
        return Response({"error": "This change would reduce the active Super Admin count below the release requirement."}, status=409)

    password = request.data.get("password")
    if password:
        if not isinstance(password, str):
            return Response({"error": "Password must be text."}, status=400)
        try:
            password_validation.validate_password(password, user)
        except ValidationError as exc:
            return Response({"error": " ".join(exc.messages)}, status=400)
        user.set_password(password)
    user.is_active = proposed_active
    user.save(update_fields=["password", "is_active"] if password else ["is_active"])
    user.groups.clear()
    user.groups.add(Group.objects.get_or_create(name=proposed_role)[0])
    AuditEvent.objects.create(actor=request.user, action="application_user_updated", entity_type="user",
                              entity_id=str(user.pk), details={"role": proposed_role,
                                                              "is_active": user.is_active,
                                                              "password_reset": bool(password)})
    return Response({"id": user.pk, "username": user.get_username(), "roles": [proposed_role],
                     "is_active": user.is_active})


def signoff_status():
    from . import data

    try:
        source_hash = data.comparison_sha256()
        info = data.run_info()
    except data.DataMissing:
        return {"signed": False, "generated_at": timezone.now().isoformat(), "source_hash": ""}
    report = (ReportRecord.objects.filter(source_hash=source_hash, config_hash=data.config_sha256(),
                                          status="released")
              .order_by("-generated_at").first())
    return {
        "signed": report is not None,
        "generated_at": info.get("generated_at", timezone.now().isoformat()),
        "source_hash": source_hash,
        "approved_by": report.approved_by.get_username() if report and report.approved_by else None,
        "approved_at": report.approved_at.isoformat() if report and report.approved_at else None,
        "clinical_signoffs": (report.metadata or {}).get("clinical_signoffs", {}) if report else {},
    }


@api_view(["POST"])
@permission_classes([CanReview])
def signoff(request):
    from . import data

    if not has_any_role(request.user, {SUPER_ADMIN_GROUP, REVIEWER_GROUP}):
        return Response({"error": "Only a Reviewer or Super Admin can release reports."}, status=403)
    try:
        info = data.run_info()
        source_hash = data.comparison_sha256()
        selected_codes = set(data.drug_config())
    except data.DataMissing as exc:
        return Response({"error": str(exc)}, status=503)
    config_hash = data.config_sha256()
    report = (ReportRecord.objects.filter(source_hash=source_hash, config_hash=config_hash)
              .order_by("-generated_at").first())
    if report is None:
        return Response({"error": "Build a report from the Admin pipeline before release."}, status=409)
    if report.status == "released":
        return Response({"ok": True, "report_id": report.pk, **signoff_status()})
    if report.status != "generated":
        return Response({"error": "This report is still a draft. Resolve its source, scope, and review gaps first."}, status=409)
    if not report.built_by_id:
        return Response({"error": "The report has no recorded builder; rebuild it through the Admin pipeline."}, status=409)
    if report.built_by_id == request.user.pk:
        return Response({"error": "The Admin who built a report cannot release it."}, status=403)

    User = get_user_model()
    active_reviewers = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name__in=(ADMIN_GROUP, SUPER_ADMIN_GROUP, REVIEWER_GROUP))).distinct().count()
    if active_reviewers < 2:
        return Response({"error": "At least two active Reviewers or Super Admins are required for independent release."}, status=409)

    payload_path = settings.BASE_DIR.parent / "reports" / "dashboard_data.json"
    try:
        payload_bytes = payload_path.read_bytes()
        payload = __import__("json").loads(payload_bytes)
    except (OSError, ValueError):
        return Response({"error": "The report artifact is missing or invalid."}, status=503)
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    if ((report.metadata or {}).get("dashboard_sha256") != payload_hash
            or payload.get("draft")
            or payload.get("drug_count") != len(selected_codes)
            or payload.get("reviewed_drug_count") != len(selected_codes)):
        return Response({"error": "The built report is stale or not fully reviewed. Rebuild it before release."}, status=409)
    reviews = {row.hcpcs: row for row in DrugUnitReview.objects.filter(
        scope_source_sha256=info.get("drug_selection_source_sha256"), hcpcs__in=selected_codes)}
    missing_reviews = sorted(code for code in selected_codes if
        code not in reviews or reviews[code].status != "validated"
        or not reviews[code].clinical_reviewer_name.strip())
    if missing_reviews:
        return Response({"error": "Clinical sign-off is required for every selected drug.",
                         "missing_hcpcs": missing_reviews}, status=409)

    report.status = "released"
    report.approved_by = request.user
    report.approved_at = timezone.now()
    clinical_signoffs = {
        code: {"reviewer_name": reviews[code].clinical_reviewer_name,
               "reviewed_at": reviews[code].reviewed_at.isoformat(), "decision": "approved"}
        for code in sorted(selected_codes)
    }
    report.metadata = {**(report.metadata or {}), "released_comparison_sha256": source_hash,
                       "released_by_role": ADMIN_GROUP, "clinical_signoffs": clinical_signoffs}
    report.save(update_fields=["status", "approved_by", "approved_at", "metadata"])
    AuditEvent.objects.create(actor=request.user, action="report_released", entity_type="report",
                              entity_id=str(report.pk), details={"source_hash": source_hash})
    return Response({"ok": True, "report_id": report.pk, **signoff_status()})
