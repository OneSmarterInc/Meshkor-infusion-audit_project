"""DuckDB access to curated Parquet outputs; SQLite stores app operations."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from pipeline.common import drugs_in_scope, paths, sha256
from pipeline.artifacts import (LEGACY_REFERENCE_ARTIFACTS, MART_ARTIFACTS,
    NORMALIZED_ARTIFACTS, REFERENCE_ARTIFACTS)

PRICED = ("ok", "review_range")       # statuses that carry a markup multiple

class DataMissing(Exception):
    pass


def files() -> dict[str, Path]:
    p = paths()
    archive = p["mart"].parent / "processed_parquet_backup"

    def stored(directory: Path, backup_group: str, name: str,
               aliases: tuple[str, ...] = ()) -> Path:
        for root in (directory, archive / backup_group):
            for candidate in (name, *aliases):
                path = root / candidate
                if path.is_file():
                    return path
        return directory / name

    return {
        "comparison": stored(p["mart"], "mart", MART_ARTIFACTS["comparison"]),
        "run_info": stored(p["mart"], "mart", MART_ARTIFACTS["run_info"]),
        "asp": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["asp"]),
        "crosswalk": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["crosswalk"],
                            LEGACY_REFERENCE_ARTIFACTS["crosswalk"]),
        "ndc_products": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["ndc_products"],
                               LEGACY_REFERENCE_ARTIFACTS["ndc_products"]),
        "ndc_packages": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["ndc_packages"],
                               LEGACY_REFERENCE_ARTIFACTS["ndc_packages"]),
        "hospitals_ref": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["hospitals"]),
        "nadac": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["nadac"]),
        "providers": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["providers"]),
        "enrollments": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["enrollments"]),
        "scope": stored(p["ref"], "ref", REFERENCE_ARTIFACTS["drug_scope"]),
        "quality": stored(p["hpt_norm"], "hpt/norm", NORMALIZED_ARTIFACTS["hospital_quality"]),
    }


def _path(name: str) -> str:
    f = files()[name]
    if not f.exists():
        raise DataMissing(f"{f.name} not found. Run: python -m pipeline.run_all")
    return f.as_posix()


# Columns written by older load_asp.py versions.
_ASP_OLD_TO_NEW = {
    "period": "quarter",
    "dosage": "dosage_text",
    "description": "short_description",
}


def _asp_read_expr(path: str, source: str) -> str:
    """Return a Parquet expression with legacy ASP columns aliased canonically."""
    con = duckdb.connect()
    try:
        cols = {row[0] for row in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall()}
    finally:
        con.close()

    old_cols = set(_ASP_OLD_TO_NEW) & cols
    aliases = ", ".join(
        f'"{old}" AS "{new}"'
        for old, new in _ASP_OLD_TO_NEW.items()
        if old in old_cols and new not in cols
    )
    parquet = f"read_parquet({source})"
    return f"(SELECT *, {aliases} FROM {parquet})" if aliases else parquet


_QUERY_BIND_TOKEN = re.compile(r"__pricing_parquet_[A-Za-z0-9_]+__|\?")


def query(sql: str, params: list | None = None, **tables: str) -> list[dict]:
    """Read from one complete snapshot, coordinating with the atomic publish window."""
    from pipeline.common import project_root
    from pipeline.snapshots import snapshot_read_mutex
    with snapshot_read_mutex(project_root()):
        return _query_snapshot(sql, params, **tables)


def _query_snapshot(sql: str, params: list | None = None, **tables: str) -> list[dict]:
    """Run SQL with parameter-bound Parquet scans; normalize legacy ASP columns."""
    if "comparison" in tables.values():
        require_current_scope()
    con = duckdb.connect()
    try:
        sub, scan_paths, materialized = {}, {}, []
        for index, (key, value) in enumerate(tables.items()):
            path = _path(value)
            marker = f"__pricing_parquet_{index}__"
            scan_paths[marker] = path
            source = _asp_read_expr(path, marker) if value == "asp" else f"read_parquet({marker})"
            if value == "comparison":
                relation = f'"_pricing_parquet_{index}"'
                materialized.append(f"{relation} AS MATERIALIZED (SELECT * FROM {source})")
                sub[key] = relation
            else:
                sub[key] = source

        values, value_index, bound = list(params or []), 0, []

        def bind(match):
            nonlocal value_index
            token = match.group(0)
            if token in scan_paths:
                bound.append(scan_paths[token])
            else:
                if value_index >= len(values):
                    return token
                bound.append(values[value_index])
                value_index += 1
            return "?"

        prefix = f"WITH {', '.join(materialized)} " if materialized else ""
        statement = _QUERY_BIND_TOKEN.sub(bind, prefix + sql.format(**sub))
        if value_index != len(values):
            raise ValueError("SQL parameter count does not match the supplied parameters.")
        cur = con.execute(statement, bound)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def run_info() -> dict:
    f = files()["run_info"]
    if not f.exists():
        raise DataMissing("run_info.json not found. Run: python -m pipeline.run_all")
    return json.loads(f.read_text(encoding="utf-8"))


_SHA_CACHE: dict[tuple, str] = {}


def comparison_sha256() -> str:
    """SHA-256 of comparison.parquet (cached by path, size and modified time)."""
    f = files()["comparison"]
    if not f.exists():
        raise DataMissing("comparison.parquet not found. Run: python -m pipeline.run_all")
    st = f.stat()
    key = (str(f), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        _SHA_CACHE.clear()
        _SHA_CACHE[key] = sha256(f)
    return _SHA_CACHE[key]


def config_sha256() -> str:
    """Fingerprint every versioned project configuration input."""
    import hashlib

    config_dir = Path(__file__).resolve().parents[2] / "config"
    digest = hashlib.sha256()
    for file_path in sorted(p for p in config_dir.rglob("*") if p.is_file()):
        relative = file_path.relative_to(config_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def drug_config() -> dict[str, dict]:
    try:
        require_current_scope()
        return {d["hcpcs"]: d for d in drugs_in_scope()}
    except FileNotFoundError as exc:
        raise DataMissing(str(exc)) from exc


def require_current_scope() -> str:
    """Validate source-backed outputs, or clearly identify a verified stored snapshot."""
    try:
        info = run_info()
        scope_rows = drugs_in_scope()
    except FileNotFoundError as exc:
        raise DataMissing(str(exc)) from exc
    if info.get("drug_selection_source") != "partb_spending":
        raise DataMissing("No current CMS Part B spend-ranked comparison is available. "
                          "Add the official source file and rerun python -m pipeline.run_all.")
    source_value = info.get("drug_selection_source_file")
    expected_hash = info.get("drug_selection_source_sha256")
    if not expected_hash:
        raise DataMissing("CMS Part B scope checksum is missing from the saved run metadata.")
    source = Path(source_value) if source_value else None
    if source and not source.is_absolute():
        from pipeline.common import PROJECT
        source = PROJECT / source
    if source and not source.is_file():
        from pipeline.common import PROJECT
        try:
            relative = source.relative_to(PROJECT)
        except ValueError:
            relative = None
        if relative and relative.parts[:2] == ("data", "raw"):
            essential_source = PROJECT / "data" / "raw_essential_inputs" / Path(*relative.parts[2:])
            if essential_source.is_file():
                source = essential_source
    source_available = bool(source and source.is_file())
    if source_available and sha256(source) != expected_hash:
        raise DataMissing("CMS Part B source has changed since this comparison was built. Rebuild the data pipeline.")
    codes = sorted(row["hcpcs"] for row in scope_rows)
    if codes != sorted(info.get("drugs", [])) or any(
        row.get("selection_source_sha256") != expected_hash for row in scope_rows
    ):
        raise DataMissing("Drug scope and comparison build do not match the current CMS Part B source.")
    scope_sources = {Path(row.get("selection_source_file") or "").name for row in scope_rows}
    run_source_name = Path(source_value or "").name
    if scope_sources != {run_source_name}:
        raise DataMissing("Saved drug scope and run metadata refer to different CMS Part B files.")
    comparison_path = files()["comparison"]
    if not comparison_path.is_file():
        raise DataMissing("comparison.parquet is missing from the active data folder and archive.")
    expected_comparison_hash = info.get("comparison_sha256")
    if expected_comparison_hash and sha256(comparison_path) != expected_comparison_hash:
        raise DataMissing("The comparison artifact does not match its run metadata; restore or rebuild a complete snapshot.")
    con = duckdb.connect()
    try:
        comparison_rows = con.execute("SELECT count(*) FROM read_parquet(?)", [comparison_path.as_posix()]).fetchone()[0]
        comparison_codes = sorted(row[0] for row in con.execute(
            "SELECT DISTINCT hcpcs FROM read_parquet(?) ORDER BY hcpcs", [comparison_path.as_posix()]
        ).fetchall())
    finally:
        con.close()
    if info.get("rows") is not None and comparison_rows != info["rows"]:
        raise DataMissing("Saved comparison row count does not match its run metadata.")
    if comparison_codes != codes:
        raise DataMissing("Saved comparison and drug scope contain different HCPCS codes.")
    return "verified_source" if source_available else "archived_snapshot"
