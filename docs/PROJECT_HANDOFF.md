# MeshKor Project Handoff

**Active Project Handoff Document**  
**Date:** 2026-11-03  
**Working Directory:** `C:\infu-audit\Meshkor_Audit_P`  
**Git Branch:** `main` (up to date with `origin/main`)

---

## 1. Executive Summary & Phases 0, 1, 5 Status

MeshKor is an enterprise healthcare data platform providing infusion-drug price transparency, hospital machine-readable file (MRF) rate benchmarking, and employer/TPA plan audits.

### Phase Status Overview

| Phase | Status | Technical Verification | External Business Requirements |
|---|---|---|---|
| **Phase 0: Project Architecture & Hygiene** | **COMPLETE** | Restructured backend entry points, zero hardcoded developer paths, centralized path resolution, reproducible environment (`backend/requirements.txt`), git hygiene verified | None |
| **Phase 1: CMS Reference Tables & Pipeline** | **COMPLETE** | 7 reference tables verified, 12 quarters ASP coverage (2024Q1–2026Q4), 5 CMS spot-checks passed, 100% normalized 11-digit NDCs, 20/20 in-scope drug coverage, 5 new reference tests passing | None |
| **Phase 2: Claims Explorer & Organizations** | **COMPLETE** | Tenant-scoped synthetic DE-SynPUF and Synthea (178,804 rows across 18 tables) analytics, subtable explorer, DuckDB queries | None |
| **Phase 3: Market Analysis Report** | **COMPLETE** | 100% byte-for-byte report reproducibility verified across markdown, CSVs, and dashboard JSON | 3 genuine broker/TPA evaluations required |
| **Phase 4: Plan Audits & Historical Analytics** | **COMPLETE** | Standalone zero-network client extractor, k-anonymity suppression ($\ge 11$), roster ingestion, audit run engine, in-network savings calculation | Customer data authorization |
| **Phase 5: Production Acceptance & Release Gate** | **PENDING ACCEPTANCE** | Financial math verified (Decimal arithmetic, statutory ASP), 101 tests pass, security regressions pass | **NOT READY FOR COMMERCIAL RELEASE** (0/3 broker evaluations, 0/20 clinical unit approvals) |

---

## 2. Phase 0: Project Structure & Backend Organization

### 2.1 Directory Layout & Standardization
The codebase adheres to the standardized project structure:
- `backend/`: Django project (`backend/auditapi/`), pricing domain app (`backend/pricing/`), entry CLI (`backend/manage.py`), backend dependencies (`backend/requirements.txt`), and backend scripts (`backend/scripts/build_market_report.py`).
- `client_extractor/`: Standalone, distributable zero-network CLI designed to run in the customer's on-premise firewall environment.
- `pipeline/`: Core data transformation package (`common.py`, `build_compare.py`, `synthea_to_parquet.py`, etc.) accessible by Django and CLI tools.
- `data/`: Curated Parquet files (`data/ref/`, `data/mart/`, `data/claims/`, `data/synthetic/`).
- `config/`: Shared pipeline and domain configurations (`drugs_in_scope.yaml`, `pipeline.yaml`, `hospital_sites.csv`, `report.json`).
- `frontend/`: React + Vite single-page application.
- `tests/`: Unified pytest suite (`tests/test_api.py`, `tests/test_pipeline.py`, `tests/test_reference_data.py`, `tests/test_client_extractor.py`).

### 2.2 Path Centralization & Portability
- All runtime data paths resolve dynamically via `pipeline.common.paths()`, which anchors to `Path(__file__).resolve().parent.parent` and supports `IA_DATA_ROOT` environment override.
- Django `settings.py` resolves `BASE_DIR = Path(__file__).resolve().parent.parent` (the `backend/` folder) and `PROJECT_DIR = BASE_DIR.parent`.
- Full-codebase scan confirmed **zero hardcoded developer machine paths** in runtime code. All path operations use relative or dynamically constructed `Path` objects.

### 2.3 Dependency & Environment Verification
- Python Version: Python 3.13.12 (Windows 64-bit).
- Dependencies locked in `backend/requirements.txt` and verified against active virtual environment:
  - `Django==5.2.18`
  - `djangorestframework==3.16.1`
  - `polars==1.34.0`
  - `duckdb==1.4.1`
  - `pyarrow==22.0.0`
  - `cryptography==50.0.2`
  - `reportlab==4.4.4`
- Git hygiene verified: `.env`, SQLite operational databases (`db.sqlite3`), Parquet files, virtual environments, node modules, and raw customer files are strictly excluded by `.gitignore`.

---

## 3. Phase 1: Reference Data Inventory & Validation

All CMS and reference datasets were directly audited via DuckDB and Polars:

### 3.1 Dataset Inventory & Statistics

| Dataset | File Path | Records | Distinct Entities | Periods / Range | Quality Status |
|---|---|---|---|---|---|
| **Medicare ASP** | `data/ref/ref_asp.parquet` | 10,795 | 1,155 HCPCS | 12 Quarters: 2024Q1–2026Q4 | **VALID** (15 null limits out of 10,795; limits $0.002 to $2.73M) |
| **CCN-to-NPI Mapping** | `data/ref/ref_ccn_npi.parquet` | 25,444 | 9,163 CCNs, 19,342 NPIs | Nationwide (783 Ohio records, 316 CCNs, 633 NPIs) | **VALID** (0 null NPIs in Ohio; 131 acute-care CCNs, 362 NPIs) |
| **NPPES Provider** | `data/ref/ref__ref_provider__state_part=OH__*.parquet` | 258,553 | 258,553 NPIs | Ohio partition | **VALID** (0 invalid NPI lengths; 47,392 Type-2 orgs, 211,161 Type-1, 502 deactivated) |
| **NDC-HCPCS Crosswalk** | `data/ref/ref_ndc_hcpcs.parquet` | 95,545 | 1,335 HCPCS, 10,468 NDCs | 12 Quarters | **VALID** (9,069 distinct 11-digit NDCs; 0 invalid `ndc11` lengths) |
| **Drug Scope Reference** | `data/ref/drug_scope.parquet` | 20 | 20 HCPCS | Part B Spending 2024 (Rank 1–20) | **VALID** (All 20 have source SHA-256; reviews pending) |
| **FDA NDC Product** | `data/ref/ref_ndc_product.parquet` | 116,501 | FDA product directory | Reference | **VALID** |
| **NADAC Acquisition** | `data/ref/ref_nadac.parquet` | 1,001,844 | Retail pharmacy acquisition | Benchmark reference | **VALID** (Acquisition prices separated from hospital negotiated rates) |

### 3.2 ASP Statutory Rate Verification & 5 Spot Checks
The published CMS ASP payment limit includes statutory payment percentages. **No additional 6% is ever added.**  
Five spot-checks for 2026Q1 were verified against published CMS payment files:

1. **J0897** (Denosumab injection / Prolia, 1 MG): **$29.4570**
2. **J1745** (Infliximab not biosimil / Remicade, 10 MG): **$32.4230**
3. **J2350** (Injection, ocrelizumab / Ocrevus, 1 MG): **$59.3240**
4. **J9271** (Injection, pembrolizumab / Keytruda, 1 MG): **$59.7260**
5. **J9299** (Injection, nivolumab / Opdivo, 1 MG): **$33.5730**

### 3.3 Scope Coverage
- 20 of 20 in-scope drugs (100%) are present in `ref_asp.parquet`.
- 20 of 20 in-scope drugs (100%) are present in `ref_ndc_hcpcs.parquet`.
- Dedicated test suite `tests/test_reference_data.py` validates all 5 conditions programmatically.

---

## 4. Phase 5: Verification & Production Acceptance

### 4.1 Report Reproducibility (Task 6)
- Verified report generation reproducibility using `scripts/test_reproducibility.py`.
- Running `build_market_report.py` across successive runs with unchanged inputs yields **100% byte-for-byte identical output**:
  - `reports/ohio_market_report_draft.md`: **MATCH (Byte-for-byte)**
  - `reports/ohio_hospital_by_drug.csv`: **MATCH (Byte-for-byte)**
  - `reports/ohio_report_sources.csv`: **MATCH (Byte-for-byte)**
  - `reports/dashboard_data.json`: **MATCH (Byte-for-byte)**

### 4.2 Financial Calculation Validation (Task 7)
- **Calculation engine**: `pipeline.build_compare` and `backend.pricing.plan_audit_engine` use Python `Decimal` arithmetic.
- **Formula adherence**:
  $$\text{per\_unit} = \frac{\text{total\_allowed}}{\text{total\_units}}$$
  $$\text{markup\_multiple} = \frac{\text{verified\_price\_per\_unit}}{\text{matching\_quarter\_asp\_payment\_limit}}$$
  $$\text{potential\_savings} = \max(0, \text{current\_allowed\_per\_unit} - \text{alternative\_allowed\_per\_unit}) \times \text{eligible\_units}$$
- **Discrepancy fix**: Resolved a defect in `pipeline/build_compare.py` where `asp["quarter"]` and `dosage_text` were expected instead of `period` and `dosage`. Both aliases are now handled automatically.
- **Run verification**: `python -m pipeline.build_compare` writes 10,266 comparison rows deterministically:
  - `unit_unresolved`: 9,153
  - `percent_of_charges`: 540
  - `algorithm_only`: 514
  - `review_range`: 59

### 4.3 Independent Clinical Unit Review (Task 8)
- Model: `DrugUnitReview` (`backend/pricing/models.py`).
- Clinical review boundary: MeshKor does not impersonate a clinical reviewer or auto-approve drug units.
- **Current Status**: All 20 in-scope drugs remain with `review_status = None` (pending).
- Review package: The frontend `DrugScope.jsx` provides HCPCS, drug description, CMS billing units, dosage text, source SHA-256 hashes, and review notes.
- Independent verification requirement: Dual-party sign-off is required before any report release. A user who submits an audit cannot be the sole approver.

### 4.4 Security Regression Testing (Task 9)
All negative and boundary security test cases pass:
- Unauthenticated requests to analytics endpoints return `401 Unauthorized`.
- Non-admin/viewer role escalation requests return `403 Forbidden`.
- Organization isolation: Users can only query datasets and plan audits belonging to their authorized tenant organizations (`visible_organizations()`).
- Path traversal: Zip upload rejects path traversal members (`../../`).
- Session security: Google Authenticator TOTP enforced; encrypted TOTP seeds in SQLite; CSRF tokens enforced on state-modifying requests.
- Formula injection: Untrusted CSV export cells prefixed with formula characters (`=`, `+`, `-`, `@`) are neutralized with prepended single quotes.

### 4.5 Legal, Compliance & Insurance Readiness (Task 10)
- **Disclaimers in Reports**:
  - ASP benchmarks represent statutory limits and do not reflect supplemental rebates.
  - Hospital prices originate from public machine-readable files (MRFs) subject to hospital reporting accuracy.
  - Billing unit conversions are subject to clinical validation.
  - Potential savings opportunities are informational estimates and require clinical appropriateness and network verification.
- **Readiness Checklist**:
  - [ ] Commercial Errors & Omissions (E&O) insurance policy active.
  - [ ] Cyber liability & privacy insurance policy active.
  - [ ] Business Associate Agreement (BAA) assessment completed.
  - [ ] Customer Data Processing Agreement (DPA) finalized.
  - [ ] Formal legal review of market report marketing claims.

### 4.6 Customer Validation & Pricing Strategy (Task 11)
- **Broker/TPA Milestone**: 3 genuine evaluations required before commercial release. Current status: **0 of 3**.
- **Pricing Strategy Models Evaluated**:
  1. *Per-Report Pricing* ($5,000–$15,000 per completed audit): Low commitment for employers; simple transaction model; lacks recurring predictable revenue.
  2. *Annual Per-Employer Retainer* ($25,000–$50,000/yr for quarterly audits): Aligns with ongoing plan year monitoring; higher annual contract value; requires continuous claims onboarding.
  3. *PEPM (Per-Employee-Per-Month)* ($0.25–$0.75 PEPM): Familiar model for TPAs and self-funded employers; scalable with employer headcount; requires steady enrollment roster integration.
  4. *Paid Pilot Pricing* ($7,500 single-plan initial audit credited toward annual retainer): Minimizes client friction; directly tests the 15-step onboarding workflow; ideal for the first authorized commercial client.

---

## 5. Release Acceptance Gate (Task 12)

```text
======================================================================
                     RELEASE ACCEPTANCE GATE
======================================================================
  Technical Implementation:      VERIFIED (101/101 tests pass)
  Security Regressions:          VERIFIED
  Report Reproducibility:        VERIFIED (Byte-for-byte identical)
  Financial Precision:           VERIFIED (Decimal arithmetic)
  Clinical Review Approvals:     PENDING (0 / 20 approved)
  Broker/TPA Evaluations:        PENDING (0 / 3 evaluations)
  Legal/Insurance Policies:      PENDING BUSINESS AUTHORIZATION

  FINAL RELEASE STATUS: NOT READY FOR COMMERCIAL RELEASE
======================================================================
```

Release is gated until:
1. Three genuine broker/TPA evaluations are recorded in `BrokerEvaluation`.
2. Qualified human clinical reviewers complete the 20 pending `DrugUnitReview` records.
3. Corporate legal and insurance coverage checklists are finalized.

---

## 6. Verification Commands & Execution Results

```powershell
cd C:\infu-audit\Meshkor_Audit_P

# 1. Django System & Migration Check
.venv\Scripts\python.exe backend/manage.py check
# Result: System check identified no issues (0 silenced).

.venv\Scripts\python.exe backend/manage.py migrate --check
# Result: No pending unapplied migrations.

# 2. Pytest Test Suite
.venv\Scripts\python.exe -m pytest -q
# Result: 101 passed in 22.86s.

# 3. Reference Data Verification
.venv\Scripts\python.exe scripts/verify_phase1_complete.py
# Result: All 5 reference datasets verified; 20/20 drugs covered; 0 invalid NPIs/NDCs.

# 4. Report Reproducibility
.venv\Scripts\python.exe scripts/test_reproducibility.py
# Result: 100% byte-for-byte match across markdown, CSVs, and dashboard JSON.

# 5. Frontend Production Build
npm --prefix frontend run build
# Result: Built in 4.7s, 0 errors.
```

---

## 7. Modified & Created Files Summary

### Files Created:
- `backend/requirements.txt`: Locked backend dependencies.
- `backend/scripts/build_market_report.py`: Backend CLI entry point for report generation.
- `tests/test_reference_data.py`: 5 automated tests for Phase 1 CMS reference datasets.
- `scripts/verify_phase1_complete.py`: Comprehensive reference data audit utility.
- `scripts/test_reproducibility.py`: Report byte-level reproducibility test script.

### Files Modified:
- `pipeline/build_compare.py`: Fixed `quarter`/`period` and `dosage_text`/`dosage` column alias handling for `ref_asp.parquet`.
- `docs/PROJECT_HANDOFF.md`: Updated with complete Phase 0, 1, and 5 handoff documentation.
