# API Endpoints and Page Routes

The React frontend communicates with the Django REST Framework API under `/api/v1/`. Canonical routes use descriptive, explicit resource names. Legacy short aliases under `/api/` and `/api/v1/` remain active for backward compatibility with existing test suites and integrations.

---

## 1. Authentication and User Management

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/system/health-check/` | Public | System health check (verifies API, SQLite database, and DuckDB/Parquet access) |
| `GET` | `/api/v1/authentication/csrf-token/` | Public | Obtains CSRF cookie for browser authentication |
| `POST` | `/api/v1/authentication/sign-in/` | Public | Password authentication; returns challenge for TOTP enrollment or verification |
| `GET` | `/api/v1/authentication/current-user/` | Public | Returns current authenticated session identity and assigned roles |
| `GET` | `/api/v1/authentication/totp/status/` | Authenticated | Checks authenticator enrollment status |
| `POST` | `/api/v1/authentication/totp/setup/` | Challenge | Generates TOTP seed and QR code URI for Google Authenticator |
| `POST` | `/api/v1/authentication/totp/confirmation/` | Challenge | Confirms setup with code; issues one-time recovery codes |
| `POST` | `/api/v1/authentication/totp/sign-in-verification/` | Challenge | Verifies 6-digit TOTP code or recovery code; completes sign-in |
| `POST` | `/api/v1/authentication/totp/deactivation/` | Authenticated | Always returns `403`; TOTP is mandatory across all accounts |
| `POST` | `/api/v1/authentication/access-token/refresh/` | Authenticated | Refreshes access token via active Django session cookie |
| `POST` | `/api/v1/authentication/sign-out/` | Authenticated | Clears user session and signs out |
| `GET`, `POST` | `/api/v1/user-accounts/` | Admin | Lists all user accounts or creates a new user account |
| `POST` | `/api/v1/user-accounts/{user_id}/management/` | Admin | Updates account role, active status, or password |

---

## 2. Phase 1: Market Analytics & Benchmark Data

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/dashboard/summary/` | Admin; Viewer with release | High-level market rate summary and median markups |
| `GET` | `/api/v1/dashboard/price-trends/` | Admin; Viewer with release | Quarterly price trend benchmarks |
| `GET` | `/api/v1/medicare-part-b-drugs/` | Admin; Viewer with release | Catalog of in-scope Medicare Part B infusion drugs |
| `GET` | `/api/v1/medicare-part-b-drugs/{hcpcs}/` | Admin; Viewer with release | Detailed price benchmarks and dosage for a drug |
| `GET` | `/api/v1/hospitals/` | Admin; Viewer with release | Directory of hospitals with normalized pricing data |
| `GET` | `/api/v1/hospitals/{hospital_key}/` | Admin; Viewer with release | Detailed price comparisons for a specific hospital |
| `GET` | `/api/v1/hospital-drug-price-comparisons/` | Admin; Viewer with release | Paginated, filterable hospital-to-ASP comparisons |
| `GET` | `/api/v1/hospital-drug-price-comparisons/exports/csv/` | Admin; Viewer with release | CSV export of filtered rate comparisons |
| `GET` | `/api/v1/hospital-drug-price-comparisons/exports/excel/` | Admin only | Excel export of rate comparisons |
| `GET` | `/api/v1/reports/` | Admin; Viewer with release | Report catalog (Admin sees history; Viewer sees released) |
| `GET` | `/api/v1/reports/market-analysis/pdf/` | Admin; Viewer with release | Downloads current released Market Analysis PDF |
| `POST` | `/api/v1/reports/approval/sign-off/` | Admin (dual sign-off) | Releases market report (requires 2 active Admins, clinical reviews) |
| `GET` | `/api/v1/reports/evaluation-package/` | Admin, Reviewer, Analyst | Returns market report summary, product explanation, 7 evaluation questions, and 15-step paid pilot checklist |
| `GET` | `/api/v1/reports/evaluations/` | Admin, Reviewer, Analyst | Lists broker evaluations and returns milestone progress (3 required verified evaluations) |
| `POST` | `/api/v1/reports/evaluations/` | Admin only | Records a broker/TPA market report evaluation (logs audit event) |
| `GET` | `/api/v1/reports/evaluations/{id}/` | Admin, Reviewer, Analyst | Detailed view of a broker/TPA evaluation record |
| `PATCH` | `/api/v1/reports/evaluations/{id}/` | Admin only | Updates evaluation status, notes, or next action (logs audit event) |
| `DELETE` | `/api/v1/reports/evaluations/{id}/` | Admin only | Deletes an evaluation record (logs audit event) |

---

## 3. Phase 1: Admin Data Operations & Pipeline

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/data-sources/` | Admin | Configured raw input files and discovery status |
| `POST` | `/api/v1/data-sources/uploads/` | Admin | Validates and stores raw pipeline input files |
| `GET`, `POST` | `/api/v1/data-sources/processing-jobs/` | Admin | Lists or queues pipeline/report generation jobs |
| `GET` | `/api/v1/data-sources/processing-jobs/{job_id}/` | Admin | Tracks pipeline background job execution and logs |
| `GET` | `/api/v1/reference-data-sets/` | Admin | Catalog of curated reference Parquet tables |
| `GET` | `/api/v1/reference-data-sets/{dataset_name}/` | Admin | Paginated browser for an allow-listed Parquet table |
| `GET` | `/api/v1/drug-selection-scope/` | Admin | Part B drug selection status and scope hash |
| `POST` | `/api/v1/drug-selection-scope/{hcpcs}/unit-review/` | Admin | Records clinical reviewer validation for a billing unit |
| `GET` | `/api/v1/drug-unit-validations/` | Admin | Drug unit review queue |
| `GET` | `/api/v1/hospital-file-quality/` | Admin | Machine-readable file parsing quality metrics |
| `GET` | `/api/v1/application-settings/` | Authenticated | Read runtime configuration (Admin may update) |
| `GET` | `/api/v1/audit-logs/` | Admin | Query operational audit events |

---

## 4. Phase 2: Claims Explorer (Synthetic Research & EHR Claims)

Access is strictly scoped to authorized organization membership. Queries run via DuckDB against:
- CMS DE-SynPUF synthetic Medicare claims (2008–2010)
- Synthea Synthetic Healthcare & EHR Claims Demo (1940–2026; 18 Parquet tables with direct identifiers scrubbed)

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/claims/datasets/` | Authenticated (Org Scoped) | Lists registered synthetic claims datasets |
| `POST` | `/api/v1/claims/datasets/` | Admin (Org Scoped) | Registers a supported claims dataset (DE-SynPUF or Synthea) for an organization |
| `GET` | `/api/v1/claims/synthea/summary/` | Authenticated (Org Scoped) | Analytical summary KPIs, yearly trends, encounter classes, top procedures/meds, and quality report |
| `GET` | `/api/v1/claims/` | Authenticated (Org Scoped) | Paginated list of claims or Synthea subtables (`encounters`, `claims`, `procedures`, `medications`, `claims_transactions`, `providers`, `organizations`, `patients`) |
| `GET` | `/api/v1/claims/{claim_id}/` | Authenticated (Org Scoped) | Detailed view of a single claim or encounter with linked procedures/medications |

---

## 5. Phase 4: Plan Audits (Employer & TPA Historical Analytics)

### 5.1 Organization, Plan, and Network Roster Setup

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/organizations/` | Authenticated (Org Scoped) | Lists visible organizations for the current user |
| `POST` | `/api/v1/organizations/` | Admin | Creates a new employer or TPA organization |
| `GET` | `/api/v1/organizations/{org_id}/users/` | Admin | Lists users granted access to the organization |
| `POST` | `/api/v1/organizations/{org_id}/users/` | Admin | Grants an Analyst, Reviewer, or Client access to an organization |
| `DELETE` | `/api/v1/organizations/{org_id}/users/{user_id}/` | Admin | Revokes user access from an organization |
| `GET` | `/api/v1/plans/` | Authenticated (Org Scoped) | Lists active plans for an organization |
| `POST` | `/api/v1/plans/` | Admin | Creates a new plan with unique plan reference |
| `GET`, `PATCH` | `/api/v1/plans/{plan_id}/` | Authenticated / Admin | Reads or updates plan details (effective dates, status) |
| `GET` | `/api/v1/plans/{plan_id}/network-roster/` | Authenticated (Org Scoped) | Reads roster status counts and full-quarter coverage |
| `POST` | `/api/v1/plans/{plan_id}/network-roster/` | Super Admin / Analyst | Uploads and validates a provider network roster CSV |

### 5.2 Approved Summary Imports

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `POST` | `/api/v1/plan-audits/import-summary/` | Super Admin / Analyst | Validates and imports extractor aggregate CSV + manifest |
| `GET` | `/api/v1/plan-audits/imports/` | Authenticated (Org Scoped) | Lists aggregate summary imports for a plan |
| `GET` | `/api/v1/plan-audits/imports/{import_id}/` | Authenticated (Org Scoped) | Metadata for a specific aggregate import |
| `GET` | `/api/v1/plan-audits/imports/{import_id}/quality/` | Authenticated (Org Scoped) | Privacy, row counts, and validation summary |

### 5.3 Plan Audit Runs and Historical Analytics

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/api/v1/plan-audits/` | Authenticated (Org Scoped) | Lists audits (Client sees approved only) |
| `POST` | `/api/v1/plan-audits/create/` | Super Admin / Analyst | Creates an audit run for an accepted summary import |
| `GET` | `/api/v1/plan-audits/{audit_id}/` | Authenticated (Org Scoped) | Details and status of a plan audit |
| `POST` | `/api/v1/plan-audits/{audit_id}/run/` | Super Admin / Analyst | Executes audit calculations against ASP & hospital references |
| `GET` | `/api/v1/plan-audits/{audit_id}/overview/` | Authenticated (Org Scoped) | Top-level audit totals and metric summary |
| `GET` | `/api/v1/plan-audits/{audit_id}/results/` | Authenticated (Org Scoped) | Paginated comparison results with allow-listed filters |
| `GET` | `/api/v1/plan-audits/{audit_id}/historical-spending/` | Authenticated (Org Scoped) | Historical spending grouped by quarter, HCPCS, site, and setting |
| `GET` | `/api/v1/plan-audits/{audit_id}/drug-analysis/` | Authenticated (Org Scoped) | Aggregated drug spending and markup multiples |
| `GET` | `/api/v1/plan-audits/{audit_id}/hospital-comparison/` | Authenticated (Org Scoped) | Plan allowed vs verified hospital reference rates |
| `GET` | `/api/v1/plan-audits/{audit_id}/asp-benchmark/` | Authenticated (Org Scoped) | Plan allowed/paid unit rates vs CMS ASP limits |
| `GET` | `/api/v1/plan-audits/{audit_id}/savings/` | Authenticated (Org Scoped) | Evidence-backed in-network savings opportunities |
| `GET` | `/api/v1/plan-audits/{audit_id}/network-validation/` | Authenticated (Org Scoped) | Provider site network coverage across claim quarters |
| `GET` | `/api/v1/plan-audits/{audit_id}/quality/` | Authenticated (Org Scoped) | Data quality findings, benchmarks, and warning counts |

### 5.4 Reports, Reviews, and Exports

| Method | Canonical Endpoint | Access | Description |
|---|---|---|---|
| `POST` | `/api/v1/plan-audits/{audit_id}/generate-report/` | Super Admin / Analyst | Generates the formal PDF audit report |
| `GET` | `/api/v1/plan-audits/{audit_id}/report/` | Authenticated (Org Scoped) | Downloads the generated PDF report |
| `GET` | `/api/v1/plan-audits/{audit_id}/export-csv/` | Authenticated (Org Scoped) | Exports complete audit results as CSV |
| `GET` | `/api/v1/plan-audits/{audit_id}/review/` | Authenticated (Org Scoped) | Reads append-only review comments and history |
| `POST` | `/api/v1/plan-audits/{audit_id}/review/` | Reviewer / Super Admin | Appends a formal review comment |
| `POST` | `/api/v1/plan-audits/{audit_id}/approve/` | Reviewer / Super Admin | Approves the audit (permitting Client access) |
| `POST` | `/api/v1/plan-audits/{audit_id}/reject/` | Reviewer / Super Admin | Rejects the audit with documented reason |

---

## 6. Frontend Page Routes

| Route | Component | Access / Visibility |
|---|---|---|
| `/` | `Overview.jsx` | Authenticated; Viewers see released report only |
| `/hospitals` | `Hospitals.jsx` | Authenticated; Viewers see released report only |
| `/hospitals/:hospital_key` | `HospitalDetail.jsx` | Authenticated; Viewers see released report only |
| `/medicare-part-b-drugs/:hcpcs` | `DrugDetail.jsx` | Authenticated; Viewers see released report only |
| `/hospital-drug-price-comparisons` | `Rates.jsx` | Authenticated; Viewers see released report only |
| `/reports` | `Reports.jsx` | Authenticated; Viewers see released report only |
| `/methodology` | `Method.jsx` | Authenticated |
| `/drug-selection-scope` | `DrugScope.jsx` | Admin only |
| `/drug-unit-validations` | `UnitValidation.jsx` | Admin only |
| `/data-sources` | `Sources.jsx` | Admin only |
| `/reference-data-sets` | `ReferenceData.jsx` | Admin only |
| `/hospital-price-transparency-files` | `HospitalFiles.jsx` | Admin only |
| `/hospital-file-quality` | `Quality.jsx` | Admin only |
| `/audit-logs` | `AuditLogs.jsx` | Admin only |
| `/user-management` | `UserManagement.jsx` | Admin only |
| `/application-settings` | `Settings.jsx` | Authenticated; Admin can modify |
| `/claims` | `ClaimsExplorer.jsx` | Authenticated (Org Scoped) |
| `/plan-audits` | `PlanAudits.jsx` | Authenticated (Org Scoped); Admin/Analyst for Setup |
