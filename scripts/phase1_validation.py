"""Phase 1 & 5 reference data validation checks."""
import duckdb
import sys

con = duckdb.connect()

print("=" * 60)
print("TASK P: comparison.parquet status breakdown")
print("=" * 60)
res = con.execute(
    "SELECT status, count(*) as cnt "
    "FROM read_parquet('data/mart/comparison.parquet') "
    "GROUP BY status ORDER BY cnt DESC"
).fetchall()
for r in res:
    print(f"  {r[0]}: {r[1]:,}")

print()
print("=" * 60)
print("TASK H: ASP spot-check 2026Q1")
print("=" * 60)
codes = "J9271,J9299,J1745,J0897,J2350".split(",")
placeholders = ",".join(f"'{c}'" for c in codes)
res = con.execute(
    f"SELECT hcpcs, period, payment_limit "
    f"FROM read_parquet('data/ref/ref_asp.parquet') "
    f"WHERE hcpcs IN ({placeholders}) AND period='2026Q1' "
    f"ORDER BY hcpcs"
).fetchall()
for r in res:
    print(f"  {r}")

print()
print("=" * 60)
print("TASK I: Drug scope HCPCS coverage in ASP")
print("=" * 60)
drug_rows = con.execute(
    "SELECT hcpcs, review_status FROM read_parquet('data/ref/drug_scope.parquet')"
).fetchall()
drug_hcpcs = [r[0] for r in drug_rows]
print(f"  Drug scope count: {len(drug_hcpcs)}")
print(f"  HCPCS: {drug_hcpcs}")

phs = ",".join(f"'{h}'" for h in drug_hcpcs)
in_asp = con.execute(
    f"SELECT DISTINCT hcpcs FROM read_parquet('data/ref/ref_asp.parquet') "
    f"WHERE hcpcs IN ({phs})"
).fetchall()
in_asp_set = {r[0] for r in in_asp}
missing = [h for h in drug_hcpcs if h not in in_asp_set]
print(f"  In ASP: {len(in_asp_set)} / {len(drug_hcpcs)}")
print(f"  Missing from ASP: {missing if missing else 'NONE - all covered'}")

print()
print("=" * 60)
print("TASK L: NDC-HCPCS crosswalk coverage for in-scope drugs")
print("=" * 60)
ndc_rows = con.execute(
    f"SELECT DISTINCT hcpcs FROM read_parquet('data/ref/ref_ndc_hcpcs.parquet') "
    f"WHERE hcpcs IN ({phs})"
).fetchall()
ndc_covered = {r[0] for r in ndc_rows}
ndc_missing = [h for h in drug_hcpcs if h not in ndc_covered]
print(f"  In NDC crosswalk: {len(ndc_covered)} / {len(drug_hcpcs)}")
print(f"  Missing from NDC crosswalk: {ndc_missing if ndc_missing else 'NONE'}")

print()
print("=" * 60)
print("TASK K: CCN-NPI mapping quality for Ohio hospitals")
print("=" * 60)
try:
    oh_rows = con.execute(
        "SELECT count(*) as total, "
        "count(DISTINCT ccn) as distinct_ccn, "
        "count(DISTINCT npi) as distinct_npi "
        "FROM read_parquet('data/ref/ref_ccn_npi.parquet') "
        "WHERE state='OH'"
    ).fetchone()
    print(f"  Ohio rows: {oh_rows[0]}, distinct CCN: {oh_rows[1]}, distinct NPI: {oh_rows[2]}")
    unmapped = con.execute(
        "SELECT count(*) FROM read_parquet('data/ref/ref_ccn_npi.parquet') "
        "WHERE state='OH' AND (npi IS NULL OR npi='')"
    ).fetchone()
    print(f"  Ohio CCNs without NPI: {unmapped[0]}")
except Exception as e:
    print(f"  ERROR: {e}")
    # Try without state filter - maybe column is named differently
    cols = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='ref_ccn_npi'"
    ).fetchall()
    print(f"  Columns: {[c[0] for c in cols]}")

print()
print("=" * 60)
print("TASK J: NPI format check in ref_provider (OH)")
print("=" * 60)
try:
    import glob, os
    oh_files = glob.glob("data/ref/ref__ref_provider__state_part=OH__data_*.parquet")
    if oh_files:
        f = oh_files[0]
        npi_res = con.execute(
            f"SELECT count(*) as total, "
            f"count(*) FILTER (WHERE length(cast(npi as varchar))!=10) as bad_length, "
            f"count(*) FILTER (WHERE entity_type_code='2') as type2_orgs "
            f"FROM read_parquet('{f}')"
        ).fetchone()
        print(f"  OH provider file: {os.path.basename(f)}")
        print(f"  Total rows: {npi_res[0]}, Bad NPI length: {npi_res[1]}, Type-2 orgs: {npi_res[2]}")
    else:
        print("  No OH provider parquet found")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("Done.")
