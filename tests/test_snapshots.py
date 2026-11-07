from pathlib import Path

import pytest

from pipeline import snapshots


def _write_replacement(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def test_snapshot_publication_rolls_back_partial_swap_and_commits_complete_set(tmp_path, monkeypatch):
    project = tmp_path / "project"
    relatives = ("data/ref", "data/hpt/norm", "data/mart", "reports")
    for relative in relatives:
        target = project / relative
        target.mkdir(parents=True)
        (target / "marker.txt").write_text("old", encoding="utf-8")

    stage = snapshots.create_stage(project)
    for relative in relatives:
        _write_replacement(stage / relative / "marker.txt", "staged")

    original_replace = snapshots.os.replace
    failed = False

    def replace_with_one_interruption(source, destination):
        nonlocal failed
        if (not failed and "pipeline-staging" in Path(source).as_posix()
                and Path(destination).as_posix().endswith("/data/mart")):
            failed = True
            raise OSError("simulated interrupted publication")
        return original_replace(source, destination)

    monkeypatch.setattr(snapshots.os, "replace", replace_with_one_interruption)
    with pytest.raises(OSError, match="simulated interrupted publication"):
        snapshots.publish_stage(project, stage)
    assert all((project / relative / "marker.txt").read_text(encoding="utf-8") == "old"
               for relative in relatives)

    monkeypatch.setattr(snapshots.os, "replace", original_replace)
    stage = snapshots.create_stage(project)
    for relative in relatives:
        _write_replacement(stage / relative / "marker.txt", "new")
    snapshots.publish_stage(project, stage)
    assert all((project / relative / "marker.txt").read_text(encoding="utf-8") == "new"
               for relative in relatives)
    assert not snapshots.snapshot_journal(project).exists()


def test_pipeline_outputs_can_target_an_isolated_stage_without_moving_raw_inputs(tmp_path, monkeypatch):
    project = tmp_path / "project"
    output = tmp_path / "staging"
    monkeypatch.setenv("IA_DATA_ROOT", str(project))
    monkeypatch.setenv("IA_OUTPUT_ROOT", str(output))

    from pipeline.common import paths
    resolved = paths()
    assert resolved["raw"] == project / "data" / "raw_essential_inputs"
    assert resolved["ref"] == output / "data" / "ref"
    assert resolved["hpt_norm"] == output / "data" / "hpt" / "norm"
    assert resolved["mart"] == output / "data" / "mart"


def test_pipeline_mutex_supports_nonblocking_liveness_probe(tmp_path):
    project = tmp_path / "project"
    with snapshots.pipeline_mutex(project, blocking=False) as acquired:
        assert acquired is True
    with snapshots.pipeline_mutex(project, blocking=False) as acquired_after_release:
        assert acquired_after_release is True
