"""Phase 1 reference data validation tests according to CMS and Developer Guide specifications."""
from pathlib import Path
from decimal import Decimal
import duckdb
import pytest

REF_DIR = Path(__file__).resolve().parent.parent / "data" / "ref"


def _artifact_path(canonical: str, *legacy: str) -> Path:
    """Resolve a stored artifact using the same canonical-first policy as the app."""
    for name in (canonical, *legacy):
        candidate = REF_DIR / name
        if candidate.is_file():
            return candidate
    return REF_DIR / canonical


def _columns(con, path: Path) -> set[str]:
    return {row[0] for row in con.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [path.as_posix()]).fetchall()}


def _column(columns: set[str], *aliases: str) -> str:
    found = next((name for name in aliases if name in columns), None)
    assert found is not None, f"Expected one of {aliases}; actual Parquet columns: {sorted(columns)}"
    return found


@pytest.fixture(scope="module")
def con():
    conn = duckdb.connect()
    yield conn
    conn.close()


def test_ref_asp_quarters_and_structure(con):
    """Verify ref_asp has all 12 quarters (2024Q1 - 2026Q4) and required columns."""
    asp_path = _artifact_path("ref_asp.parquet")
    assert asp_path.exists(), "ref_asp.parquet must exist"

    cols = _columns(con, asp_path)
    _column(cols, "hcpcs")
    _column(cols, "short_description", "description")
    _column(cols, "dosage_text", "dosage")
    _column(cols, "payment_limit")
    quarter = _column(cols, "quarter", "period")
    _column(cols, "source_file")
    _column(cols, "source_sha256")

    quarters = [r[0] for r in con.execute(
        f'SELECT DISTINCT "{quarter}" FROM read_parquet(?) ORDER BY 1',
        [asp_path.as_posix()]).fetchall()]
    expected_quarters = [
        "2024Q1", "2024Q2", "2024Q3", "2024Q4",
        "2025Q1", "2025Q2", "2025Q3", "2025Q4",
        "2026Q1", "2026Q2", "2026Q3", "2026Q4",
    ]
    assert quarters == expected_quarters, f"Quarters mismatch: {quarters} vs {expected_quarters}"


def test_ref_asp_spot_checks_no_additional_6_percent(con):
    """Verify 5 ASP spot-checks match published CMS limits exactly (no additional 6% added)."""
    asp_path = _artifact_path("ref_asp.parquet")
    cols = _columns(con, asp_path)
    hcpcs = _column(cols, "hcpcs")
    payment_limit = _column(cols, "payment_limit")
    quarter = _column(cols, "quarter", "period")
    res = dict(con.execute(f"""
        SELECT "{hcpcs}", round("{payment_limit}", 4)
        FROM read_parquet(?)
        WHERE "{hcpcs}" IN ('J0897', 'J1745', 'J2350', 'J9271', 'J9299') AND "{quarter}" = '2026Q1'
    """, [asp_path.as_posix()]).fetchall())

    # Published CMS ASP limits for 2026Q1:
    assert res.get("J0897") == 29.4570, "J0897 (Prolia) mismatch"
    assert res.get("J1745") == 32.4230, "J1745 (Infliximab) mismatch"
    assert res.get("J2350") == 59.3240, "J2350 (Ocrevus) mismatch"
    assert res.get("J9271") == 59.7260, "J9271 (Keytruda) mismatch"
    assert res.get("J9299") == 33.5730, "J9299 (Opdivo) mismatch"


def test_drug_scope_coverage_in_asp_and_crosswalk(con):
    """Verify all 20 in-scope drugs appear in ASP and Crosswalk."""
    scope_path = REF_DIR / "drug_scope.parquet"
    asp_path = _artifact_path("ref_asp.parquet")
    xwalk_path = _artifact_path("ref_crosswalk.parquet", "ref_ndc_hcpcs.parquet")

    assert scope_path.exists()
    codes = [r[0] for r in con.execute(f"SELECT hcpcs FROM read_parquet('{scope_path.as_posix()}')").fetchall()]
    assert len(codes) == 20, f"Expected 20 in-scope drugs, got {len(codes)}"

    phs = ",".join(f"'{c}'" for c in codes)
    asp_cov = con.execute(f"SELECT count(DISTINCT hcpcs) FROM read_parquet('{asp_path.as_posix()}') WHERE hcpcs IN ({phs})").fetchone()[0]
    assert asp_cov == 20, f"Expected 20 drugs in ASP, got {asp_cov}"

    xwalk_cov = con.execute(f"SELECT count(DISTINCT hcpcs) FROM read_parquet('{xwalk_path.as_posix()}') WHERE hcpcs IN ({phs})").fetchone()[0]
    assert xwalk_cov == 20, f"Expected 20 drugs in Crosswalk, got {xwalk_cov}"


def test_ndc_11digit_normalization(con):
    """Verify NDC-HCPCS crosswalk contains normalized 11-digit NDCs."""
    xwalk_path = _artifact_path("ref_crosswalk.parquet", "ref_ndc_hcpcs.parquet")
    bad_ndcs = con.execute(f"""
        SELECT count(*)
        FROM read_parquet('{xwalk_path.as_posix()}')
        WHERE length(ndc11) != 11
    """).fetchone()[0]
    assert bad_ndcs == 0, f"Found {bad_ndcs} unnormalized NDCs"


def test_ccn_npi_ohio_coverage(con, monkeypatch):
    """Verify CCN-NPI mapping has Ohio acute care coverage and no null NPIs."""
    # This reference-data test targets the workspace snapshot, not the session's
    # synthetic IA_DATA_ROOT used by the API test fixtures.
    monkeypatch.delenv("IA_DATA_ROOT", raising=False)
    from pricing.data import files
    ccn_path = files()["enrollments"]
    assert ccn_path.is_file(), "CMS enrollment mapping is missing"
    cols = _columns(con, ccn_path)
    ccn = _column(cols, "ccn")
    npi = _column(cols, "npi")
    state = _column(cols, "state")
    acute = _column(cols, "acute_care", "subgroup_acute_care")
    stats = con.execute(f"""
        SELECT 
            count(*),
            count(DISTINCT "{ccn}"),
            count(DISTINCT "{npi}"),
            count(*) FILTER (WHERE "{npi}" IS NULL)
        FROM read_parquet(?)
        WHERE "{state}" = 'OH' AND upper(cast("{acute}" as varchar)) in ('Y', 'TRUE')
    """, [ccn_path.as_posix()]).fetchone()

    ohio_rows, distinct_ccn, distinct_npi, null_npi = stats
    assert ohio_rows >= 100, f"Expected Ohio acute-care enrollment rows, got {ohio_rows}"
    assert distinct_ccn >= 100, f"Expected distinct Ohio CCNs, got {distinct_ccn}"
    assert distinct_npi >= 100, f"Expected distinct Ohio NPIs, got {distinct_npi}"
    assert null_npi == 0, f"Found {null_npi} Ohio records with NULL NPI"
