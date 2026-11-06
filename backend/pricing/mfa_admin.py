from django.contrib.admin import AdminSite
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from .mfa import mfa_session_is_valid


class MFAAdminSite(AdminSite):
    """Django Admin is available only after the application's MFA sign-in flow."""

    def has_permission(self, request):
        return super().has_permission(request) and mfa_session_is_valid(request.session, request.user)

    def login(self, request, extra_context=None):
        if self.has_permission(request):
            return HttpResponseRedirect(reverse("admin:index"))
        return HttpResponseForbidden(
            "Multi-factor authentication is required. Sign in through the MeshKor application before using Django Admin."
        )
