# Client-Side Claims Extractor

Run this tool in the employer's or TPA's controlled environment. It reads raw claims locally and does not upload them to MeshKor. Only the approved aggregate CSV and its manifest are exported.

## Setup

```powershell
cd client_extractor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config/client_config.example.yaml config/client_config.yaml
```

Set `plan_id` and `plan_reference` in `config/client_config.yaml`. Populate `hcpcs_billing_units` only from an approved, source-verified HCPCS billing-unit review; never guess units. Each entry must include `qty`, `unit`, `verification_status: validated`, `source_reference`, and the source file `source_sha256`.

Required input columns: `claim_id`, `service_date`, `hcpcs_code`, `site_npi`, `units`, `allowed_amount`, `paid_amount`, `member_id`, `claim_status`, and `plan_id`. Optional columns: `adjustment_reference`, `reversal_reference`, and `service_setting`.

The optional `column_mapping` configuration maps canonical field names to exact source header labels. For example:

```yaml
column_mapping:
  claim_id: Claim Number
  member_id: Member Number
```

Do not map one source column to multiple canonical fields. Required mapped headers are checked before rows are processed. Keep claim/member identifiers, plan IDs, and NPIs as text in XLSX/XLSM; numeric spreadsheet cells may already have lost leading zeroes before the extractor reads them. Inputs are limited to 256 MiB and 1,000,000 records. CSV fields are limited to 1,000,000 characters; XLSX/XLSM archives are checked for internal file count, expansion size, and extreme compression ratios. The extractor does not make network requests.

## Run

```powershell
python -m src.cli extract --claims path\to\claims.csv --config config\client_config.yaml --output output
```

CSV, XLSX, and XLSM exports are supported. The 837P/837I adapter deliberately reports that parsing is unavailable until payer companion guides and adjudication rules are implemented.

## Adjudication and privacy

- Exact duplicate rows are excluded and counted. Conflicting identifiers, unresolved references, multiple adjustments to one original, and cross-member/plan/code/provider/date references are flagged and excluded.
- A reversal cancels its explicitly referenced claim. An adjustment replaces the explicitly referenced claim only when that reference is unambiguous and matches member, plan, HCPCS, site NPI, and service date. No amount-based or latest-row guess is used.
- Denied claims are excluded. Only paid/approved claims and unambiguous adjusted replacements are included.
- Aggregation groups by plan, HCPCS, site NPI, quarter, and service setting. Cells below 11 distinct members are suppressed. A complementary site cell is also suppressed within the same plan/code/quarter/setting group. No subtotal or total rows are exported. The manifest declares the threshold and complementary suppression.
- Exact member counts, claim IDs, member IDs, and row-level values are not included in the approved export or manifest. The manifest contains the source-file SHA-256, source format, input record count, source period, and configured plan reference; the source file itself stays local. The central server cannot verify that source hash against an unuploaded file. The local quality report contains row numbers and generic reasons only. Suppression alone is not a HIPAA compliance determination.
- The extractor rejects unit configuration without a verified source and checksum. Records for codes without a configured verified HCPCS billing unit are excluded and counted.

## Outputs

- `output/claims_summary.csv`: approved central-import schema only.
- `output/manifest.json`: aggregate CSV SHA-256, schema version, row count, privacy/suppression declarations, and non-identifying source provenance.
- `output/data_quality.json`: local-only row numbers and reasons; keep it inside the client environment.

This tool is a privacy control, not a legal or compliance certification. The employer/TPA must review adjudication, suppression, and data-governance rules before use.
