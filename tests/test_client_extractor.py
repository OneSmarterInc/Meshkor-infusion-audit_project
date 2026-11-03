"""Local extractor tests use isolated, explicitly fictional TEST claims only."""
import csv
import hashlib
import json
from argparse import Namespace

import pytest
import yaml
from openpyxl import Workbook

from client_extractor.src.cli import extract_claims
from client_extractor.src.processing.aggregate_claims import aggregate_claims
from client_extractor.src.processing.resolve_adjustments import resolve_claims
from client_extractor.src.readers import read_claims
from client_extractor.src.validation.claims_validator import validate_claim


def _raw_claim(claim_id, member_id, status="paid", **overrides):
    row = {
        "claim_id": claim_id, "service_date": "2026-01-15", "hcpcs_code": "J9271",
        "site_npi": "1000000004", "units": "1", "allowed_amount": "100.25",
        "paid_amount": "80.20", "member_id": member_id, "claim_status": status,
        "plan_id": "PLAN-TEST", "adjustment_reference": "", "reversal_reference": "",
        "service_setting": "outpatient",
    }
    row.update(overrides)
    return row


def _write_source(path, rows):
    if path.suffix == ".csv":
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(rows[0]))
    for row in rows:
        sheet.append(list(row.values()))
    workbook.save(path)


def test_csv_xlsx_and_xlsm_readers_accept_mapped_headers_and_preserve_identifiers(tmp_path):
    mapping = {"claim_id": "Claim Number", "member_id": "Member Number"}
    row = _raw_claim("00000123", "00004567")
    row["Claim Number"] = row.pop("claim_id")
    row["Member Number"] = row.pop("member_id")
    for suffix in (".csv", ".xlsx", ".xlsm"):
        source = tmp_path / ("customer_export" + suffix)
        _write_source(source, [row])
        read_rows = list(read_claims(source, mapping))
        assert len(read_rows) == 1
        assert read_rows[0][1]["Claim Number"] == "00000123"
        assert read_rows[0][1]["Member Number"] == "00004567"


def test_extractor_exports_only_suppressed_aggregates_and_source_provenance(tmp_path):
    claims_path = tmp_path / "customer_export.csv"
    rows = []
    for index in range(11):
        row = _raw_claim(f"claim-{index:03}", f"member-{index:03}")
        row["Claim Number"] = row.pop("claim_id")
        row["Member Number"] = row.pop("member_id")
        rows.append(row)
    _write_source(claims_path, rows)

    config_path = tmp_path / "client_config.yaml"
    config_path.write_text(yaml.safe_dump({
        "plan_id": "PLAN-TEST", "plan_reference": "TEST-PLAN",
        "column_mapping": {"claim_id": "Claim Number", "member_id": "Member Number"},
        "hcpcs_billing_units": {
            "J9271": {"qty": 1, "unit": "MG", "verification_status": "validated",
                      "source_reference": "TEST-only unit rule",
                      "source_sha256": "a" * 64},
        },
    }), encoding="utf-8")
    output = tmp_path / "output"
    assert extract_claims(Namespace(claims=claims_path, config=config_path, output=output)) == 0

    summary = (output / "claims_summary.csv").read_bytes()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(summary).hexdigest()
    assert manifest["source_provenance"] == {
        "source_sha256": hashlib.sha256(claims_path.read_bytes()).hexdigest(),
        "source_format": "csv", "input_records": 11,
        "source_period": "2026-01-15/2026-01-15", "plan_reference": "TEST-PLAN",
    }
    assert b"claim-000" not in summary and b"member-000" not in summary
    quality = (output / "data_quality.json").read_text(encoding="utf-8")
    assert "claim-000" not in quality and "member-000" not in quality


def test_adjustment_reversal_and_denied_rows_are_not_double_counted():
    original = validate_claim(_raw_claim("original", "member-1"), 2)
    adjustment = validate_claim(_raw_claim(
        "adjustment", "member-1", "adjusted", adjustment_reference="original",
        allowed_amount="125", paid_amount="100",
    ), 3)
    reversal = validate_claim(_raw_claim(
        "reversal", "member-1", "reversed", reversal_reference="adjustment",
    ), 4)
    denied = validate_claim(_raw_claim("denied", "member-2", "denied"), 5)
    quality = {"adjusted_records": 0, "reversed_records": 0, "excluded_records": 0, "reasons": {}}
    assert resolve_claims([original, adjustment, reversal, denied], quality) == []
    assert quality["adjusted_records"] == 1
    assert quality["reversed_records"] == 2
    assert quality["reasons"]["denied_claim"] == 1


def test_identifier_and_amount_validation_rejects_unrepresentable_values():
    row = _raw_claim("claim-1", "member-1", allowed_amount="1.0000001")
    try:
        validate_claim(row, 2)
    except ValueError as error:
        assert "18 integer and 6 decimal digits" in str(error)
    else:
        raise AssertionError("Amount precision beyond the central import schema was accepted.")

    row = _raw_claim("claim-1", "member-1", service_date="2026-01-15not-a-date")
    try:
        validate_claim(row, 2)
    except ValueError as error:
        assert "ISO date" in str(error)
    else:
        raise AssertionError("A service date with trailing invalid text was accepted.")


def test_edi_837_file_is_explicitly_disabled(tmp_path):
    edi_path = tmp_path / "claims.837"
    edi_path.write_text("ISA*00*          *00*          *ZZ*SUBMITTER      *ZZ*RECEIVER       *260101*1200*^*00501*000000001*0*P*:~", encoding="utf-8")
    with pytest.raises(NotImplementedError) as exc_info:
        list(read_claims(edi_path))
    assert "837P/837I parsing is not enabled" in str(exc_info.value)


def test_primary_and_complementary_suppression_rules():
    """
    Verifies:
    1. Primary cell suppression for < 11 members.
    2. Complementary cell suppression when a sibling group has a suppressed cell.
    """
    claims = []
    # Site 1: 15 distinct members (passes primary threshold >= 11)
    for i in range(15):
        claims.append(validate_claim(_raw_claim(f"c1-{i}", f"m1-{i}", site_npi="1000000004"), i + 2))
    # Site 2: 12 distinct members (passes primary threshold >= 11)
    for i in range(12):
        claims.append(validate_claim(_raw_claim(f"c2-{i}", f"m2-{i}", site_npi="1000000012"), i + 20))
    # Site 3: 5 distinct members (< 11, triggers primary suppression)
    for i in range(5):
        claims.append(validate_claim(_raw_claim(f"c3-{i}", f"m3-{i}", site_npi="1000000020"), i + 40))

    units_config = {
        "J9271": {"qty": 1, "unit": "MG", "verification_status": "validated",
                  "source_reference": "Ref", "source_sha256": "0" * 64}
    }
    exported, quality = aggregate_claims(
        claims, plan_reference="TEST-PLAN", source_period="2026-01-01/2026-03-31",
        extracted_at="2026-01-15T00:00:00Z", billing_units=units_config,
    )
    # Site 3 is suppressed by primary suppression (< 11).
    # Since Site 3 is suppressed, complementary suppression suppresses the smallest sibling cell (Site 2 with 12 members).
    # Thus only Site 1 (15 members) is exported.
    assert len(exported) == 1
    assert exported[0]["site_npi"] == "1000000004"
    assert quality["reasons"]["small_cell_suppressed"] == 1
    assert quality["reasons"]["complementary_suppressed"] == 1


def test_missing_billing_unit_excludes_claim_from_export():
    claims = [validate_claim(_raw_claim(f"c-{i}", f"m-{i}", hcpcs_code="J9999"), i + 2) for i in range(15)]
    # Config has no J9999
    units_config = {
        "J9271": {"qty": 1, "unit": "MG", "verification_status": "validated", "source_reference": "Ref", "source_sha256": "0" * 64}
    }
    exported, quality = aggregate_claims(
        claims, plan_reference="TEST-PLAN", source_period="2026-01-01/2026-03-31",
        extracted_at="2026-01-15T00:00:00Z", billing_units=units_config,
    )
    assert len(exported) == 0
    assert quality["reasons"]["billing_unit_not_configured"] == 15
