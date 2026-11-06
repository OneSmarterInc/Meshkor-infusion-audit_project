"""Comprehensive Phase 1 Reference Data Audit Script."""
import glob
import os
import duckdb
import polars as pl

con = duckdb.connect()

print("=" * 70)
print("PART D — PHASE 1 REFERENCE DATA AUDIT")
print("=" * 70)

# 1. Medicare ASP (Task 4.3)
print("\n--- 1. Medicare ASP (ref_asp.parquet) ---")
asp_stats = con.execute("""
    SELECT 
        count(*) as total_rows,
        count(DISTINCT hcpcs) as distinct_hcpcs,
        count(DISTINCT period) as distinct_periods,
        min(period) as min_period,
        max(period) as max_period,
        count(*) FILTER (WHERE payment_limit IS NULL) as null_limits,
        min(payment_limit) as min_limit,
        max(payment_limit) as max_limit
    FROM read_parquet('data/ref/ref_asp.parquet')
""").fetchone()
print(f"Total rows: {asp_stats[0]:,}")
print(f"Distinct HCPCS: {asp_stats[1]:,}")
print(f"Distinct periods (quarters): {asp_stats[2]} (from {asp_stats[3]} to {asp_stats[4]})")
print(f"Null payment limits: {asp_stats[5]}")
print(f"Payment limit range: ${asp_stats[6]:,.4f} to ${asp_stats[7]:,.2f}")

quarters = [r[0] for r in con.execute("SELECT DISTINCT period FROM read_parquet('data/ref/ref_asp.parquet') ORDER BY period").fetchall()]
print(f"Quarters list: {quarters}")

# 5 Spot checks: J0897, J1745, J2350, J9271, J9299 for 2026Q1
print("\nASP 5 Spot-checks for 2026Q1:")
spot_checks = con.execute("""
    SELECT hcpcs, description, dosage, payment_limit, period
    FROM read_parquet('data/ref/ref_asp.parquet')
    WHERE hcpcs IN ('J0897', 'J1745', 'J2350', 'J9271', 'J9299') AND period = '2026Q1'
    ORDER BY hcpcs
""").fetchall()
for sc in spot_checks:
    print(f"  {sc[0]} ({sc[1]} | {sc[2]}): ${sc[3]:.4f} [{sc[4]}]")

# 2. CCN-to-NPI Mapping (Task 4.2)
print("\n--- 2. CCN-to-NPI Mapping (ref_ccn_npi.parquet) ---")
ccn_stats = con.execute("""
    SELECT 
        count(*) as total_rows,
        count(DISTINCT ccn) as distinct_ccn,
        count(DISTINCT npi) as distinct_npi,
        count(*) FILTER (WHERE state = 'OH') as ohio_rows,
        count(DISTINCT ccn) FILTER (WHERE state = 'OH') as ohio_ccns,
        count(DISTINCT npi) FILTER (WHERE state = 'OH') as ohio_npis,
        count(*) FILTER (WHERE state = 'OH' AND npi IS NULL) as ohio_null_npis
    FROM read_parquet('data/ref/ref_ccn_npi.parquet')
""").fetchone()
print(f"Total CCN-NPI rows: {ccn_stats[0]:,}")
print(f"Distinct CCNs nationwide: {ccn_stats[1]:,}")
print(f"Distinct NPIs nationwide: {ccn_stats[2]:,}")
print(f"Ohio rows: {ccn_stats[3]:,}")
print(f"Ohio distinct CCNs: {ccn_stats[4]:,}")
print(f"Ohio distinct NPIs: {ccn_stats[5]:,}")
print(f"Ohio NULL NPIs: {ccn_stats[6]}")

# Ohio acute care hospitals
oh_acute = con.execute("""
    SELECT count(DISTINCT ccn) as acute_ccns, count(DISTINCT npi) as acute_npis
    FROM read_parquet('data/ref/ref_ccn_npi.parquet')
    WHERE state = 'OH' AND (subgroup_acute_care = 1 OR subgroup_general = 1)
""").fetchone()
print(f"Ohio Acute Care / General Hospital CCNs: {oh_acute[0]}, NPIs: {oh_acute[1]}")

# 3. NPPES Provider Reference (Task 4.1)
print("\n--- 3. NPPES Provider Reference (ref_provider) ---")
oh_prov_files = glob.glob("data/ref/ref__ref_provider__state_part=OH__data_*.parquet")
if oh_prov_files:
    f = oh_prov_files[0]
    prov_stats = con.execute(f"""
        SELECT 
            count(*) as total_providers,
            count(DISTINCT npi) as distinct_npis,
            count(*) FILTER (WHERE length(cast(npi as varchar)) != 10) as invalid_npi_len,
            count(*) FILTER (WHERE entity_type = 2) as org_type2,
            count(*) FILTER (WHERE entity_type = 1) as ind_type1,
            count(*) FILTER (WHERE deactivation_date IS NOT NULL) as deactivated
        FROM read_parquet('{f}')
    """).fetchone()
    print(f"Ohio Providers Total: {prov_stats[0]:,}")
    print(f"Distinct NPIs: {prov_stats[1]:,}")
    print(f"Invalid NPI length (!=10): {prov_stats[2]}")
    print(f"Type 2 (Organizations): {prov_stats[3]:,}")
    print(f"Type 1 (Individuals): {prov_stats[4]:,}")
    print(f"Deactivated: {prov_stats[5]:,}")
else:
    print("No Ohio provider partition found!")

# 4. NDC-HCPCS Crosswalk (Task 4.4)
print("\n--- 4. NDC-HCPCS Crosswalk (ref_ndc_hcpcs.parquet) ---")
ndc_stats = con.execute("""
    SELECT 
        count(*) as total_rows,
        count(DISTINCT hcpcs) as distinct_hcpcs,
        count(DISTINCT ndc) as distinct_ndc,
        count(DISTINCT ndc11) as distinct_ndc11,
        count(*) FILTER (WHERE length(ndc11) != 11) as invalid_ndc11_len,
        count(DISTINCT period) as distinct_periods
    FROM read_parquet('data/ref/ref_ndc_hcpcs.parquet')
""").fetchone()
print(f"Total crosswalk rows: {ndc_stats[0]:,}")
print(f"Distinct HCPCS: {ndc_stats[1]:,}")
print(f"Distinct hyphenated NDCs: {ndc_stats[2]:,}")
print(f"Distinct 11-digit NDCs: {ndc_stats[3]:,}")
print(f"Invalid 11-digit NDC length (!=11): {ndc_stats[4]}")
print(f"Distinct periods: {ndc_stats[5]}")

# 5. In-scope Drug Scope Coverage (Task 4.5 & Task 5)
print("\n--- 5. Drug Scope (drug_scope.parquet) ---")
scope_cols = [c[0] for c in con.execute("SELECT * FROM read_parquet('data/ref/drug_scope.parquet') LIMIT 0").description]
print(f"Drug scope columns: {scope_cols}")
scope_rows = con.execute("SELECT * FROM read_parquet('data/ref/drug_scope.parquet')").fetchall()
print(f"Total in-scope drugs: {len(scope_rows)}")
for r in scope_rows[:3]:
    print(f"  {dict(zip(scope_cols, r))}")

# Check ndc format in ref_ndc_hcpcs
ndc_cols = [c[0] for c in con.execute("SELECT * FROM read_parquet('data/ref/ref_ndc_hcpcs.parquet') LIMIT 0").description]
print(f"\nCrosswalk columns: {ndc_cols}")
ndc_samples = con.execute("SELECT * FROM read_parquet('data/ref/ref_ndc_hcpcs.parquet') LIMIT 3").fetchall()
for r in ndc_samples:
    print(f"  {dict(zip(ndc_cols, r))}")

# Check coverage in ASP and Crosswalk
scope_codes = [r[scope_cols.index("hcpcs")] for r in scope_rows]
phs = ",".join(f"'{c}'" for c in scope_codes)
cov = con.execute(f"""
    SELECT 
        (SELECT count(DISTINCT hcpcs) FROM read_parquet('data/ref/ref_asp.parquet') WHERE hcpcs IN ({phs})) as in_asp,
        (SELECT count(DISTINCT hcpcs) FROM read_parquet('data/ref/ref_ndc_hcpcs.parquet') WHERE hcpcs IN ({phs})) as in_crosswalk
""").fetchone()
print(f"\nIn-scope drugs in ASP: {cov[0]}/{len(scope_codes)}")
print(f"In-scope drugs in Crosswalk: {cov[1]}/{len(scope_codes)}")

print("\nPhase 1 verification script completed successfully.")
