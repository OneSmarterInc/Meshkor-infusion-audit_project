"""Reproduce the exact query from views.py rates() with ALL columns and params."""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')

from pricing import data

RATE_COLUMNS = [
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

available = set(data.files()["comparison"].stat() and [r[0] for r in __import__('duckdb').connect().execute("DESCRIBE SELECT * FROM read_parquet(?)", [data.files()["comparison"].as_posix()]).fetchall()])

import duckdb as dk
con2 = dk.connect()
available = {r[0] for r in con2.execute("DESCRIBE SELECT * FROM read_parquet(?)", [data.files()["comparison"].as_posix()]).fetchall()}

cols = [c for c in RATE_COLUMNS if c in available]
cols_str = ", ".join(cols)

print(f"Columns used: {len(cols)}")

# Test 1: No where clause
print("\nTest 1: No WHERE, all RATE_COLUMNS, OFFSET 0")
try:
    rows = data.query(
        f"SELECT {cols_str} FROM {{cmp}} ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0",
        [], cmp="comparison"
    )
    print(f"  OK: {len(rows)} rows")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

# Test 2: with price_type filter (unnest)
print("\nTest 2: price_type filter, all RATE_COLUMNS, OFFSET 0")
try:
    rows = data.query(
        f"SELECT {cols_str} FROM {{cmp}} WHERE price_type IN (SELECT unnest(?)) ORDER BY markup_multiple DESC NULLS LAST LIMIT 50 OFFSET 0",
        [['median_allowed']], cmp="comparison"
    )
    print(f"  OK: {len(rows)} rows")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("\nTest 3: exact match — sort=markup_multiple, order=DESC, all cols, OFFSET 0")
sort = "markup_multiple"
order = "DESC"
limit = 50
offset = 0
where = "WHERE price_type IN (SELECT unnest(?))"
params = [['median_allowed']]
try:
    total = data.query(f"SELECT count(*) AS n FROM {{cmp}} {where}", params, cmp="comparison")[0]["n"]
    print(f"  Total: {total}")
    rows = data.query(
        f"SELECT {cols_str} FROM {{cmp}} {where} ORDER BY {sort} {order} NULLS LAST LIMIT {limit} OFFSET {offset}",
        params, cmp="comparison"
    )
    print(f"  Rows: {len(rows)}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")
