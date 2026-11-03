"""Run and inspect plan-specific audit comparisons."""
import logging
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import AuditEvent, PlanAudit, PlanAuditResult, SavingsOpportunity
from .permissions import CanAnalyze, CanView
from .plan_audit_common import audit_payload, get_visible_audit
from .plan_audit_engine import run_plan_audit

# Allow-listed sort fields for historical spending queries
_ALLOWED_SORT_FIELDS = frozenset([
    "quarter", "hcpcs_code", "site_npi", "service_setting",
    "total_allowed_amount", "total_paid_amount", "total_units",
])
# Allow-listed filter fields
_ALLOWED_FILTER_FIELDS = frozenset([
    "hcpcs_code", "quarter", "site_npi", "service_setting", "data_quality_status",
])


logger = logging.getLogger(__name__)


def _decimal_text(value):
    return None if value is None else format(Decimal(value), "f")


def result_payload(item):
    return {
        "id": item.pk, "hcpcs_code": item.hcpcs_code, "drug_description": item.drug_description,
        "site_npi": item.site_npi,
        "quarter": item.quarter, "service_setting": item.service_setting,
        "total_units": _decimal_text(item.total_units),
        "total_allowed_amount": _decimal_text(item.total_allowed_amount),
        "total_paid_amount": _decimal_text(item.total_paid_amount),
        "member_count_band": item.member_count_band,
        "plan_allowed_price_per_unit": _decimal_text(item.plan_allowed_price_per_unit),
        "plan_paid_price_per_unit": _decimal_text(item.plan_paid_price_per_unit),
        "hospital_price_per_unit": _decimal_text(item.hospital_price_per_unit),
        "asp_payment_limit": _decimal_text(item.asp_payment_limit),
        "hospital_markup_multiple": _decimal_text(item.hospital_markup_multiple),
        "allowed_markup_multiple": _decimal_text(item.allowed_markup_multiple),
        "paid_markup_multiple": _decimal_text(item.paid_markup_multiple),
        "plan_to_hospital_price_multiple": _decimal_text(item.plan_to_hospital_price_multiple),
        "network_status": item.network_status,
        "data_quality_status": item.data_quality_status,
        "data_quality_notes": item.data_quality_notes,
        "source_references": item.source_references,
    }


def _visible_totals(audit):
    results = list(audit.results.all())
    allowed = sum((row.total_allowed_amount for row in results), Decimal(0))
    paid = sum((row.total_paid_amount for row in results), Decimal(0))
    units = sum((row.total_units for row in results), Decimal(0))
    opportunities = list(audit.savings_opportunities.all())
    savings = sum((row.potential_allowed_savings for row in opportunities), Decimal(0))
    return {
        "drugs_analyzed": len({row.hcpcs_code for row in results}),
        "hospitals_compared": len({row.site_npi for row in results if row.hospital_price_per_unit is not None}),
        "sites_with_claims": len({row.site_npi for row in results}),
        "units_analyzed": _decimal_text(units),
        "visible_allowed_amount": _decimal_text(allowed),
        "visible_paid_amount": _decimal_text(paid),
        "potential_allowed_savings": _decimal_text(savings) if opportunities else None,
        "data_quality_warnings": sum(row.data_quality_status != "VALID" for row in results),
        "totals_scope": "visible_approved_aggregate_rows_only",
    }


@api_view(["POST"])
@permission_classes([CanAnalyze])
def run_audit(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    try:
        with transaction.atomic():
            audit = PlanAudit.objects.select_for_update().select_related(
                "organization", "plan", "summary_import"
            ).get(pk=audit.pk)
            if audit.summary_import.import_status != "accepted":
                return Response({"error": "The selected summary import is not accepted."}, status=409)
            audit.status = "running"
            audit.summary = {}
            audit.save(update_fields=["status", "summary"])
            audit.results.all().delete()
            audit.savings_opportunities.all().delete()
            summary = run_plan_audit(audit)
            audit.summary = summary
            has_warnings = any(status != "VALID" for status in summary["quality_status_counts"])
            audit.status = "needs_review" if has_warnings else "completed"
            audit.completed_at = timezone.now()
            audit.save(update_fields=["summary", "status", "completed_at"])
            AuditEvent.objects.create(
                actor=request.user, action="plan_audit_run_completed",
                entity_type="plan_audit", entity_id=str(audit.pk),
                details={"result_rows": summary["result_rows"],
                         "savings_opportunities": summary["savings_opportunities"],
                         "status": audit.status},
            )
    except Exception:
        logger.exception("Plan audit execution failed for audit id %s.", audit.pk)
        PlanAudit.objects.filter(pk=audit.pk).update(
            status="failed", summary={"error": "Audit execution failed; no result details were stored."})
        return Response({"error": "Audit execution failed. Check reference-data availability and server logs."},
                        status=500)
    return Response(audit_payload(audit))


@api_view(["GET"])
@permission_classes([CanView])
def audit_results(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    try:
        limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return Response({"error": "limit and offset must be whole numbers."}, status=400)
    queryset = audit.results.all()
    if request.query_params.get("hcpcs_code"):
        queryset = queryset.filter(hcpcs_code=request.query_params["hcpcs_code"].upper())
    if request.query_params.get("quarter"):
        queryset = queryset.filter(quarter=request.query_params["quarter"])
    if request.query_params.get("data_quality_status"):
        queryset = queryset.filter(data_quality_status=request.query_params["data_quality_status"])
    total = queryset.count()
    rows = queryset[offset:offset + limit]
    return Response({"count": total, "limit": limit, "offset": offset,
                     "results": [result_payload(row) for row in rows]})


@api_view(["GET"])
@permission_classes([CanView])
def drug_analysis(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    grouped = defaultdict(lambda: {
        "units": Decimal(0), "allowed": Decimal(0), "paid": Decimal(0),
        "asp": set(), "allowed_multiple": [], "paid_multiple": [],
        "qualities": defaultdict(int), "sites": set(), "quarters": set(),
    })
    for row in audit.results.all():
        item = grouped[row.hcpcs_code]
        item["units"] += row.total_units
        item["allowed"] += row.total_allowed_amount
        item["paid"] += row.total_paid_amount
        item["sites"].add(row.site_npi)
        item["quarters"].add(row.quarter)
        item["qualities"][row.data_quality_status] += 1
        if row.asp_payment_limit is not None:
            item["asp"].add(row.asp_payment_limit)
        if row.allowed_markup_multiple is not None:
            item["allowed_multiple"].append(row.allowed_markup_multiple)
        if row.paid_markup_multiple is not None:
            item["paid_multiple"].append(row.paid_markup_multiple)
    result = []
    for code, item in sorted(grouped.items()):
        allowed_unit = item["allowed"] / item["units"] if item["units"] else None
        paid_unit = item["paid"] / item["units"] if item["units"] else None
        asp = next(iter(item["asp"])) if len(item["asp"]) == 1 else None
        allowed_markup = allowed_unit / asp if asp else None
        paid_markup = paid_unit / asp if asp else None
        result.append({
            "hcpcs_code": code, "total_units": _decimal_text(item["units"]),
            "visible_allowed_amount": _decimal_text(item["allowed"]),
            "visible_paid_amount": _decimal_text(item["paid"]),
            "allowed_price_per_unit": _decimal_text(allowed_unit),
            "paid_price_per_unit": _decimal_text(paid_unit),
            "asp_payment_limit": _decimal_text(asp),
            "allowed_markup_multiple": _decimal_text(allowed_markup),
            "paid_markup_multiple": _decimal_text(paid_markup),
            "hospitals": len(item["sites"]), "quarters": sorted(item["quarters"]),
            "data_quality_status_counts": dict(item["qualities"]),
            "totals_scope": "visible_approved_aggregate_rows_only",
        })
    return Response({"audit_id": audit.pk, "results": result})


@api_view(["GET"])
@permission_classes([CanView])
def hospital_comparison(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    rows = audit.results.exclude(hospital_price_per_unit__isnull=True)
    return Response([{
        "hcpcs_code": row.hcpcs_code, "site_npi": row.site_npi,
        "quarter": row.quarter, "service_setting": row.service_setting,
        "plan_allowed_price_per_unit": _decimal_text(row.plan_allowed_price_per_unit),
        "hospital_reported_price_per_unit": _decimal_text(row.hospital_price_per_unit),
        "hospital_markup_multiple": _decimal_text(row.hospital_markup_multiple),
        "plan_to_hospital_price_multiple": _decimal_text(row.plan_to_hospital_price_multiple),
        "data_quality_status": row.data_quality_status,
        "source_references": row.source_references,
        "interpretation": "Hospital-reported reference price; not the employer plan's contracted rate.",
    } for row in rows[:2000]])


@api_view(["GET"])
@permission_classes([CanView])
def asp_benchmark(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    rows = audit.results.all().order_by("hcpcs_code", "quarter", "site_npi")
    return Response([{
        "hcpcs_code": row.hcpcs_code, "site_npi": row.site_npi,
        "quarter": row.quarter, "plan_allowed_price_per_unit": _decimal_text(row.plan_allowed_price_per_unit),
        "plan_paid_price_per_unit": _decimal_text(row.plan_paid_price_per_unit),
        "asp_payment_limit": _decimal_text(row.asp_payment_limit),
        "allowed_markup_multiple": _decimal_text(row.allowed_markup_multiple),
        "paid_markup_multiple": _decimal_text(row.paid_markup_multiple),
        "data_quality_status": row.data_quality_status,
        "source_references": row.source_references,
    } for row in rows[:2000]])


@api_view(["GET"])
@permission_classes([CanView])
def audit_overview(request, audit_id):
    audit = get_visible_audit(request.user, audit_id)
    return Response({"audit": audit_payload(audit), "metrics": _visible_totals(audit)})


# ---------------------------------------------------------------------------
# Historical spending analytics (Task B)
# ---------------------------------------------------------------------------

_ALLOWED_HCPCS_RE = __import__("re").compile(r"^[A-Z0-9]{5}$")
_ALLOWED_QUARTER_RE = __import__("re").compile(r"^20\d{2}Q[1-4]$")
_ALLOWED_NPI_RE = __import__("re").compile(r"^\d{10}$")
_ALLOWED_QUALITY_STATUSES = frozenset(["VALID", "UNIT_UNRESOLVED", "REFERENCE_NOT_FOUND", "NETWORK_UNVERIFIED"])


def _safe_filter(queryset, params):
    """Apply allow-listed query filters; silently ignore unrecognised or unsafe values."""
    hcpcs = str(params.get("hcpcs_code", "")).upper().strip()
    if hcpcs and _ALLOWED_HCPCS_RE.fullmatch(hcpcs):
        queryset = queryset.filter(hcpcs_code=hcpcs)
    quarter = str(params.get("quarter", "")).upper().strip()
    if quarter and _ALLOWED_QUARTER_RE.fullmatch(quarter):
        queryset = queryset.filter(quarter=quarter)
    site_npi = str(params.get("site_npi", "")).strip()
    if site_npi and _ALLOWED_NPI_RE.fullmatch(site_npi):
        queryset = queryset.filter(site_npi=site_npi)
    quality = str(params.get("data_quality_status", "")).upper().strip()
    if quality and quality in _ALLOWED_QUALITY_STATUSES:
        queryset = queryset.filter(data_quality_status=quality)
    service_setting = str(params.get("service_setting", "")).strip()
    if service_setting and len(service_setting) <= 80:
        queryset = queryset.filter(service_setting=service_setting)
    return queryset


@api_view(["GET"])
@permission_classes([CanView])
def historical_spending(request, audit_id):
    """
    Historical spending view for a completed plan audit.

    Returns:
    - summary: audit header + top-level metrics
    - by_quarter: allowed and paid totals grouped by quarter
    - by_hcpcs: spending totals grouped by HCPCS code
    - by_site: spending totals grouped by site NPI
    - by_setting: spending totals grouped by service setting
    - data_quality: quality status distribution
    - data_notes: standard interpretation notes

    Security:
    - Enforces org/plan/audit access via get_visible_audit (same chain as audit_results).
    - Returns only PlanAuditResult rows already stored from the approved import.
    - Suppressed cells are excluded because suppressed rows are never stored
      (ClaimsSummaryImport only stores accepted rows passing the extractor's suppression controls).
    - No raw claims, member IDs, or filesystem paths are exposed.
    - Filters are allow-listed; unrecognised values are silently ignored.
    - Decimal precision preserved throughout using Decimal arithmetic.
    """
    audit = get_visible_audit(request.user, audit_id)

    # Only completed audits have meaningful result rows.
    if audit.status not in {"completed", "needs_review"}:
        return Response({
            "audit_id": audit.pk,
            "available": False,
            "reason": "Run the audit to populate historical spending analytics.",
            "by_quarter": [],
            "by_hcpcs": [],
            "by_site": [],
            "by_setting": [],
            "data_quality": {},
            "data_notes": _spending_notes(),
        })

    queryset = audit.results.all()
    queryset = _safe_filter(queryset, request.query_params)

    # Aggregate across the filtered result set. All arithmetic uses Decimal.
    by_quarter = defaultdict(lambda: {"allowed": Decimal(0), "paid": Decimal(0), "units": Decimal(0), "rows": 0})
    by_hcpcs = defaultdict(lambda: {"allowed": Decimal(0), "paid": Decimal(0), "units": Decimal(0),
                                    "drug_description": "", "sites": set(), "quarters": set()})
    by_site = defaultdict(lambda: {"allowed": Decimal(0), "paid": Decimal(0), "units": Decimal(0),
                                   "hcpcs_set": set(), "network_statuses": set()})
    by_setting = defaultdict(lambda: {"allowed": Decimal(0), "paid": Decimal(0), "units": Decimal(0), "rows": 0})
    quality_counts = defaultdict(int)

    total_allowed = Decimal(0)
    total_paid = Decimal(0)
    total_units = Decimal(0)
    row_count = 0
    matched_benchmarks = 0
    unmatched_benchmarks = 0
    distinct_hcpcs = set()
    distinct_sites = set()

    for row in queryset.iterator(chunk_size=2000):
        row_count += 1
        total_allowed += row.total_allowed_amount
        total_paid += row.total_paid_amount
        total_units += row.total_units
        distinct_hcpcs.add(row.hcpcs_code)
        distinct_sites.add(row.site_npi)
        quality_counts[row.data_quality_status] += 1

        if row.asp_payment_limit is not None:
            matched_benchmarks += 1
        else:
            unmatched_benchmarks += 1

        q = by_quarter[row.quarter]
        q["allowed"] += row.total_allowed_amount
        q["paid"] += row.total_paid_amount
        q["units"] += row.total_units
        q["rows"] += 1

        h = by_hcpcs[row.hcpcs_code]
        h["allowed"] += row.total_allowed_amount
        h["paid"] += row.total_paid_amount
        h["units"] += row.total_units
        h["sites"].add(row.site_npi)
        h["quarters"].add(row.quarter)
        if not h["drug_description"] and row.drug_description:
            h["drug_description"] = row.drug_description

        s = by_site[row.site_npi]
        s["allowed"] += row.total_allowed_amount
        s["paid"] += row.total_paid_amount
        s["units"] += row.total_units
        s["hcpcs_set"].add(row.hcpcs_code)
        s["network_statuses"].add(row.network_status)

        g = by_setting[row.service_setting or "unknown"]
        g["allowed"] += row.total_allowed_amount
        g["paid"] += row.total_paid_amount
        g["units"] += row.total_units
        g["rows"] += 1

    if row_count == 0:
        return Response({
            "audit_id": audit.pk,
            "available": False,
            "reason": (
                "No audit results are available for the current filters. "
                "If no filters are applied, the audit may not have produced result rows — "
                "check the data quality endpoint."
            ),
            "by_quarter": [],
            "by_hcpcs": [],
            "by_site": [],
            "by_setting": [],
            "data_quality": {},
            "data_notes": _spending_notes(),
        })

    quarters_sorted = sorted(by_quarter.keys())
    by_quarter_list = [
        {
            "quarter": q,
            "total_allowed_amount": _decimal_text(by_quarter[q]["allowed"]),
            "total_paid_amount": _decimal_text(by_quarter[q]["paid"]),
            "total_units": _decimal_text(by_quarter[q]["units"]),
            "result_row_count": by_quarter[q]["rows"],
        }
        for q in quarters_sorted
    ]

    by_hcpcs_list = sorted(
        [
            {
                "hcpcs_code": code,
                "drug_description": item["drug_description"] or None,
                "total_allowed_amount": _decimal_text(item["allowed"]),
                "total_paid_amount": _decimal_text(item["paid"]),
                "total_units": _decimal_text(item["units"]),
                "site_count": len(item["sites"]),
                "quarter_count": len(item["quarters"]),
                "allowed_per_unit": _decimal_text(
                    item["allowed"] / item["units"] if item["units"] else None
                ),
                "paid_per_unit": _decimal_text(
                    item["paid"] / item["units"] if item["units"] else None
                ),
            }
            for code, item in by_hcpcs.items()
        ],
        key=lambda r: r["hcpcs_code"],
    )

    by_site_list = sorted(
        [
            {
                "site_npi": npi,
                "total_allowed_amount": _decimal_text(item["allowed"]),
                "total_paid_amount": _decimal_text(item["paid"]),
                "total_units": _decimal_text(item["units"]),
                "hcpcs_count": len(item["hcpcs_set"]),
                "network_status_summary": _resolve_network_summary(item["network_statuses"]),
            }
            for npi, item in by_site.items()
        ],
        key=lambda r: r["site_npi"],
    )

    by_setting_list = sorted(
        [
            {
                "service_setting": setting,
                "total_allowed_amount": _decimal_text(item["allowed"]),
                "total_paid_amount": _decimal_text(item["paid"]),
                "total_units": _decimal_text(item["units"]),
                "result_row_count": item["rows"],
            }
            for setting, item in by_setting.items()
        ],
        key=lambda r: r["service_setting"],
    )

    return Response({
        "audit_id": audit.pk,
        "audit_name": audit.audit_name,
        "organization_name": audit.organization.organization_name,
        "plan_name": audit.plan.plan_name,
        "reporting_period": audit.reporting_period,
        "audit_status": audit.status,
        "available": True,
        "summary": {
            "total_allowed_amount": _decimal_text(total_allowed),
            "total_paid_amount": _decimal_text(total_paid),
            "total_units": _decimal_text(total_units),
            "result_row_count": row_count,
            "distinct_hcpcs_count": len(distinct_hcpcs),
            "distinct_site_count": len(distinct_sites),
            "distinct_quarter_count": len(by_quarter),
            "matched_benchmark_rows": matched_benchmarks,
            "unmatched_benchmark_rows": unmatched_benchmarks,
            "totals_scope": "visible_approved_aggregate_rows_only",
        },
        "by_quarter": by_quarter_list,
        "by_hcpcs": by_hcpcs_list,
        "by_site": by_site_list,
        "by_setting": by_setting_list,
        "data_quality": dict(quality_counts),
        "data_notes": _spending_notes(),
    })


def _resolve_network_summary(statuses):
    """Summarise multiple network statuses for a site without fabricating coverage."""
    if not statuses:
        return "NETWORK_STATUS_UNKNOWN"
    if statuses == {"IN_NETWORK"}:
        return "IN_NETWORK"
    if statuses == {"OUT_OF_NETWORK"}:
        return "OUT_OF_NETWORK"
    return "MIXED_OR_UNKNOWN"


def _spending_notes():
    return [
        "Totals include only approved, unsuppressed aggregate rows from the selected audit.",
        "Suppressed cells (fewer than 11 distinct members) are excluded by the client extractor and are not stored.",
        "Complementary suppression is declared in the manifest and verified at import.",
        "Amounts are shown without member counts or claim-level detail to preserve privacy controls.",
        "Hospital price references are not this plan's contracted rates.",
        "Potential savings remain pending clinical review and network verification; they are not guarantees.",
        "Historical charts do not include synthetic research claims, ASP references, or hospital-published prices.",
    ]

