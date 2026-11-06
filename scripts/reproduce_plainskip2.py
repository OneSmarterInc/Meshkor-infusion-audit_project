"""Reproduce the exact PlainSkip error using the require_current_scope() path."""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')

import duckdb
from pricing import data

# require_current_scope() validates the data then leaves it alone.
# The real error must come from the `unnest(?)` in the WHERE clause.
print("=== Testing require_current_scope() ===")
try:
    state = data.require_current_scope()
    print(f"OK: {state}")
except Exception as e:
    print(f"DataMissing: {e}")

from pipeline.common import paths
p = paths()
parquet_path = p['mart'] / 'comparison.parquet'
parquet_str = parquet_path.as_posix()

print("\n=== Testing unnest(?) in WHERE clause ===")
con = duckdb.connect()

# This is what _rate_filters generates for price_type=median_allowed
# WHERE price_type IN (SELECT unnest(?))
print("Test A: price_type IN (SELECT unnest(?))")
try:
    r = con.execute(
        f"SELECT markup_multiple FROM read_parquet('{parquet_str}') WHERE price_type IN (SELECT unnest(?)) ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0",
        [['median_allowed']]
    ).fetchall()
    print(f"  OFFSET 0: {len(r)} rows")
    r = con.execute(
        f"SELECT markup_multiple FROM read_parquet('{parquet_str}') WHERE price_type IN (SELECT unnest(?)) ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 50",
        [['median_allowed']]
    ).fetchall()
    print(f"  OFFSET 50: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

# Try the combined WHERE from _authorized_rate_filters for a non-payload user:
# WHERE price_type IN (SELECT unnest(?))
# AND coalesce(reviewed, false) AND status NOT IN (...) AND hcpcs IN (SELECT unnest(?))
print("\nTest B: combined WHERE with reviewed + status + hcpcs unnest")
drug_codes = ['J9271', 'J9144', 'J0897', 'J0178', 'J9299', 'J2777', 'J0129', 'J9173', 'J3380', 'J1569', 'J0896', 'J9022', 'J2350', 'J0177', 'J9177', 'J3111', 'J1303', 'J9358', 'J1561', 'J9228']
try:
    sql = f"""
        SELECT markup_multiple, status, price_type, hcpcs
        FROM read_parquet('{parquet_str}')
        WHERE price_type IN (SELECT unnest(?))
          AND coalesce(reviewed, false)
          AND status NOT IN ('unit_unresolved','unit_ambiguous','billing_unit_mismatch')
          AND hcpcs IN (SELECT unnest(?))
        ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0
    """
    r = con.execute(sql, [['median_allowed'], drug_codes]).fetchall()
    print(f"  OFFSET 0: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("\nTest C: reproduce actual data.query() call that views.py makes")
try:
    # This is exactly how data.query() calls it for non-payload user
    sql = "SELECT markup_multiple, hcpcs, status, price_type FROM {cmp} WHERE price_type IN (SELECT unnest(?)) ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0"
    rows = data.query(sql, [['median_allowed']], cmp='comparison')
    print(f"  OFFSET 0: {len(rows)} rows")
    rows2 = data.query(sql.replace('OFFSET 0', 'OFFSET 50'), [['median_allowed']], cmp='comparison')
    print(f"  OFFSET 50: {len(rows2)} rows")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
