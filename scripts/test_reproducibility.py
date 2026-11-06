"""Check reproducibility of market report output."""
import hashlib
import glob
import subprocess
import json

def get_hashes():
    h = {}
    for f in glob.glob("reports/*"):
        h[f] = hashlib.sha256(open(f, "rb").read()).hexdigest()
    return h

h1 = get_hashes()
subprocess.run([r".venv\Scripts\python.exe", "build_market_report.py"], check=True)
h2 = get_hashes()

print("=" * 60)
print("REPORT REPRODUCIBILITY RESULTS:")
print("=" * 60)
for f in sorted(h1):
    m = h1[f] == h2.get(f)
    print(f"{f}: {'MATCH' if m else 'DIFF'}")
    if not m:
        # Check if diff is just timestamp
        if f.endswith(".json"):
            d1 = json.load(open(f, encoding="utf-8"))
            d2 = json.load(open(f, encoding="utf-8"))
            # remove timestamp if any
            ts1 = d1.pop("generated_at", None)
            ts2 = d2.pop("generated_at", None)
            print(f"   JSON content equal without generated_at? {d1 == d2} (ts1={ts1}, ts2={ts2})")
        elif f.endswith(".md"):
            lines1 = open(f, encoding="utf-8").readlines()
            lines2 = open(f, encoding="utf-8").readlines()
            diffs = [f"L{i}: {l1.strip()} vs {l2.strip()}" for i, (l1, l2) in enumerate(zip(lines1, lines2)) if l1 != l2]
            print(f"   MD diffs count: {len(diffs)}")
            for d in diffs[:5]:
                print(f"     {d}")
