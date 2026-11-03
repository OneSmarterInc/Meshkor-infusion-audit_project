"""Savings, quality, review, and report endpoints."""
import csv
import io
import json
from collections import Counter

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from pipeline.common import csv_safe_cell
from .models import AuditEvent, AuditReview, SavingsOpportunity
from .permissions import CanAnalyze, CanView, CanReview, has_any_role, SUPER_ADMIN_GROUP, REVIEWER_GROUP
from .plan_audit_common import get_visible_audit, is_admin
from .plan_audit_run import _decimal_text, _visible_totals


def _admin_required(request):
    if not is_admin(request.user):
        return Response({"error": "This action requires the Admin role."}, status=403)
    return None


def _savings_payload(item):
    return {
        "id": item.pk, "hcpcs_code": item.hcpcs_code, "quarter": item.quarter,
        "service_setting": item.service_setting, "current_site_npi": item.current_site_npi,
        "alternative_site_npi": item.alternative_site_npi,
        "current_allowed_price_per_unit": _decimal_text(item.current_allowed_price_per_unit),
        "alternative_allowed_price_per_unit": _decimal_text(item.alternative_allowed_price_per_unit),
        "units": _decimal_text(item.units),
        "potential_allowed_savings": _decimal_text(item.potential_allowed_savings),
        "potential_paid_difference": _decimal_text(item.potential_paid_difference),
        "network_status": item.network_status,
        "clinical_review_status": item.clinical_review_status,
        "validation_status": item.validation_status,
        "source_references": item.source_references,
        "label": "Potential allowed-amount savings; clinical review pending.",
    }


@api_view(["GET"])
@permission_classes([CanView])
def audit_savings(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    return Response([_savings_payload(item) for item in audit.savings_opportunities.all()[:2000]])


@api_view(["GET"])
@permission_classes([CanView])
def audit_network_validation(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    rows = audit.results.order_by("site_npi", "quarter", "hcpcs_code")
    return Response([{
        "site_npi": row.site_npi, "quarter": row.quarter,
        "data_network_status": row.network_status,
        "hcpcs_code": row.hcpcs_code, "service_setting": row.service_setting,
        "source_references": row.source_references,
    } for row in rows[:2000]])


@api_view(["GET"])
@permission_classes([CanView])
def audit_quality(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    results = audit.results.all()
    statuses = Counter(results.values_list("data_quality_status", flat=True))
    result_count = results.count()
    asp_count = results.filter(asp_payment_limit__isnull=False).count()
    hospital_count = results.filter(hospital_price_per_unit__isnull=False).count()
    return Response({
        "audit_id": audit.pk, "status": audit.status,
        "import_quality": audit.summary_import.validation_summary,
        "result_status_counts": dict(statuses),
        "benchmark_availability": {
            "result_rows": result_count,
            "asp_available": asp_count,
            "asp_unavailable": result_count - asp_count,
            "hospital_reference_available": hospital_count,
            "hospital_reference_unavailable": result_count - hospital_count,
        },
        "notes": [
            "Only approved, unsuppressed aggregate rows are included.",
            "Hospital price references require a verified site mapping and an unambiguous matching quarter and setting.",
            "Network status is unknown unless the plan roster covers the entire claim quarter.",
            "Potential savings remain pending clinical review; no savings are treated as guaranteed.",
            "Summary metrics include only visible approved aggregate rows.",
        ] + (["Potential savings are unavailable because no evidence-backed lower-priced full-quarter in-network alternative was identified."]
             if not audit.savings_opportunities.exists() else []),
    })


@api_view(["POST"])
@permission_classes([CanAnalyze])
def generate_plan_report(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    if audit.status not in {"completed", "needs_review"} or not audit.results.exists():
        return Response({"error": "Run the audit before generating a report."}, status=409)
    audit.report_generated_at = timezone.now()
    audit.save(update_fields=["report_generated_at"])
    AuditEvent.objects.create(actor=request.user, action="plan_audit_report_generated",
                              entity_type="plan_audit", entity_id=str(audit.pk))
    return Response({"audit_id": audit.pk, "generated_at": audit.report_generated_at,
                     "report_url": f"/api/v1/plan-audits/{audit.pk}/report/"})


@api_view(["GET"])
@permission_classes([CanView])
def plan_report(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    if not audit.report_generated_at:
        return Response({"error": "Generate this report before downloading it."}, status=404)
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)
    y = height - 42

    def draw(value, *, size=8, bold=False):
        nonlocal y
        text = str(value if value is not None else "")
        text = "".join(character if 32 <= ord(character) < 127 else "?" for character in text)
        font = "Helvetica-Bold" if bold else "Helvetica"
        pdf.setFont(font, size)
        pdf.drawString(36, y, text[:180])
        y -= size + 6
        if y < 40:
            pdf.showPage()
            y = height - 42

    draw("MeshKor Healthcare | Plan-Specific Infusion Drug Audit", size=15, bold=True)
    draw(f"Organization: {audit.organization.organization_name} | Plan: {audit.plan.plan_name}", size=10)
    draw(f"Audit: {audit.audit_name} | Period: {audit.reporting_period} | Status: {audit.status}")
    draw(f"Generated: {audit.report_generated_at.isoformat(timespec='seconds')}")
    metrics = _visible_totals(audit)
    draw(f"Visible units: {metrics['units_analyzed']} | Visible allowed: {metrics['visible_allowed_amount']} "
         f"| Visible paid: {metrics['visible_paid_amount']}")
    savings_text = metrics["potential_allowed_savings"]
    savings_label = (f"Potential allowed savings: {savings_text} (requires clinical review; not guaranteed)"
                     if savings_text is not None else
                     "Potential allowed savings: unavailable (no evidence-backed lower-priced full-quarter in-network alternative)")
    draw(savings_label, size=8)
    draw("HCPCS | NPI | Quarter | Allowed/unit | Paid/unit | Hospital/unit | ASP | Hospital x | Allowed x | Paid x | Quality",
         bold=True)
    for row in audit.results.all()[:5000]:
        draw(f"{row.hcpcs_code} | {row.site_npi} | {row.quarter} | "
             f"{_decimal_text(row.plan_allowed_price_per_unit)} | "
             f"{_decimal_text(row.plan_paid_price_per_unit)} | "
             f"{_decimal_text(row.hospital_price_per_unit)} | "
             f"{_decimal_text(row.asp_payment_limit)} | "
             f"{_decimal_text(row.hospital_markup_multiple)} | "
             f"{_decimal_text(row.allowed_markup_multiple)} | "
             f"{_decimal_text(row.paid_markup_multiple)} | {row.data_quality_status}")
    draw("Amounts are limited to visible approved aggregates. Hospital reference prices are not this plan's contracted rates.")
    draw("An alternative is not actionable until network, billing unit, service setting, and clinical suitability are verified.")
    pdf.save()
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="plan-audit-{audit.pk}.pdf"'
    return response


@api_view(["GET"])
@permission_classes([CanView])
def export_plan_csv(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    output = io.StringIO(newline="")
    fields = [
        "hcpcs_code", "site_npi", "quarter", "service_setting", "total_units",
        "total_allowed_amount", "total_paid_amount", "member_count_band",
        "plan_allowed_price_per_unit", "plan_paid_price_per_unit",
        "hospital_price_per_unit", "asp_payment_limit", "hospital_markup_multiple", "allowed_markup_multiple",
        "paid_markup_multiple", "plan_to_hospital_price_multiple",
        "network_status", "data_quality_status", "data_quality_notes", "source_references",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in audit.results.all():
        data = {
            "hcpcs_code": row.hcpcs_code, "site_npi": row.site_npi,
            "quarter": row.quarter, "service_setting": row.service_setting,
            "total_units": _decimal_text(row.total_units),
            "total_allowed_amount": _decimal_text(row.total_allowed_amount),
            "total_paid_amount": _decimal_text(row.total_paid_amount),
            "member_count_band": row.member_count_band,
            "plan_allowed_price_per_unit": _decimal_text(row.plan_allowed_price_per_unit),
            "plan_paid_price_per_unit": _decimal_text(row.plan_paid_price_per_unit),
            "hospital_price_per_unit": _decimal_text(row.hospital_price_per_unit),
            "asp_payment_limit": _decimal_text(row.asp_payment_limit),
            "hospital_markup_multiple": _decimal_text(row.hospital_markup_multiple),
            "allowed_markup_multiple": _decimal_text(row.allowed_markup_multiple),
            "paid_markup_multiple": _decimal_text(row.paid_markup_multiple),
            "plan_to_hospital_price_multiple": _decimal_text(row.plan_to_hospital_price_multiple),
            "network_status": row.network_status, "data_quality_status": row.data_quality_status,
            "data_quality_notes": "; ".join(row.data_quality_notes),
            "source_references": json.dumps(row.source_references, sort_keys=True, separators=(",", ":")),
        }
        writer.writerow({key: csv_safe_cell(value) for key, value in data.items()})
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="plan-audit-{audit.pk}-results.csv"'
    return response


@api_view(["GET", "POST"])
@permission_classes([CanView])
def audit_review(request, audit_id):
    if request.method == "POST" and not has_any_role(request.user, {SUPER_ADMIN_GROUP, REVIEWER_GROUP}):
        return Response({"error": "Only a Reviewer or Super Admin can submit a formal review."}, status=403)
    audit = get_visible_audit(request.user, audit_id)
    if request.method == "GET":
        return Response([{
            "id": item.pk, "reviewer": item.reviewer.get_username() if item.reviewer_id else None,
            "review_status": item.review_status, "comments": item.comments,
            "reviewed_at": item.reviewed_at,
        } for item in audit.reviews.select_related("reviewer")])
    if audit.status not in {"completed", "needs_review"}:
        return Response({"error": "Run the audit before submitting a review."}, status=409)
    comments = str(request.data.get("comments", "")).strip()
    if len(comments) > 2000:
        return Response({"error": "Review comments are limited to 2,000 characters."}, status=400)
    review = AuditReview.objects.create(
        plan_audit=audit, reviewer=request.user,
        review_status="submitted", comments=comments,
    )
    AuditEvent.objects.create(actor=request.user, action="plan_audit_review_submitted",
                              entity_type="plan_audit", entity_id=str(audit.pk))
    return Response({"id": review.pk, "review_status": review.review_status,
                     "reviewed_at": review.reviewed_at}, status=201)


def _set_review(request, audit_id, status):
    if not has_any_role(request.user, {SUPER_ADMIN_GROUP, REVIEWER_GROUP}):
        return Response({"error": "Only a Reviewer or Super Admin can approve or reject an audit."}, status=403)
    audit = get_visible_audit(request.user, audit_id)
    if audit.status not in {"completed", "needs_review"}:
        return Response({"error": "Run the audit before reviewing it."}, status=409)
    comments = str(request.data.get("comments", "")).strip()
    if status == "rejected" and not comments:
        return Response({"error": "Provide a review reason when rejecting an audit."}, status=400)
    if len(comments) > 2000:
        return Response({"error": "Review comments are limited to 2,000 characters."}, status=400)
    review = AuditReview.objects.create(
        plan_audit=audit, reviewer=request.user, review_status=status, comments=comments,
    )
    audit.status = "completed" if status == "approved" else "needs_review"
    audit.save(update_fields=["status"])
    AuditEvent.objects.create(actor=request.user, action=f"plan_audit_{status}",
                              entity_type="plan_audit", entity_id=str(audit.pk))
    return Response({"id": review.pk, "audit_id": audit.pk, "review_status": status,
                     "audit_status": audit.status, "reviewed_at": review.reviewed_at})


@api_view(["POST"])
@permission_classes([CanReview])
def approve_plan_audit(request, audit_id):
    return _set_review(request, audit_id, "approved")


@api_view(["POST"])
@permission_classes([CanReview])
def reject_plan_audit(request, audit_id):
    return _set_review(request, audit_id, "rejected")
