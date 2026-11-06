import duckdb
con = duckdb.connect()
path = 'tmp_pytest/run7/ia_test_data0/data/mart/comparison.parquet'
con.execute(f"SELECT DISTINCT hospital_label, hcpcs, benchmark_quarter, price_per_billing_unit, price_type, status, setting FROM '{path}' WHERE hcpcs='J9271'")
for row in con.fetchall():
    print(row)
