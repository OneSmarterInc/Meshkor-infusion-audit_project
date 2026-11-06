"""Test DuckDB OFFSET behavior on comparison.parquet."""
import duckdb

con = duckdb.connect()
parquet = "data/mart/comparison.parquet"

print("Test 1: OFFSET 0")
try:
    r = con.execute(f"SELECT markup_multiple FROM read_parquet('{parquet}') ORDER BY markup_multiple DESC NULLS LAST LIMIT 10 OFFSET 0").fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("Test 2: OFFSET 10")
try:
    r = con.execute(f"SELECT markup_multiple FROM read_parquet('{parquet}') ORDER BY markup_multiple DESC NULLS LAST LIMIT 10 OFFSET 10").fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("Test 3: CTE workaround for OFFSET 10")
try:
    r = con.execute(f"""
        WITH base AS (
            SELECT markup_multiple FROM read_parquet('{parquet}')
            ORDER BY markup_multiple DESC NULLS LAST
        )
        SELECT * FROM base LIMIT 10 OFFSET 10
    """).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")

print("Test 4: ROW_NUMBER workaround for OFFSET 10")
try:
    r = con.execute(f"""
        WITH numbered AS (
            SELECT markup_multiple,
                   ROW_NUMBER() OVER (ORDER BY markup_multiple DESC NULLS LAST) AS rn
            FROM read_parquet('{parquet}')
        )
        SELECT markup_multiple FROM numbered WHERE rn > 10 AND rn <= 20
    """).fetchall()
    print(f"  OK: {len(r)} rows")
except Exception as e:
    print(f"  ERROR: {e}")
