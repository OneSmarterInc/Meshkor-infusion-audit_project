import re

from rest_framework.permissions import BasePermission


ADMIN_GROUP = "Admin"
VIEWER_GROUP = "Viewer"
SUPER_ADMIN_GROUP = "SUPER_ADMIN"
ANALYST_GROUP = "ANALYST"
REVIEWER_GROUP = "REVIEWER"
CLIENT_GROUP = "CLIENT"
APPLICATION_ROLES = (SUPER_ADMIN_GROUP, ANALYST_GROUP, REVIEWER_GROUP, CLIENT_GROUP)
ROLE_ALIASES = {
    SUPER_ADMIN_GROUP: {SUPER_ADMIN_GROUP, ADMIN_GROUP},
    ANALYST_GROUP: {ANALYST_GROUP},
    REVIEWER_GROUP: {REVIEWER_GROUP},
    CLIENT_GROUP: {CLIENT_GROUP, VIEWER_GROUP},
    ADMIN_GROUP: {SUPER_ADMIN_GROUP, ADMIN_GROUP},
    VIEWER_GROUP: {CLIENT_GROUP, VIEWER_GROUP},
}


def has_any_role(user, roles: set[str]) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    names = set().union(*(ROLE_ALIASES.get(role, {role}) for role in roles))
    return user.groups.filter(name__in=names).exists()


def _log_read(request):
    if request.method != "GET" or not request.user.is_authenticated:
        return
    from .models import AuditEvent

    path = request.path
    downloadable = any(marker in path for marker in ("/exports/", "/pdf", ".csv", ".xlsx"))
    AuditEvent.objects.create(
        actor=request.user,
        action="report_downloaded" if downloadable else "data_viewed",
        entity_type="api_resource",
        entity_id=path[:120],
        details={"method": "GET", "path": path[:500]},
    )


def _client_get_route_allowed(path):
    """Allow only client-facing organization, plan, published-audit, and market reads."""
    route = path
    for prefix in ("/api/v1", "/api"):
        if route.startswith(prefix + "/"):
            route = route[len(prefix):]
            break
    if route == "/organizations/" or route == "/plans/":
        return True
    if re.fullmatch(r"/plans/\d+/", route):
        return True
    if route == "/plan-audits/" or re.fullmatch(r"/plan-audits/\d+/", route):
        return True
    if re.fullmatch(
            r"/plan-audits/\d+/(overview|results|historical-spending|drug-analysis|"
            r"hospital-comparison|asp-benchmark|savings|network-validation|quality|report|export-csv|review)/",
            route):
        return True
    released_market_routes = (
        "/dashboard/summary/", "/dashboard/price-trends/", "/medicare-part-b-drugs/",
        "/hospitals/", "/hospital-drug-price-comparisons/", "/reports/",
    )
    return any(route.startswith(prefix) for prefix in released_market_routes)


class CanView(BasePermission):
    message = "Sign in with an authorized application account."

    def has_permission(self, request, view):
        allowed = has_any_role(request.user, set(APPLICATION_ROLES) | {ADMIN_GROUP, VIEWER_GROUP})
        if (allowed and not request.user.is_superuser
                and has_any_role(request.user, {CLIENT_GROUP, VIEWER_GROUP})
                and not has_any_role(request.user, {SUPER_ADMIN_GROUP, ADMIN_GROUP, ANALYST_GROUP, REVIEWER_GROUP})):
            allowed = request.method == "GET" and _client_get_route_allowed(request.path)
        if allowed:
            _log_read(request)
        return allowed


class CanAnalyze(BasePermission):
    message = "This action requires the Super Admin or Analyst role."

    def has_permission(self, request, view):
        allowed = has_any_role(request.user, {SUPER_ADMIN_GROUP, ANALYST_GROUP})
        if allowed:
            _log_read(request)
        return allowed


class CanAdminister(BasePermission):
    message = "This action requires the Super Admin role."

    def has_permission(self, request, view):
        allowed = has_any_role(request.user, {SUPER_ADMIN_GROUP})
        if allowed:
            _log_read(request)
        return allowed


class CanReview(BasePermission):
    message = "This action requires the Super Admin or Reviewer role."

    def has_permission(self, request, view):
        allowed = has_any_role(request.user, {SUPER_ADMIN_GROUP, REVIEWER_GROUP})
        if allowed:
            _log_read(request)
        return allowed
