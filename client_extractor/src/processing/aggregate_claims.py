from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext


def quarter_for(service_date: date) -> str:
    return f"{service_date.year}Q{(service_date.month - 1) // 3 + 1}"


def aggregate_claims(claims, *, plan_reference, source_period, extracted_at, billing_units):
    with localcontext() as context:
        context.prec = 50
        return _aggregate_claims(
            claims, plan_reference=plan_reference, source_period=source_period,
            extracted_at=extracted_at, billing_units=billing_units,
        )


def _aggregate_claims(claims, *, plan_reference, source_period, extracted_at, billing_units):
    groups = defaultdict(lambda: {"units": Decimal(0), "allowed": Decimal(0),
                                  "paid": Decimal(0), "members": set()})
    quality = {
        "reasons": {},
        "excluded_records": 0,
        "suppressed_aggregate_cells": 0,
        "rejected_aggregate_cells": 0,
        "aggregate_cell_reasons": {},
    }
    for claim in claims:
        if claim["hcpcs_code"] not in billing_units:
            _reason(quality, "billing_unit_not_configured")
            continue
        key = (plan_reference, claim["hcpcs_code"], claim["site_npi"],
               quarter_for(claim["service_date"]), claim["service_setting"])
        group = groups[key]
        group["units"] += claim["units"]
        group["allowed"] += claim["allowed_amount"]
        group["paid"] += claim["paid_amount"]
        group["members"].add(claim["member_id"])

    rows, suppressed = [], set()
    rows_by_parent = defaultdict(list)
    for key, values in groups.items():
        if len(values["members"]) < 11:
            suppressed.add((key[0], key[1], key[3], key[4]))
            _cell_reason(quality, "small_cell_suppressed", suppressed=True)
            continue
        if not all(_fits_import_decimal(values[field]) for field in ("units", "allowed", "paid")):
            _cell_reason(quality, "aggregate_exceeds_import_precision")
            continue
        band = "11-50" if len(values["members"]) <= 50 else "51-100" if len(values["members"]) <= 100 else "100+"
        row = {
            "plan_reference": key[0], "hcpcs_code": key[1], "site_npi": key[2],
            "quarter": key[3], "service_setting": key[4],
            "total_units": format(values["units"], "f"),
            "total_allowed_amount": format(values["allowed"], "f"),
            "total_paid_amount": format(values["paid"], "f"),
            "member_count_band": band, "source_period": source_period,
            "extraction_timestamp": extracted_at, "schema_version": "1.0",
            "_member_count_for_local_suppression": len(values["members"]),
        }
        rows.append(row)
        rows_by_parent[(key[0], key[1], key[3], key[4])].append(row)

    # Hide one additional site cell where a sibling was suppressed, preventing
    # the hidden value from being derived as a remainder of visible cells.
    complementary = set()
    for parent in suppressed:
        siblings = sorted(rows_by_parent.get(parent, []),
                          key=lambda row: (row["_member_count_for_local_suppression"], row["site_npi"]))
        if siblings:
            complementary.add((parent, siblings[0]["site_npi"]))
    exported = []
    for row in rows:
        parent = (row["plan_reference"], row["hcpcs_code"], row["quarter"], row["service_setting"])
        if (parent, row["site_npi"]) in complementary:
            _cell_reason(quality, "complementary_suppressed", suppressed=True)
            continue
        row.pop("_member_count_for_local_suppression", None)
        exported.append(row)
    quality["exported_aggregate_rows"] = len(exported)
    quality["excluded_aggregate_cells"] = sum(quality["aggregate_cell_reasons"].values())
    return exported, quality


def _reason(quality, reason):
    quality["reasons"][reason] = quality["reasons"].get(reason, 0) + 1
    quality["excluded_records"] += 1


def _cell_reason(quality, reason, *, suppressed=False):
    reasons = quality["aggregate_cell_reasons"]
    reasons[reason] = reasons.get(reason, 0) + 1
    if suppressed:
        quality["suppressed_aggregate_cells"] += 1
    else:
        quality["rejected_aggregate_cells"] += 1


def _fits_import_decimal(value):
    normalized = value.normalize()
    scale = max(-normalized.as_tuple().exponent, 0)
    integer_digits = max(normalized.adjusted() + 1, 0) if value else 1
    return scale <= 6 and integer_digits <= 18
