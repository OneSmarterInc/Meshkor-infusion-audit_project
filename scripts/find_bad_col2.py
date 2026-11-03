"""Find minimum combination of columns that triggers PlainSkip."""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')

import duckdb
from pricing import data

parquet_path = data.files()["comparison"].as_posix()
con = duckdb.connect()

ALL_COLS = [
    "hospital_key", "hospital_label", "system", "locations", "hcpcs", "drug_name",
    "description", "ndc11", "matched_via", "setting", "billing_class", "payer_name",
    "plan_name", "payer_group", "price_type", "price", "gross_charge", "discounted_cash",
    "negotiated_dollar", "estimated_amount", "median_amount", "p10_amount", "p90_amount",
    "drug_unit", "drug_unit_type",
    "billing_units", "unit_note", "benchmark_quarter", "asp_payment_limit", "asp_dosage",
    "price_per_billing_unit", "markup_multiple", "pct_over_asp", "negotiated_percentage",
    "methodology", "file_last_updated", "reviewed", "source_file", "source_member",
    "source_sha256", "status",
]

def test_cols(cols):
    try:
        con.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{parquet_path}') LIMIT 50 OFFSET 0").fetchall()
        return True
    except Exception:
        return False

# Binary search for minimum failing set
print("Binary search for minimum column set that fails...")
# Half-halving approach
left = 0
right = len(ALL_COLS)
mid = right // 2
# Find the minimum prefix that fails
for prefix_size in range(1, len(ALL_COLS) + 1):
    if not test_cols(ALL_COLS[:prefix_size]):
        print(f"Minimum failing prefix: {prefix_size} columns")
        print(f"  Last added: {ALL_COLS[prefix_size-1]}")
        # Now test the previous set that was passing
        print(f"  Previous {prefix_size-1} cols passed, adding '{ALL_COLS[prefix_size-1]}' causes failure")
        # Find which in the first set is triggering together with the last one
        for i in range(prefix_size - 1):
            probe = [ALL_COLS[i], ALL_COLS[prefix_size-1]]
            if not test_cols(probe):
                print(f"  Pair that fails: {probe}")
        break

# Check if the issue is column count (row-group crossing) or specific column combo
print("\nTest: select 41 columns but using '*' alias workaround")
try:
    r = con.execute(f"SELECT * FROM read_parquet('{parquet_path}') LIMIT 50 OFFSET 0").fetchall()
    print(f"  SELECT * OK: {len(r)} rows")
except Exception as e:
    print(f"  SELECT * ERROR: {e}")

# Test if it's row group related — use CTE to force materialization first
print("\nTest: CTE materialization first, then OFFSET")
try:
    r = con.execute(f"""
        WITH base AS (
            SELECT {', '.join(ALL_COLS)} 
            FROM read_parquet('{parquet_path}')
            ORDER BY markup_multiple DESC NULLS LAST
        )
        SELECT * FROM base LIMIT 50 OFFSET 0
    """).fetchall()
    print(f"  CTE workaround OK: {len(r)} rows")
except Exception as e:
    print(f"  CTE ERROR: {e}")

# Test if using row_number avoids it
print("\nTest: ROW_NUMBER workaround")
try:
    r = con.execute(f"""
        WITH numbered AS (
            SELECT {', '.join(ALL_COLS)},
                   ROW_NUMBER() OVER (ORDER BY markup_multiple DESC NULLS LAST) AS _row_n
            FROM read_parquet('{parquet_path}')
        )
        SELECT {', '.join(ALL_COLS)} FROM numbered WHERE _row_n BETWEEN 1 AND 50
    """).fetchall()
    print(f"  ROW_NUMBER workaround OK: {len(r)} rows")
except Exception as e:
    print(f"  ROW_NUMBER ERROR: {e}")
