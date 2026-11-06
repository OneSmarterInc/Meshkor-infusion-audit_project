# MeshKor Architecture & Security Specifications

## 1. System Overview

MeshKor is a healthcare analytics platform designed for infusion-drug price transparency, market rate benchmarking, and employer/TPA plan audits.

The application follows a **dual-store architecture**:
1. **Analytical Data Store (Parquet + DuckDB)**: Parquet files serve as the durable, immutable store for hospital rates, Medicare benchmarks, reference crosswalks, and synthetic claims. DuckDB executes fast in-process analytical queries without loading bulk price rows into an RDBMS.
2. **Operational Data Store (SQLite + Django ORM)**: SQLite stores operational records, users, RBAC groups, encrypted TOTP seeds, audit logs, background job metadata, and plan audit organizational structures (`data/operational/meshkor.sqlite3`).
3. **Local Client Extractor (`client_extractor/`)**: An isolated, client-executed Python tool that processes raw employer/TPA claims inside the customer's controlled perimeter, applies primary and complementary privacy suppression, and generates an approved aggregate CSV and manifest. Raw claims never enter central MeshKor systems.

```
+-------------------------------------------------------------+
|                Customer / TPA Boundary                      |
|                                                             |
|   +-------------------+         +-----------------------+   |
|   | Raw Claims Export | ------> | Client-Side Extractor |   |
|   | (CSV, XLSX, XLSM) |         | (Local CLI, no net)   |   |
|   +-------------------+         +-----------------------+   |
|                                             |               |
|                           Generates:        |               |
|                           - claims_summary  | (Aggregates)  |
|                           - manifest.json   |               |
+---------------------------------------------|---------------+
                                              | HTTPS Upload
                                              v (Analyst / Admin)
+-------------------------------------------------------------+
|                 MeshKor Central System                      |
|                                                             |
|   +--------------------+          +---------------------+   |
|   | React Frontend     | <------> | Django REST (ASGI)  |   |
|   | (Vite SPA)         |          | Versioned API /v1/  |   |
|   +--------------------+          +---------------------+   |
|                                       |             |       |
|                       +---------------+             |       |
|                       v                             v       |
|              +------------------+         +---------------+ |
|              | SQLite DB        |         | DuckDB Engine | |
|              | (meshkor.sqlite3)|         | (In-process)  | |
|              | Operational/Auth |         +---------------+ |
|              +------------------+                   |       |
|                                                     v       |
|                                           +---------------+ |
|                                           | Parquet Mart  | |
|                                           | (Curated data)| |
|                                           +---------------+ |
+-------------------------------------------------------------+
```

---

## 2. Storage Boundaries and Data Contracts

### 2.1 SQLite Operational Tables

All operational models reside in `backend/pricing/models.py`:

| Model | Table Name | Purpose |
|---|---|---|
| `User`, `Group` | `auth_user`, `auth_group` | Account identity and RBAC role assignment |
| `AdminOTPDevice` | `pricing_adminotpdevice` | Encrypted TOTP secret (`TOTP_ENCRYPTION_KEY`), recovery code hashes, replay prevention |
| `AuditEvent` | `pricing_auditevent` | Immutable audit log capturing actor, action, entity, and timestamp |
| `SourceFile` | `pricing_sourcefile` | Metadata, path, SHA-256 checksum, and status of pipeline input files |
| `ProcessingJob` | `pricing_processingjob` | Background task state, progress, and execution summaries |
| `ReportRecord` | `pricing_reportrecord` | Phase 1 market report release state, builder/approver IDs, release checksums |
| `DrugUnitReview` | `pricing_drugunitreview` | External clinical reviewer validation records for billing units |
| `PlanOrganization` | `pricing_planorganization` | Employer or TPA organizational tenant |
| `Plan` | `pricing_plan` | Plan definition tied to an organization with plan reference and effective dates |
| `PlanOrganizationAccess` | `pricing_planorganizationaccess` | Explicit user-to-organization authorization boundary |
| `ClaimsSummaryImport` | `pricing_claimssummaryimport` | Metadata for approved aggregate uploads; raw claims never stored |
| `ClaimsSummaryRecord` | `pricing_claimssummaryrecord` | Approved unsuppressed aggregate cells (quarter, HCPCS, site NPI, setting, amounts) |
| `NetworkRosterEntry` | `pricing_networkrosterentry` | In-network and out-of-network provider rosters with effective date ranges |
| `PlanAudit` | `pricing_planaudit` | Audit run definition for a plan and accepted aggregate import |
| `PlanAuditResult` | `pricing_planauditresult` | Derived comparisons (plan allowed/paid vs ASP limit and hospital reference) |
| `SavingsOpportunity` | `pricing_savingsopportunity` | Evidence-backed in-network price differences (clinical review pending) |
| `AuditReview` | `pricing_auditreview` | Append-only review history and formal approval/rejection decisions |
| `ClaimsExplorerDataset` | `pricing_claimsexplorerdataset` | Registrations for synthetic CMS DE-SynPUF exploration |

### 2.2 Parquet Analytical Artifacts

Parquet datasets are managed under `data/`:

| Artifact | Location | Schema / Contents |
|---|---|---|
| ASP Benchmarks | `data/ref/ref_asp.parquet` | CMS ASP payment limits by HCPCS, quarter, dosage text, short description |
| NDC Crosswalk | `data/ref/ref_crosswalk.parquet` | CMS NDC-to-HCPCS mappings |
| FDA NDC Products/Packages | `data/ref/ref_ndc_products.parquet`, `ref_ndc_packages.parquet` | FDA National Drug Code directory |
| Hospital Directory | `data/ref/ref_hospitals.parquet` | CMS hospital catalog (CCN, name, address, state) |
| NADAC Reference | `data/ref/ref_nadac.parquet` | National Average Drug Acquisition Cost |
| Provider Directory | `data/ref/ref_providers.parquet`, `ref_enrollments.parquet` | NPI directory and CMS enrollment data |
| Part B Drug Scope | `data/ref/drug_scope.parquet` | Spend-ranked Part B infusion drugs with scope provenance hash |
| Hospital Rates | `data/hpt/norm/hpt_drug_rates.parquet` | Normalized hospital machine-readable file (MRF) rate observations |
| Hospital File Quality | `data/hpt/norm/hpt_file_quality.parquet` | MRF parsing results, error counts, row counts |
| Price Comparisons | `data/mart/comparison.parquet` | Hospital-to-ASP comparisons, billing units, status, rate type |
| Run Metadata | `data/mart/run_info.json` | Snapshot timestamps, source checksums, comparison counts |
| Synthetic Claims | `data/claims/normalized/*.parquet` | 2008–2010 CMS DE-SynPUF synthetic research claims (`outpatient_claims.parquet`, `inpatient_claims.parquet`) |

---

## 3. Workflows

### 3.1 Phase 1: Market Rate Analysis & Reporting

1. **Source Ingestion**: Raw CMS ASP and hospital MRF files are cataloged in `SourceFile` and normalized to Parquet.
2. **Benchmark Matching**: For each Part B drug in `drug_scope`, hospital negotiated dollar rates are matched against CMS ASP payment limits for the identical quarter.
3. **Clinical Unit Review**: External clinical reviewers inspect dosage and billing unit configurations. Every drug in scope requires validated review (`DrugUnitReview`) before market report release.
4. **Dual-Admin Report Sign-off**: Generating an official market analysis PDF requires sign-off from two distinct Admin accounts (`ReportRecord`), guaranteeing separation of builder and approver duties.
5. **Viewer Access**: Viewers see only officially released reports; unapproved drafts and unresolved units are filtered out.

### 3.2 Phase 2: Claims Explorer (Synthetic Claims)

- Exposes read-only DuckDB views over normalized 2008–2010 CMS DE-SynPUF synthetic claims.
- Strictly isolated from commercial reference pricing: **no financial comparisons to current prices are permitted**.
- Access is restricted to authenticated users with explicit organization assignments (`ClaimsExplorerDataset`).

### 3.3 Phase 4: Plan-Specific Infusion Drug Audits

1. **Client-Side Extraction**:
   - The employer or TPA runs `client_extractor` inside their environment against raw claim exports (CSV, XLSX, XLSM).
   - Validates claim status, dates, positive units, non-negative amounts, and unique adjustment/reversal links.
   - Enforces k-anonymity suppression: aggregate cells with fewer than 11 distinct members are suppressed; complementary suppression is applied to prevent cell reconstruction.
   - Produces `claims_summary.csv` and `manifest.json`.
2. **Central Ingestion**:
   - Analyst or Admin imports the summary CSV and manifest.
   - Server re-verifies SHA-256 hash, privacy declarations, schema conformance, and plan reference alignment.
   - Persists only approved aggregate rows into `ClaimsSummaryRecord`. Raw claims are never uploaded or stored.
3. **Network Roster Ingestion**:
   - Admin imports provider network rosters with effective date ranges.
   - System rejects overlapping conflicting statuses for the same NPI. An NPI is considered in-network only when coverage spans the full claim quarter.
4. **Audit Execution (`plan_audit_engine.py`)**:
   - Evaluates plan allowed and paid prices per unit against the matching-quarter CMS ASP payment limit.
   - Evaluates hospital-reported reference prices where verified name-to-NPI mappings exist in `config/hospital_sites.csv`.
   - Identifies evidence-backed savings opportunities where a lower-priced alternative site has full-quarter in-network status.
5. **Review & Governance (`plan_audit_reports.py`)**:
   - Reviewers examine results and submit comments. Decisions (`approved` or `rejected`) are append-only.
   - Client accounts can only download or view reports after an authorized Reviewer or Admin approves the audit.

---

## 4. Financial Calculation Principles

All calculations are executed with Python `Decimal` precision to avoid floating-point errors.

1. **Plan Allowed Price per Unit**:
   $$\text{Allowed per Unit} = \frac{\text{Total Allowed Amount}}{\text{Total Units}}$$
   Requires $\text{Total Units} > 0$.

2. **Plan Paid Price per Unit**:
   $$\text{Paid per Unit} = \frac{\text{Total Paid Amount}}{\text{Total Units}}$$
   Requires $\text{Total Units} > 0$.

3. **Plan Allowed Markup Multiple**:
   $$\text{Allowed Markup} = \frac{\text{Plan Allowed Price per Unit}}{\text{Matching Quarter CMS ASP Payment Limit}}$$
   Gated on verified billing unit match (`unit_ok`).

4. **Plan Paid Markup Multiple**:
   $$\text{Paid Markup} = \frac{\text{Plan Paid Price per Unit}}{\text{Matching Quarter CMS ASP Payment Limit}}$$
   Gated on verified billing unit match (`unit_ok`).

5. **Hospital Markup Multiple**:
   $$\text{Hospital Markup} = \frac{\text{Hospital Reported Price per Billing Unit}}{\text{Matching Quarter CMS ASP Payment Limit}}$$

6. **Statutory ASP Add-on Rule**:
   CMS ASP payment limits published by CMS **already include** the statutory 6% add-on ($106\% \text{ of ASP}$). The calculation engine **never adds a second 6%**.

7. **Quarter Matching Rule**:
   Comparisons require an exact calendar quarter match (e.g., claims from `2025Q1` match CMS ASP `2025Q1`). Falling back to a later or earlier ASP quarter is strictly prohibited.

8. **Hospital Reference Rate Semantics**:
   Hospital machine-readable file prices represent facility-published charges, not the employer plan's contracted rate. They are labeled as reference rates only.

9. **Potential Savings Calculation**:
   $$\text{Potential Allowed Savings} = \max\left(0, \text{Current Allowed/Unit} - \text{Alternative Allowed/Unit}\right) \times \text{Units}$$
   - Alternative site must have verified full-quarter in-network roster status.
   - Same HCPCS, same calendar quarter, same service setting, verified billing units.
   - Labeled as **potential opportunities pending clinical review**, never guaranteed or recoverable overpayments.

---

## 5. Security & Authorization Architecture

### 5.1 Authentication & Multi-Factor Protection
- **Session & Bearer Authentication**: Django session cookies protected with `SameSite`, `HttpOnly`, and `Secure` flags; DRF endpoints support Bearer JWT tokens.
- **Mandatory TOTP MFA**: Every user account (Admin, Analyst, Reviewer, Client, Viewer) requires Google Authenticator TOTP enrollment before gaining access.
- **Encryption at Rest**: TOTP seed secrets are encrypted at rest with Fernet (`TOTP_ENCRYPTION_KEY`). Recovery codes are stored as salted one-way hashes and consumed once.
- **Replay & Rate Limiting**: TOTP time steps are tracked to prevent replay attacks; authentication endpoints enforce exponential backoff and rate limits.
- **CSRF Protection**: All mutating HTTP requests require valid `X-CSRFToken` headers.

### 5.2 Role-Based Access Control (RBAC)

| Role | Permissions |
|---|---|
| **Super Admin / Admin** | Full access to sources, jobs, user provisioning, organization creation, plan creation, roster import, report approval sign-off. |
| **Analyst** | Can import approved aggregate summaries, create audits, run audits, generate reports, explore synthetic claims within assigned organizations. |
| **Reviewer** | Can inspect audit results, evidence lineage, submit formal reviews, approve or reject audits. |
| **Client / Viewer** | Read-only access to assigned organizations. Can view only **approved audits with generated reports** or **released Phase 1 market reports**. |

### 5.3 Tenant Isolation & Data Privacy
- **Organization-Level Scoping**: Queries for organizations, plans, imports, audits, and claims datasets filter through `visible_organizations(request.user)`. Cross-organization data leakage is blocked at the ORM layer.
- **Zero Raw Claims Exposure**: Central MeshKor systems never accept, process, or store raw claims, member IDs, patient names, or claim line numbers.
- **Privacy Suppression Enforcement**: The central API verifies that manifests declare a minimum cell size threshold ($\ge 11$) and complementary suppression. Cells below threshold are omitted.
- **Immutable Audit Logging**: Key operational and authorization actions write to `AuditEvent`. Review histories (`AuditReview`) are strictly append-only.
