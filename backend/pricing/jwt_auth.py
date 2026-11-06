import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication, SessionAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .mfa import mfa_session_is_valid, token_mfa_session_is_valid


class MFASessionAuthentication(SessionAuthentication):
    """Accept Django sessions only when they carry current server-side MFA evidence."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None
        user, _ = authenticated
        if not mfa_session_is_valid(request.session, user):
            raise AuthenticationFailed("Multi-factor authentication is required.")
        return authenticated


class AccessTokenAuthentication(BaseAuthentication):
    """Validate short-lived, first-party HS256 bearer access tokens."""

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None
        if len(header) != 2 or header[0].lower() != b"bearer":
            raise AuthenticationFailed("Invalid authorization header.")
        User = get_user_model()
        try:
            claims = jwt.decode(header[1], settings.SECRET_KEY, algorithms=["HS256"],
                                options={"require": ["sub", "exp", "iat", "type", "sid", "mfa_at"]})
            if claims["type"] != "access":
                raise AuthenticationFailed("Invalid access token.")
            user = User.objects.get(pk=claims["sub"], is_active=True)
        except (jwt.PyJWTError, User.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed("Invalid or expired access token.")
        if not token_mfa_session_is_valid(user, claims["sid"], claims["mfa_at"]):
            raise AuthenticationFailed("MFA session is invalid or expired.")
        return user, claims

    def authenticate_header(self, request):
        return "Bearer"

