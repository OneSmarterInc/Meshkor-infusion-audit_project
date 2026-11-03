"""Fix the CCN-NPI and NPI format checks with correct column names."""
import duckdb
import glob, os

con = duckdb.connect()

print("=" * 60)
print("TASK K: CCN-NPI Ohio hospitals - correct check")
print("=" * 60)
# Get actual columns first
cols = con.execute(
    "SELECT * FROM read_parquet('data/ref/ref_ccn_npi.parquet') LIMIT 0"
).description
print(f"  Columns: {[c[0] for c in cols]}")
oh_rows = con.execute(
    "SELECT count(*) as total, "
    "count(DISTINCT ccn) as distinct_ccn, "
    "count(DISTINCT npi) as distinct_npi "
    "FROM read_parquet('data/ref/ref_ccn_npi.parquet') "
    "WHERE state='OH'"
).fetchone()
print(f"  Ohio rows: {oh_rows[0]}, distinct CCN: {oh_rows[1]}, distinct NPI: {oh_rows[2]}")
# Check for nulls with proper cast
null_check = con.execute(
    "SELECT count(*) FROM read_parquet('data/ref/ref_ccn_npi.parquet') "
    "WHERE state='OH' AND npi IS NULL"
).fetchone()
print(f"  Ohio CCNs with NULL NPI: {null_check[0]}")
# sample
sample = con.execute(
    "SELECT ccn, npi, facility_name FROM read_parquet('data/ref/ref_ccn_npi.parquet') "
    "WHERE state='OH' LIMIT 5"
).fetchall()
print("  Sample Ohio rows:")
for r in sample:
    print(f"    {r}")

print()
print("=" * 60)
print("TASK J: NPI format check in ref_provider (OH)")
print("=" * 60)
oh_files = glob.glob("data/ref/ref__ref_provider__state_part=OH__data_*.parquet")
if oh_files:
    f = oh_files[0]
    cols = con.execute(f"SELECT * FROM read_parquet('{f}') LIMIT 0").description
    print(f"  OH provider file: {os.path.basename(f)}")
    print(f"  Columns: {[c[0] for c in cols]}")
    npi_res = con.execute(
        f"SELECT count(*) as total, "
        f"count(*) FILTER (WHERE length(cast(npi as varchar))!=10) as bad_length, "
        f"count(*) FILTER (WHERE entity_type='NPI-2') as type2_orgs, "
        f"count(*) FILTER (WHERE entity_type='NPI-1') as type1_ind "
        f"FROM read_parquet('{f}')"
    ).fetchone()
    print(f"  Total rows: {npi_res[0]}, Bad NPI length: {npi_res[1]}, Type-2 orgs: {npi_res[2]}, Type-1 ind: {npi_res[3]}")
else:
    print("  No OH provider parquet found")

print()
print("=" * 60)
print("TASK E: parquet_file_mapping.csv purpose check")
print("=" * 60)
import csv
with open("parquet_file_mapping.csv", newline="", encoding="utf-8") as f2:
    reader = csv.DictReader(f2)
    rows = list(reader)
    print(f"  Total rows: {len(rows)}")
    print(f"  Columns: {list(rows[0].keys()) if rows else 'none'}")
    for r in rows[:3]:
        print(f"  {r}")

print()
print("=" * 60)
print("TASK G: requirements.txt vs installed versions")
print("=" * 60)
import subprocess, sys
for pkg in ["duckdb", "pyarrow", "django", "djangorestframework"]:
    res = subprocess.run(
        [sys.executable, "-c", f"import importlib.metadata; print(importlib.metadata.version('{pkg}'))"],
        capture_output=True, text=True
    )
    installed = res.stdout.strip()
    print(f"  {pkg}: installed={installed}")
