from django.contrib.admin.apps import AdminConfig


class MFAAdminConfig(AdminConfig):
    default_site = "pricing.mfa_admin.MFAAdminSite"
