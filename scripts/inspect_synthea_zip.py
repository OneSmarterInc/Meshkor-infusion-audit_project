import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

ZIP_PATH = Path("data/synthetic/synthea/raw/synthea_sample_data_csv_latest.zip")

def main():
    h = hashlib.sha256()
    with open(ZIP_PATH, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    zip_sha256 = h.hexdigest()
    zip_size = ZIP_PATH.stat().st_size

    results = {}
    with zipfile.ZipFile(ZIP_PATH, "r") as z:
        for info in z.infolist():
            filename = info.filename
            if not filename.endswith(".csv"):
                continue
            with z.open(info, "r") as bf:
                wrapper = io.TextIOWrapper(bf, encoding="utf-8-sig", errors="replace")
                reader = csv.reader(wrapper)
                header = next(reader, None)
                if not header:
                    continue
                row_count = 0
                sample_rows = []
                for row in reader:
                    row_count += 1
                    if row_count <= 3:
                        sample_rows.append(row)
                results[filename] = {
                    "compressed_bytes": info.compress_size,
                    "uncompressed_bytes": info.file_size,
                    "row_count": row_count,
                    "column_count": len(header),
                    "header": header,
                    "sample": sample_rows[0] if sample_rows else [],
                }
                print(f"{filename}: {row_count:,} rows, {len(header)} cols -> {header[:6]}")

    out_file = Path("data/synthetic/synthea/pre_conversion_inspection.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "zip_sha256": zip_sha256,
        "zip_size": zip_size,
        "files": results,
    }, indent=2), encoding="utf-8")
    print(f"\nSaved inspection to {out_file}")

if __name__ == "__main__":
    main()
