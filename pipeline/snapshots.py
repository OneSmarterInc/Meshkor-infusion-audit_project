"""Cross-process pipeline lock and recoverable publication of complete data snapshots."""
from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path


JOURNAL_NAME = "pipeline-snapshot-transaction.json"


@contextmanager
def pipeline_mutex(project: Path, *, blocking: bool = True):
    """Lock shared pipeline work; yield False when a nonblocking probe finds it busy."""
    lock_path = project / "data" / "operational" / "pipeline-jobs.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            while True:
                try:
                    mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                    msvcrt.locking(lock_file.fileno(), mode, 1)
                    break
                except OSError:
                    if not blocking:
                        yield False
                        return
                    time.sleep(0.2)
            try:
                yield True
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(lock_file.fileno(), mode)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def snapshot_journal(project: Path) -> Path:
    return project / "data" / "operational" / JOURNAL_NAME


@contextmanager
def snapshot_read_mutex(project: Path):
    """Coordinate only with the short publication window, not the long ETL build."""
    lock_path = project / "data" / "operational" / "pipeline-snapshot-publish.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_snapshot_consistent(project: Path) -> None:
    """Wait for publication to finish and recover a crashed directory swap if needed."""
    journal = snapshot_journal(project)
    if not journal.exists():
        return
    with pipeline_mutex(project):
        recover_snapshot(project)


def recover_snapshot(project: Path) -> None:
    journal = snapshot_journal(project)
    if not journal.exists():
        return
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
        backup_root = project / payload["backup_root"]
        stage_root = project / payload["stage_root"]
        entries = payload["entries"]
        if payload.get("state") == "committed":
            _remove(backup_root)
            _remove(stage_root)
            journal.unlink(missing_ok=True)
            return
        for item in reversed(entries):
            target = project / item["target"]
            backup = project / item["backup"]
            staged = project / item["staged"]
            if backup.exists():
                _remove(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
            elif not item["had_previous"] and not staged.exists():
                _remove(target)
        _remove(backup_root)
        _remove(stage_root)
        journal.unlink(missing_ok=True)
    except Exception:
        # Keep the journal and backups intact for manual recovery if rollback itself fails.
        raise


def create_stage(project: Path) -> Path:
    """Clone current snapshot directories using hardlinks, then write only into the clone."""
    operational = project / "data" / "operational"
    stage_root = operational / "pipeline-staging" / f"run-{os.getpid()}-{time.time_ns()}"
    for relative in ("data/ref", "data/hpt/norm", "data/mart", "reports"):
        source = project / relative
        destination = stage_root / relative
        if source.is_dir():
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_function = shutil.copy2 if relative == "reports" else _link_or_copy
            shutil.copytree(source, destination, copy_function=copy_function)
        else:
            destination.mkdir(parents=True, exist_ok=True)
    return stage_root


def publish_stage(project: Path, stage_root: Path) -> None:
    with snapshot_read_mutex(project):
        _publish_stage_locked(project, stage_root)


def _publish_stage_locked(project: Path, stage_root: Path) -> None:
    """Swap all visible data directories, rolling back the full set after any crash."""
    operational = project / "data" / "operational"
    backup_root = operational / "pipeline-backups" / stage_root.name
    backup_root.mkdir(parents=True, exist_ok=False)
    relatives = ("data/ref", "data/hpt/norm", "data/mart", "reports")
    entries = [{
        "target": relative,
        "staged": (stage_root / relative).relative_to(project).as_posix(),
        "backup": (backup_root / relative).relative_to(project).as_posix(),
        "had_previous": (project / relative).exists(),
    } for relative in relatives]
    payload = {
        "state": "publishing", "stage_root": stage_root.relative_to(project).as_posix(),
        "backup_root": backup_root.relative_to(project).as_posix(), "entries": entries,
    }
    journal = snapshot_journal(project)
    temporary = journal.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, journal)
    try:
        for item in entries:
            target, staged, backup = (project / item[key] for key in ("target", "staged", "backup"))
            backup.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        payload["state"] = "committed"
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, journal)
    except Exception:
        recover_snapshot(project)
        raise
    recover_snapshot(project)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination
