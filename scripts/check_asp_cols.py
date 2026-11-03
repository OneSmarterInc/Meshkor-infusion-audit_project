"""Check ASP parquet column names and quarter vs period discrepancy."""
import duckdb
con = duckdb.connect()

res = con.execute("SELECT * FROM read_parquet('data/ref/ref_asp.parquet') LIMIT 0").description
print("ASP columns:", [c[0] for c in res])
# check what column has the quarter values
sample = con.execute("SELECT * FROM read_parquet('data/ref/ref_asp.parquet') LIMIT 3").fetchall()
col_names = [c[0] for c in con.execute("SELECT * FROM read_parquet('data/ref/ref_asp.parquet') LIMIT 0").description]
print("Column names:", col_names)
print("Sample row 0:", dict(zip(col_names, sample[0])))

# Also check run_info.json
import json
info = json.loads(open('data/mart/run_info.json').read())
print("run_info asp_quarters:", info.get('asp_quarters'))
print("run_info generated_at:", info.get('generated_at'))
