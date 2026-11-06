"""Replicate exact query from views.py rates() to reproduce the PlainSkip error."""
import duckdb, sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')

from pipeline.common import paths

p = paths()
parquet_path = str(p['mart'] / 'comparison.parquet').replace('\\', '/')

# Replicate the _asp_read_expr wrapper from data.py
con = duckdb.connect()

# Inspect comparison parquet columns
cols_desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')").fetchall()
cols = [c[0] for c in cols_desc]
print("Columns:", cols)

# Try common queries that the view would issue
print("\nTest 1: sort by markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0")
try:
    r = con.execute(f"SELECT markup_multiple, status, hcpcs, hospital_label FROM read_parquet('{parquet_path}') ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0").fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 2: with require_current_scope() style (the actual sub expression from data.py)")
# data.py wraps the parquet in: "(SELECT *, aliases FROM read_parquet(...))" for ASP
# but for comparison it's just read_parquet directly:
try:
    sub = f"read_parquet('{parquet_path}')"
    sql = f"SELECT markup_multiple, status, hcpcs, hospital_label FROM {sub} ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0"
    r = con.execute(sql, []).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 3: with status filter")
try:
    r = con.execute(
        f"SELECT markup_multiple, status, hcpcs FROM read_parquet('{parquet_path}') WHERE status != ? ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0",
        ['unit_unresolved']
    ).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 4: median_allowed price_type filter + OFFSET 0")
try:
    r = con.execute(
        f"SELECT markup_multiple, price_type, status FROM read_parquet('{parquet_path}') WHERE price_type=? ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0",
        ['median_allowed']
    ).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nTest 5: all_columns + OFFSET 0 (like views.py cols)")
# Get all column names from the allowed set in views.py
try:
    all_cols = ", ".join(cols[:15])  # first 15 columns
    r = con.execute(
        f"SELECT {all_cols} FROM read_parquet('{parquet_path}') ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0"
    ).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")
