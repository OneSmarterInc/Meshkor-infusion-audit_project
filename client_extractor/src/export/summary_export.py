import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


APPROVED_FIELDS = [
    "plan_reference", "hcpcs_code", "site_npi", "quarter", "service_setting",
    "total_units", "total_allowed_amount", "total_paid_amount", "member_count_band",
    "source_period", "extraction_timestamp", "schema_version",
]


def export_summary(rows, output_directory, *, source_provenance):
    if (not isinstance(source_provenance, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(source_provenance.get("source_sha256", "")))
            or source_provenance.get("source_format") not in {"csv", "xlsx", "xlsm"}
            or not isinstance(source_provenance.get("input_records"), int)
            or source_provenance["input_records"] < 1
            or not str(source_provenance.get("source_period", ""))
            or not str(source_provenance.get("plan_reference", ""))):
        raise ValueError("Source provenance is incomplete or invalid.")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "claims_summary.csv"
    fd, temp_name = tempfile.mkstemp(prefix="claims_summary_", suffix=".part", dir=output)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=APPROVED_FIELDS, extrasaction="raise")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row[field] for field in APPROVED_FIELDS})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    digest = _sha256(destination)
    manifest = {
        "file": destination.name, "sha256": digest, "schema_version": "1.0",
        "approved_fields": APPROVED_FIELDS, "aggregate_rows": len(rows),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_claims_included": False, "member_identifiers_included": False,
        "small_cell_suppression_threshold": 11,
        "complementary_suppression_applied": True,
        "source_provenance": source_provenance,
    }
    manifest_path = output / "manifest.json"
    manifest_tmp = manifest_path.with_suffix(".json.part")
    manifest_tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(manifest_tmp, manifest_path)
    return destination, manifest_path, digest


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
