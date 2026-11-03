"""Shared authorization and serialization helpers for plan-specific audits."""
from datetime import date
from django.shortcuts import get_object_or_404

from .models import (
    ClaimsSummaryImport, Plan, PlanAudit, PlanOrganization,
)
from .permissions import (
    ADMIN_GROUP, ANALYST_GROUP, CLIENT_GROUP, REVIEWER_GROUP,
    SUPER_ADMIN_GROUP, VIEWER_GROUP, has_any_role,
)


def is_admin(user):
    return has_any_role(user, {ADMIN_GROUP})


def visible_organizations(user):
    queryset = PlanOrganization.objects.filter(status="active")
    if is_admin(user):
        return queryset
    return queryset.filter(user_access__user=user).distinct()


def get_visible_organization(user, organization_id):
    return get_object_or_404(visible_organizations(user), pk=organization_id)


def get_visible_plan(user, plan_id):
    plans = Plan.objects.select_related("organization").filter(status="active")
    if not is_admin(user):
        plans = plans.filter(organization__user_access__user=user).distinct()
    return get_object_or_404(plans, pk=plan_id)


def get_visible_import(user, import_id):
    imports = ClaimsSummaryImport.objects.select_related("organization", "plan")
    if not is_admin(user):
        imports = imports.filter(organization__user_access__user=user).distinct()
    return get_object_or_404(imports, pk=import_id)


def get_visible_audit(user, audit_id):
    audits = PlanAudit.objects.select_related(
        "organization", "plan", "summary_import"
    )
    if not is_admin(user):
        audits = audits.filter(organization__user_access__user=user).distinct()
        if (has_any_role(user, {CLIENT_GROUP, VIEWER_GROUP})
                and not has_any_role(user, {SUPER_ADMIN_GROUP, ADMIN_GROUP, ANALYST_GROUP, REVIEWER_GROUP})):
            from django.db.models import OuterRef, Subquery
            from .models import AuditReview
            latest_status = Subquery(
                AuditReview.objects.filter(plan_audit=OuterRef("pk"))
                .order_by("-reviewed_at", "-id")
                .values("review_status")[:1]
            )
            audits = audits.filter(
                status="completed",
                report_generated_at__isnull=False,
            ).annotate(latest_review_status=latest_status).filter(
                latest_review_status="approved"
            )
    return get_object_or_404(audits, pk=audit_id)


def organization_payload(item):
    return {
        "id": item.pk, "organization_name": item.organization_name,
        "organization_type": item.organization_type, "status": item.status,
        "created_at": item.created_at,
    }


def plan_payload(item):
    return {
        "id": item.pk, "organization_id": item.organization_id,
        "organization_name": item.organization.organization_name,
        "plan_name": item.plan_name, "payer_reference": item.payer_reference,
        "plan_reference": item.plan_reference, "effective_start": item.effective_start,
        "effective_end": item.effective_end, "status": item.status,
        "created_at": item.created_at,
    }


def import_payload(item):
    return {
        "id": item.pk, "organization_id": item.organization_id,
        "organization_name": item.organization.organization_name, "plan_id": item.plan_id,
        "plan_name": item.plan.plan_name, "source_file_name": item.source_file_name,
        "file_hash": item.file_hash, "schema_version": item.schema_version,
        "imported_at": item.imported_at, "import_status": item.import_status,
        "row_count": item.row_count, "source_period": item.source_period,
        "validation_summary": item.validation_summary,
    }


def audit_payload(item):
    return {
        "id": item.pk, "organization_id": item.organization_id,
        "organization_name": item.organization.organization_name, "plan_id": item.plan_id,
        "plan_name": item.plan.plan_name, "summary_import_id": item.summary_import_id,
        "audit_name": item.audit_name, "reporting_period": item.reporting_period,
        "status": item.status, "created_by": item.created_by.get_username() if item.created_by_id else None,
        "created_at": item.created_at, "completed_at": item.completed_at,
        "report_generated_at": item.report_generated_at, "summary": item.summary,
    }


def parse_iso_date(value, label, *, optional=False):
    if value in (None, "") and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date.") from exc
    return parsed


def valid_npi(value):
    import re
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{10}", raw):
        return False
    digits = [int(digit) for digit in "80840" + raw]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
