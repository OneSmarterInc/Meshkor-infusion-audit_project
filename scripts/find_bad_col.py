"""Binary search to find which column triggers PlainSkip error."""
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

# Binary search
print("Binary search for problematic column(s)...")
bad_cols = []
for col in ALL_COLS:
    ok = test_cols([col])
    if not ok:
        print(f"  BAD: {col}")
        bad_cols.append(col)
    else:
        pass

print(f"\nBad columns (alone): {bad_cols}")

# Now test pairs / combinations
print("\nTest: all except bad_cols individually")
for col in ALL_COLS:
    subset = [c for c in ALL_COLS if c != col]
    ok = test_cols(subset)
    if not ok:
        print(f"  Still fails without {col}")
    else:
        print(f"  PASSES without: {col}  <-- removing this fixes it")
