"""Claims Explorer: read-only DuckDB views of synthetic claims and healthcare data.

Datasets:
1. CMS DE-SynPUF synthetic Medicare claims (2008-2010)
2. Synthea Synthetic Healthcare & EHR Claims Demo (1940-2026)

All synthetic. No real patient data (PHI). Direct identifiers scrubbed.
No financial comparisons to current reference pricing or employer plan savings are made here.
Access is strictly scoped to authorized organization membership.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import duckdb
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import AuditEvent, ClaimsExplorerDataset
from .permissions import CanAnalyze
from .plan_audit_common import is_admin, visible_organizations

logger = logging.getLogger(__name__)

DATA_DISCLAIMER = (
    "Synthetic CMS DE-SynPUF data 2008-2010. Not real patient data. "
    "No financial comparisons to current reference data are valid."
)

SYNTHEA_DISCLAIMER = (
    "Synthetic Synthea healthcare & EHR claims dataset. Not real patient data. "
    "Direct identifiers scrubbed. Does not contain adjudicated Part B infusion claims or HCPCS J-codes."
)

# Base path for normalized claims parquet files
def _claims_dir() -> Path:
    return settings.BASE_DIR.parent / "data" / "claims" / "normalized"


def _synthea_dir() -> Path:
    return settings.BASE_DIR.parent / "data" / "synthetic" / "synthea" / "parquet"


def _synthea_manifest_path() -> Path:
    return settings.BASE_DIR.parent / "data" / "synthetic" / "synthea" / "manifest.json"


def _synthea_quality_path() -> Path:
    return settings.BASE_DIR.parent / "data" / "synthetic" / "synthea" / "quality_report.json"


DATASETS = {
    "desynpuf_outpatient_claims_sample_1": {
        "label": "DE-SynPUF outpatient sample 1",
        "source_dataset": "desynpuf_outpatient_claims",
        "claims_file": "outpatient_claims.parquet",
        "lines_file": "outpatient_claim_lines.parquet",
        "claim_type": "outpatient",
        "category": "desynpuf",
        "reporting_period": "2008-2010",
    },
    "desynpuf_inpatient_claims_sample_1": {
        "label": "DE-SynPUF inpatient sample 1",
        "source_dataset": "desynpuf_inpatient_claims",
        "claims_file": "inpatient_claims.parquet",
        "lines_file": None,
        "claim_type": "inpatient",
        "category": "desynpuf",
        "reporting_period": "2008-2010",
    },
    "synthea_sample_data": {
        "label": "Synthea Synthetic EHR & Claims Demo",
        "source_dataset": "synthea_sample_data",
        "category": "synthetic_demo",
        "is_synthea": True,
        "reporting_period": "Historical Synthetic (1940-2026)",
    },
}

SYNTHEA_SUBTABLES = {
    "encounters": {
        "file": "encounters.parquet",
        "columns": [
            "Id", "START", "STOP", "PATIENT", "ORGANIZATION", "PROVIDER",
            "PAYER", "ENCOUNTERCLASS", "CODE", "DESCRIPTION",
            "BASE_ENCOUNTER_COST", "TOTAL_CLAIM_COST", "PAYER_COVERAGE",
        ],
        "sort_default": "START",
        "sortable": {"Id", "START", "STOP", "ENCOUNTERCLASS", "TOTAL_CLAIM_COST", "BASE_ENCOUNTER_COST"},
        "id_col": "Id",
        "date_col": "START",
        "searchable": ["DESCRIPTION", "ENCOUNTERCLASS", "CODE"],
    },
    "claims": {
        "file": "claims.parquet",
        "columns": [
            "Id", "PATIENTID", "PROVIDERID", "PRIMARYPATIENTINSURANCEID",
            "SERVICEDATE", "CURRENTILLNESSDATE", "STATUS1", "STATUS2",
            "OUTSTANDING1", "HEALTHCARECLAIMTYPEID1",
        ],
        "sort_default": "SERVICEDATE",
        "sortable": {"Id", "SERVICEDATE", "CURRENTILLNESSDATE", "STATUS1", "OUTSTANDING1"},
        "id_col": "Id",
        "date_col": "SERVICEDATE",
        "searchable": ["STATUS1", "STATUS2", "Id"],
    },
    "procedures": {
        "file": "procedures.parquet",
        "columns": [
            "START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM",
            "CODE", "DESCRIPTION", "BASE_COST", "REASONCODE",
        ],
        "sort_default": "START",
        "sortable": {"START", "CODE", "DESCRIPTION", "BASE_COST"},
        "id_col": "ENCOUNTER",
        "date_col": "START",
        "searchable": ["DESCRIPTION", "CODE", "SYSTEM"],
    },
    "medications": {
        "file": "medications.parquet",
        "columns": [
            "START", "STOP", "PATIENT", "PAYER", "ENCOUNTER",
            "CODE", "DESCRIPTION", "BASE_COST", "PAYER_COVERAGE",
            "DISPENSES", "TOTALCOST",
        ],
        "sort_default": "START",
        "sortable": {"START", "CODE", "DESCRIPTION", "BASE_COST", "TOTALCOST", "DISPENSES"},
        "id_col": "ENCOUNTER",
        "date_col": "START",
        "searchable": ["DESCRIPTION", "CODE"],
    },
    "claims_transactions": {
        "file": "claims_transactions.parquet",
        "columns": [
            "ID", "CLAIMID", "CHARGEID", "PATIENTID", "TYPE",
            "AMOUNT", "METHOD", "FROMDATE", "TODATE", "PROCEDURECODE",
            "UNITS", "PAYMENTS", "ADJUSTMENTS", "OUTSTANDING",
        ],
        "sort_default": "FROMDATE",
        "sortable": {"ID", "CLAIMID", "FROMDATE", "AMOUNT", "PAYMENTS"},
        "id_col": "ID",
        "date_col": "FROMDATE",
        "searchable": ["TYPE", "METHOD", "CLAIMID"],
    },
    "providers": {
        "file": "providers.parquet",
        "columns": [
            "Id", "ORGANIZATION", "NAME", "GENDER", "SPECIALITY",
            "CITY", "STATE", "ZIP", "ENCOUNTERS", "PROCEDURES", "NPI",
        ],
        "sort_default": "NAME",
        "sortable": {"Id", "NAME", "SPECIALITY", "CITY", "NPI", "ENCOUNTERS"},
        "id_col": "Id",
        "date_col": None,
        "searchable": ["NAME", "SPECIALITY", "CITY", "NPI"],
    },
    "organizations": {
        "file": "organizations.parquet",
        "columns": [
            "Id", "NAME", "ADDRESS", "CITY", "STATE", "ZIP",
            "REVENUE", "UTILIZATION", "NPI",
        ],
        "sort_default": "NAME",
        "sortable": {"Id", "NAME", "CITY", "NPI", "REVENUE", "UTILIZATION"},
        "id_col": "Id",
        "date_col": None,
        "searchable": ["NAME", "CITY", "NPI"],
    },
    "patients": {
        "file": "patients.parquet",
        "columns": [
            "Id", "BIRTHDATE", "DEATHDATE", "MARITAL", "RACE",
            "ETHNICITY", "GENDER", "CITY", "STATE", "COUNTY",
            "HEALTHCARE_EXPENSES", "HEALTHCARE_COVERAGE", "INCOME",
        ],
        "sort_default": "BIRTHDATE",
        "sortable": {"Id", "BIRTHDATE", "GENDER", "CITY", "HEALTHCARE_EXPENSES", "INCOME"},
        "id_col": "Id",
        "date_col": "BIRTHDATE",
        "searchable": ["CITY", "COUNTY", "GENDER", "RACE"],
    },
}

# Outpatient/inpatient claims columns (from actual schema inspection)
EXPOSED_COLUMNS = {
    "outpatient": [
        "claim_id", "claim_type", "service_from_date", "service_to_date",
        "provider_id", "attending_npi", "paid_amount",
        "primary_payer_paid_amount", "utilization_days",
    ],
    "inpatient": [
        "claim_id", "claim_type", "service_from_date", "service_to_date",
        "provider_id", "attending_npi", "paid_amount",
        "primary_payer_paid_amount", "utilization_days",
    ],
}

EXPOSED_LINE_COLUMNS = (
    "line_id", "service_date", "hcpcs", "submitted_units", "billed_amount",
    "allowed_amount", "paid_amount", "place_of_service",
)

# Sortable columns (must be in EXPOSED_COLUMNS)
_SORTABLE = {
    "claim_id", "service_from_date", "service_to_date",
    "paid_amount", "primary_payer_paid_amount", "utilization_days",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ORG_ID_RE = re.compile(r"^[0-9]{1,18}$")
_CLAIM_ID_RE = re.compile(r"^[\w\-]{1,64}$")
_HCPCS_RE = re.compile(r"^[A-Za-z0-9]{1,7}$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9\-]{1,32}$")
_CLAIM_TYPE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_SAFE_SEARCH_RE = re.compile(r"^[A-Za-z0-9\s\-_.,/()]{1,100}$")


def _validate_date(value: str, field: str) -> str | None:
    """Return the date string if valid YYYY-MM-DD, else None."""
    if not value or not _DATE_RE.match(value):
        return None
    return value


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _resolve_authorized_organization(request):
    parameters = request.data if request.method == "POST" else request.query_params
    org_id = parameters.get("organization_id")
    if not isinstance(org_id, (str, int)) or not _ORG_ID_RE.fullmatch(str(org_id)):
        return None, Response({"error": "A valid organization_id is required."}, status=400)
    organization = visible_organizations(request.user).filter(pk=int(org_id)).first()
    if organization is None:
        return None, Response({"error": "Organization not found or access denied."}, status=403)
    return organization, None


def _registered_dataset(organization, identifier):
    if identifier not in DATASETS:
        return None
    return ClaimsExplorerDataset.objects.filter(
        organization=organization, dataset_identifier=identifier,
    ).first()


@api_view(["GET", "POST"])
@permission_classes([CanAnalyze])
def claims_datasets(request):
    """List or explicitly register allowlisted claims datasets for one organization."""
    organization, error = _resolve_authorized_organization(request)
    if error:
        return error

    if request.method == "POST":
        if not is_admin(request.user):
            return Response({"error": "Only an Admin can register a claims dataset."}, status=403)
        identifier = request.data.get("dataset_identifier")
        if not isinstance(identifier, str) or identifier not in DATASETS:
            return Response({"error": "Select a supported claims dataset identifier."}, status=400)
        
        category = DATASETS[identifier].get("category", "desynpuf")
        registration, created = ClaimsExplorerDataset.objects.get_or_create(
            organization=organization,
            dataset_identifier=identifier,
            defaults={"registered_by": request.user, "category": category},
        )
        if created:
            AuditEvent.objects.create(
                actor=request.user,
                action="claims_explorer_dataset_registered",
                entity_type="organization",
                entity_id=str(organization.pk),
                details={"dataset_identifier": identifier, "category": category},
            )
        return Response({
            "organization_id": organization.pk,
            "dataset_identifier": registration.dataset_identifier,
            "category": registration.category,
            "registered": True,
            "created": created,
        }, status=201 if created else 200)

    registrations = ClaimsExplorerDataset.objects.filter(organization=organization)
    return Response({
        "organization_id": organization.pk,
        "datasets": [{
            "dataset_identifier": item.dataset_identifier,
            "label": DATASETS[item.dataset_identifier]["label"],
            "category": getattr(item, "category", DATASETS[item.dataset_identifier].get("category", "desynpuf")),
            "reporting_period": DATASETS[item.dataset_identifier].get("reporting_period", "2008-2010"),
            "synthetic": True,
        } for item in registrations if item.dataset_identifier in DATASETS],
        "available_datasets": [{
            "dataset_identifier": identifier,
            "label": config["label"],
            "category": config.get("category", "desynpuf"),
            "reporting_period": config.get("reporting_period", ""),
        } for identifier, config in DATASETS.items()] if is_admin(request.user) else [],
        "data_disclaimer": DATA_DISCLAIMER,
    })


@api_view(["GET"])
@permission_classes([CanAnalyze])
def claims_synthea_summary(request):
    """Return analytical overview, summary KPIs, quality audit, and data boundaries for Synthea."""
    org, error = _resolve_authorized_organization(request)
    if error:
        return error

    if not _registered_dataset(org, "synthea_sample_data"):
        return Response({"error": "Synthea dataset is not registered for the selected organization."}, status=403)

    manifest_path = _synthea_manifest_path()
    quality_path = _synthea_quality_path()

    if not manifest_path.is_file():
        return Response({
            "error": "Synthea manifest is unavailable. Run the Synthea conversion pipeline.",
            "data_disclaimer": SYNTHEA_DISCLAIMER,
        }, status=503)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        quality = {}
        if quality_path.is_file():
            with open(quality_path, "r", encoding="utf-8") as f:
                quality = json.load(f)
    except Exception:
        logger.exception("Failed to load Synthea metadata files")
        return Response({"error": "Failed to load Synthea metadata."}, status=500)

    return Response({
        "organization_id": org.pk,
        "organization_name": org.organization_name,
        "dataset_identifier": "synthea_sample_data",
        "display_name": manifest.get("display_name", "Synthea Synthetic EHR & Claims Demo"),
        "category": "synthetic_demo",
        "created_at": manifest.get("created_at"),
        "total_tables": manifest.get("total_tables", 18),
        "total_rows": manifest.get("total_rows", 0),
        "summary_metrics": manifest.get("summary_metrics", {}),
        "data_boundaries": manifest.get("data_boundaries", {}),
        "quality_report": quality,
        "tables_summary": [{
            "table": name,
            "rows": meta.get("row_count", 0),
            "columns": meta.get("column_count", 0),
            "size_bytes": meta.get("size_bytes", 0),
            "sha256": meta.get("sha256", ""),
        } for name, meta in sorted(manifest.get("tables", {}).items())],
        "available_subtables": list(SYNTHEA_SUBTABLES.keys()),
        "data_disclaimer": SYNTHEA_DISCLAIMER,
    })


@api_view(["GET"])
@permission_classes([CanAnalyze])
def claims_list(request):
    """Paginated, filterable list of claims/records from Parquet files."""
    org, error = _resolve_authorized_organization(request)
    if error:
        return error

    dataset = request.query_params.get("dataset", "").strip()
    dataset_config = DATASETS.get(dataset)
    if dataset_config is None:
        return Response({"error": "A supported dataset identifier is required."}, status=400)
    if not _registered_dataset(org, dataset):
        return Response({"error": "This dataset is not registered for the selected organization."}, status=403)

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        return Response({"error": "page must be a whole number."}, status=400)

    try:
        page_size = min(200, max(1, int(request.query_params.get("page_size", 50))))
    except (TypeError, ValueError):
        return Response({"error": "page_size must be a whole number."}, status=400)

    # -------------------------------------------------------------
    # Synthea Synthetic Dataset Handling
    # -------------------------------------------------------------
    if dataset == "synthea_sample_data":
        subtable = request.query_params.get("subtable", "encounters").strip().lower()
        if subtable not in SYNTHEA_SUBTABLES:
            subtable = "encounters"
        table_config = SYNTHEA_SUBTABLES[subtable]

        parquet_path = _synthea_dir() / table_config["file"]
        if not parquet_path.is_file():
            return Response({
                "error": f"Synthea table '{subtable}' is unavailable.",
                "data_disclaimer": SYNTHEA_DISCLAIMER,
            }, status=503)

        exposed_cols = table_config["columns"]
        select_expr = ", ".join(f"cast({_quote(c)} as VARCHAR) as {_quote(c)}" for c in exposed_cols)

        filters = []
        params = [parquet_path.as_posix()]

        search = request.query_params.get("search", "").strip()
        if search:
            if not _SAFE_SEARCH_RE.match(search):
                return Response({"error": "search filter contains invalid characters."}, status=400)
            searchable_fields = table_config.get("searchable", [])
            if searchable_fields:
                search_clauses = [f"cast({_quote(f)} as VARCHAR) ILIKE ?" for f in searchable_fields]
                filters.append(f"({' OR '.join(search_clauses)})")
                params.extend([f"%{search}%"] * len(searchable_fields))

        raw_date_from = request.query_params.get("service_date_from", "").strip()
        raw_date_to = request.query_params.get("service_date_to", "").strip()
        date_from = _validate_date(raw_date_from, "service_date_from")
        date_to = _validate_date(raw_date_to, "service_date_to")

        date_col = table_config.get("date_col")
        if date_col:
            if date_from:
                filters.append(f"cast({_quote(date_col)} as VARCHAR) >= ?")
                params.append(date_from)
            if date_to:
                filters.append(f"cast({_quote(date_col)} as VARCHAR) <= ?")
                params.append(date_to + "T23:59:59")

        sort_field = request.query_params.get("sort", table_config["sort_default"]).strip()
        if sort_field not in table_config["sortable"]:
            sort_field = table_config["sort_default"]
        sort_dir = request.query_params.get("sort_dir", "desc" if date_col else "asc").strip().lower()
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc" if date_col else "asc"

        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
        sort_expr = f"{_quote(sort_field)} {sort_dir.upper()} NULLS LAST"
        offset = (page - 1) * page_size

        count_params = list(params)
        data_params = list(params) + [page_size, offset]

        try:
            con = duckdb.connect()
            try:
                total = con.execute(
                    f"SELECT count(*) FROM read_parquet(?) {where_clause}",
                    count_params,
                ).fetchone()[0]

                cur = con.execute(
                    f"SELECT {select_expr} FROM read_parquet(?) {where_clause} "
                    f"ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                    data_params,
                )
                col_names = [d[0] for d in cur.description]
                rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
            finally:
                con.close()
        except Exception:
            logger.exception("Synthea table query failed for %s", subtable)
            return Response({
                "error": "Failed to query Synthea synthetic dataset.",
                "data_disclaimer": SYNTHEA_DISCLAIMER,
            }, status=503)

        return Response({
            "organization_id": org.pk,
            "organization_name": org.organization_name,
            "dataset": "synthea_sample_data",
            "dataset_label": "Synthea Synthetic EHR & Claims Demo",
            "category": "synthetic_demo",
            "subtable": subtable,
            "available_subtables": list(SYNTHEA_SUBTABLES.keys()),
            "total": total,
            "page": page,
            "page_size": page_size,
            "columns": col_names,
            "rows": rows,
            "data_disclaimer": SYNTHEA_DISCLAIMER,
        })

    # -------------------------------------------------------------
    # CMS DE-SynPUF Dataset Handling
    # -------------------------------------------------------------
    claim_type = dataset_config["claim_type"]
    parquet_path = _claims_dir() / dataset_config["claims_file"]
    if not parquet_path.is_file():
        return Response({
            "error": "The selected claims dataset is currently unavailable.",
            "data_disclaimer": DATA_DISCLAIMER,
        }, status=503)

    claim_id_filter = request.query_params.get("claim_id", "").strip()
    if claim_id_filter and not _CLAIM_ID_RE.match(claim_id_filter):
        return Response({"error": "claim_id contains invalid characters."}, status=400)

    raw_date_from = request.query_params.get("service_date_from", "").strip()
    raw_date_to = request.query_params.get("service_date_to", "").strip()
    date_from = _validate_date(raw_date_from, "service_date_from")
    date_to = _validate_date(raw_date_to, "service_date_to")
    if raw_date_from and not date_from:
        return Response({"error": "service_date_from must be YYYY-MM-DD."}, status=400)
    if raw_date_to and not date_to:
        return Response({"error": "service_date_to must be YYYY-MM-DD."}, status=400)
    if date_from and date_to and date_from > date_to:
        return Response({"error": "service_date_from must not be after service_date_to."}, status=400)

    hcpcs_filter = request.query_params.get("hcpcs", "").strip().upper()
    if hcpcs_filter and not _HCPCS_RE.match(hcpcs_filter):
        return Response({"error": "hcpcs contains invalid characters."}, status=400)

    provider_filter = request.query_params.get("provider", "").strip()
    if provider_filter and not _PROVIDER_RE.match(provider_filter):
        return Response({"error": "provider contains invalid characters."}, status=400)

    claim_type_filter = request.query_params.get("claim_type", "").strip()
    if claim_type_filter and not _CLAIM_TYPE_RE.match(claim_type_filter):
        return Response({"error": "claim_type contains invalid characters."}, status=400)

    sort_field = request.query_params.get("sort", "claim_id").strip()
    if sort_field not in _SORTABLE:
        sort_field = "claim_id"
    sort_dir = request.query_params.get("sort_dir", "asc").strip().lower()
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    exposed_cols = EXPOSED_COLUMNS[claim_type]
    select_expr = ", ".join(_quote(c) for c in exposed_cols)

    filters = [f"{_quote('source_dataset')} = ?", f"{_quote('claim_type')} = ?"]
    params = [parquet_path.as_posix(), dataset_config["source_dataset"], claim_type]

    if claim_id_filter:
        filters.append(f"{_quote('claim_id')} ILIKE ?")
        params.append(f"%{claim_id_filter}%")
    if date_from:
        filters.append(f"{_quote('service_from_date')} >= ?")
        params.append(date_from)
    if date_to:
        filters.append(f"{_quote('service_to_date')} <= ?")
        params.append(date_to)
    if provider_filter:
        filters.append(f"({_quote('provider_id')} ILIKE ? OR {_quote('attending_npi')} ILIKE ?)")
        params.extend([f"%{provider_filter}%", f"%{provider_filter}%"])
    if claim_type_filter:
        filters.append(f"{_quote('claim_type')} = ?")
        params.append(claim_type_filter)
    if hcpcs_filter:
        lines_file = dataset_config["lines_file"]
        lines_path = _claims_dir() / lines_file if lines_file else None
        if lines_path and lines_path.is_file():
            filters.append(
                f"{_quote('claim_id')} IN ("
                f"SELECT DISTINCT {_quote('claim_id')} FROM read_parquet(?) "
                f"WHERE {_quote('source_dataset')} = ? AND {_quote('hcpcs')} = ?"
                f")"
            )
            params.extend([lines_path.as_posix(), dataset_config["source_dataset"], hcpcs_filter])
        else:
            filters.append("1 = 0")

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    sort_expr = f"{_quote(sort_field)} {sort_dir.upper()} NULLS LAST"
    if sort_field != "claim_id":
        sort_expr += f", {_quote('claim_id')} ASC"
    offset = (page - 1) * page_size

    count_params = list(params)
    data_params = list(params) + [page_size, offset]

    try:
        con = duckdb.connect()
        try:
            total = con.execute(
                f"SELECT count(*) FROM read_parquet(?) {where_clause}",
                count_params,
            ).fetchone()[0]

            cur = con.execute(
                f"SELECT {select_expr} FROM read_parquet(?) {where_clause} "
                f"ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                data_params,
            )
            col_names = [d[0] for d in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]

            # Serialize non-JSON-safe types
            for row in rows:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif isinstance(v, float) and not math.isfinite(v):
                        row[k] = None
        finally:
            con.close()
    except Exception:
        logger.exception("Claims list query failed")
        return Response({
            "error": "Failed to query claims dataset.",
            "data_disclaimer": DATA_DISCLAIMER,
        }, status=503)

    return Response({
        "organization_id": org.pk,
        "organization_name": org.organization_name,
        "dataset": dataset,
        "total": total,
        "page": page,
        "page_size": page_size,
        "columns": col_names,
        "rows": rows,
        "data_disclaimer": DATA_DISCLAIMER,
    })


@api_view(["GET"])
@permission_classes([CanAnalyze])
def claims_detail(request, claim_id: str):
    """Return allowlisted header and line detail within an authorized dataset."""
    org, error = _resolve_authorized_organization(request)
    if error:
        return error
    if not _CLAIM_ID_RE.match(claim_id):
        return Response({"error": "Invalid claim_id format."}, status=400)

    dataset = request.query_params.get("dataset", "").strip()
    dataset_config = DATASETS.get(dataset)
    if dataset_config is None:
        return Response({"error": "A supported dataset identifier is required."}, status=400)
    if not _registered_dataset(org, dataset):
        return Response({"error": "This dataset is not registered for the selected organization."}, status=403)

    # -------------------------------------------------------------
    # Synthea Detail Lookup
    # -------------------------------------------------------------
    if dataset == "synthea_sample_data":
        enc_path = _synthea_dir() / "encounters.parquet"
        proc_path = _synthea_dir() / "procedures.parquet"
        med_path = _synthea_dir() / "medications.parquet"
        claims_path = _synthea_dir() / "claims.parquet"
        tx_path = _synthea_dir() / "claims_transactions.parquet"

        if not enc_path.is_file():
            return Response({
                "error": "Synthea dataset is unavailable.",
                "data_disclaimer": SYNTHEA_DISCLAIMER,
            }, status=503)

        try:
            con = duckdb.connect()
            try:
                # 1. Try encounters
                enc_cols = SYNTHEA_SUBTABLES["encounters"]["columns"]
                enc_expr = ", ".join(f"cast({_quote(c)} as VARCHAR) as {_quote(c)}" for c in enc_cols)
                enc_cur = con.execute(
                    f"SELECT {enc_expr} FROM read_parquet(?) WHERE {_quote('Id')} = ? LIMIT 1",
                    [enc_path.as_posix(), claim_id],
                )
                enc_rows = enc_cur.fetchall()

                if enc_rows:
                    encounter = dict(zip(enc_cols, enc_rows[0]))
                    # Get associated procedures
                    proc_cur = con.execute(
                        f"SELECT cast({_quote('CODE')} as VARCHAR) as code, "
                        f"cast({_quote('DESCRIPTION')} as VARCHAR) as description, "
                        f"cast({_quote('BASE_COST')} as VARCHAR) as base_cost, "
                        f"cast({_quote('START')} as VARCHAR) as start_date "
                        f"FROM read_parquet(?) WHERE {_quote('ENCOUNTER')} = ? LIMIT 50",
                        [proc_path.as_posix(), claim_id],
                    )
                    procs = [dict(zip(["code", "description", "base_cost", "start_date"], r)) for r in proc_cur.fetchall()]

                    # Get associated medications
                    med_cur = con.execute(
                        f"SELECT cast({_quote('CODE')} as VARCHAR) as code, "
                        f"cast({_quote('DESCRIPTION')} as VARCHAR) as description, "
                        f"cast({_quote('TOTALCOST')} as VARCHAR) as total_cost, "
                        f"cast({_quote('DISPENSES')} as VARCHAR) as dispenses, "
                        f"cast({_quote('START')} as VARCHAR) as start_date "
                        f"FROM read_parquet(?) WHERE {_quote('ENCOUNTER')} = ? LIMIT 50",
                        [med_path.as_posix(), claim_id],
                    )
                    meds = [dict(zip(["code", "description", "total_cost", "dispenses", "start_date"], r)) for r in med_cur.fetchall()]

                    return Response({
                        "organization_id": org.pk,
                        "organization_name": org.organization_name,
                        "dataset": "synthea_sample_data",
                        "record_type": "encounter",
                        "encounter": encounter,
                        "procedures": procs,
                        "medications": meds,
                        "data_disclaimer": SYNTHEA_DISCLAIMER,
                    })

                # 2. Try claims
                if claims_path.is_file():
                    claim_cols = SYNTHEA_SUBTABLES["claims"]["columns"]
                    claim_expr = ", ".join(f"cast({_quote(c)} as VARCHAR) as {_quote(c)}" for c in claim_cols)
                    claim_cur = con.execute(
                        f"SELECT {claim_expr} FROM read_parquet(?) WHERE {_quote('Id')} = ? LIMIT 1",
                        [claims_path.as_posix(), claim_id],
                    )
                    c_rows = claim_cur.fetchall()
                    if c_rows:
                        claim_record = dict(zip(claim_cols, c_rows[0]))
                        txs = []
                        if tx_path.is_file():
                            tx_cur = con.execute(
                                f"SELECT cast({_quote('ID')} as VARCHAR) as id, "
                                f"cast({_quote('TYPE')} as VARCHAR) as type, "
                                f"cast({_quote('AMOUNT')} as VARCHAR) as amount, "
                                f"cast({_quote('FROMDATE')} as VARCHAR) as from_date, "
                                f"cast({_quote('PROCEDURECODE')} as VARCHAR) as procedure_code "
                                f"FROM read_parquet(?) WHERE {_quote('CLAIMID')} = ? LIMIT 50",
                                [tx_path.as_posix(), claim_id],
                            )
                            txs = [dict(zip(["id", "type", "amount", "from_date", "procedure_code"], r)) for r in tx_cur.fetchall()]

                        return Response({
                            "organization_id": org.pk,
                            "organization_name": org.organization_name,
                            "dataset": "synthea_sample_data",
                            "record_type": "claim",
                            "claim": claim_record,
                            "transactions": txs,
                            "data_disclaimer": SYNTHEA_DISCLAIMER,
                        })

                return Response({
                    "error": "Record not found.",
                    "data_disclaimer": SYNTHEA_DISCLAIMER,
                }, status=404)
            finally:
                con.close()
        except Exception:
            logger.exception("Synthea detail query failed for %s", claim_id)
            return Response({
                "error": "Failed to retrieve Synthea record details.",
                "data_disclaimer": SYNTHEA_DISCLAIMER,
            }, status=503)

    # -------------------------------------------------------------
    # CMS DE-SynPUF Detail Lookup
    # -------------------------------------------------------------
    claim_type = dataset_config["claim_type"]
    header_path = _claims_dir() / dataset_config["claims_file"]
    lines_file = dataset_config["lines_file"]
    lines_path = _claims_dir() / lines_file if lines_file else None

    if not header_path.is_file():
        return Response({
            "error": "The selected claims dataset is currently unavailable.",
            "data_disclaimer": DATA_DISCLAIMER,
        }, status=503)

    header_columns = EXPOSED_COLUMNS[claim_type]
    header_expr = ", ".join(_quote(column) for column in header_columns)
    try:
        con = duckdb.connect()
        try:
            header_rows = con.execute(
                f"SELECT {header_expr} FROM read_parquet(?) "
                f"WHERE {_quote('source_dataset')} = ? AND {_quote('claim_type')} = ? "
                f"AND {_quote('claim_id')} = ? LIMIT 1",
                [header_path.as_posix(), dataset_config["source_dataset"], claim_type, claim_id],
            ).fetchall()
            if not header_rows:
                return Response({
                    "error": "Claim not found.",
                    "data_disclaimer": DATA_DISCLAIMER,
                }, status=404)

            header = dict(zip(header_columns, header_rows[0]))

            lines = []
            if lines_path and lines_path.is_file():
                line_expr = ", ".join(_quote(column) for column in EXPOSED_LINE_COLUMNS)
                cur = con.execute(
                    f"SELECT {line_expr} FROM read_parquet(?) "
                    f"WHERE {_quote('source_dataset')} = ? AND {_quote('claim_id')} = ? "
                    f"ORDER BY {_quote('line_id')}",
                    [lines_path.as_posix(), dataset_config["source_dataset"], claim_id],
                )
                line_cols = [d[0] for d in cur.description]
                lines = [dict(zip(line_cols, row)) for row in cur.fetchall()]

            for row in [header] + lines:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif isinstance(v, float) and not math.isfinite(v):
                        row[k] = None
        finally:
            con.close()
    except Exception:
        logger.exception("Claims detail query failed for %s", claim_id)
        return Response({
            "error": "Failed to retrieve claim details.",
            "data_disclaimer": DATA_DISCLAIMER,
        }, status=503)

    return Response({
        "organization_id": org.pk,
        "organization_name": org.organization_name,
        "dataset": dataset,
        "claim": header,
        "lines": lines,
        "data_disclaimer": DATA_DISCLAIMER,
    })
