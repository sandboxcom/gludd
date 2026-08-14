from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_ci_shard_scratch.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("clean_ci_shard_scratch_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _age_path(path: Path, seconds: int) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_recent_ci_shard_directory_is_not_removed(tmp_path: Path) -> None:
    module = _load_module()
    active = tmp_path / "gludd-ci-shard-other-123"
    active.mkdir()
    (active / "popen-gw0").mkdir()

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert active.exists()
    assert (active / "popen-gw0").exists()
    assert result["removed"] == []
    assert f"{active}:recent" in result["skipped"]


def test_stale_ci_shard_directory_is_removed(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-ci-shard-unit-3-456"
    stale.mkdir()
    (stale / "node.log").write_text("old", encoding="utf-8")
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert not stale.exists()
    assert str(stale) in result["removed"]


def test_stale_unit_shard_directory_is_removed(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-unit-shard-2-789"
    stale.mkdir()
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert not stale.exists()
    assert str(stale) in result["removed"]


def test_inactive_gate_unit_root_is_removed(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-gate-unit-3-abcd1234"
    stale.mkdir()
    (stale / "large.bin").write_bytes(b"gate-output")
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=0,
        active_process_pids=lambda _path: [],
    )

    assert not stale.exists()
    assert result == {"removed": [str(stale)], "skipped": []}


def test_gate_unit_root_with_active_process_is_refused(tmp_path: Path) -> None:
    module = _load_module()
    active = tmp_path / "gludd-gate-unit-3-active"
    active.mkdir()

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=0,
        active_process_pids=lambda _path: [4242],
    )

    assert active.exists()
    assert result["removed"] == []
    assert result["skipped"] == [f"{active}:active-pids=4242"]


def test_process_detector_matches_candidate_in_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-gate-unit-3-live"
    candidate.mkdir()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, f"991 python -m pytest --basetemp={candidate}\n", ""
        ),
    )

    assert module._active_process_pids(candidate) == [991]


def test_process_detector_does_not_match_path_prefix_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-gate-unit-3-live"
    candidate.mkdir()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, f"992 python --basetemp={candidate}-other\n", ""
        ),
    )

    assert module._active_process_pids(candidate) == []


def test_process_inspection_error_refuses_cleanup(
    tmp_path: Path,
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-gate-unit-3-unknown"
    candidate.mkdir()

    def fail_inspection(_path: Path) -> list[int]:
        raise module.ProcessInspectionError("unavailable")

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=0,
        active_process_pids=fail_inspection,
    )

    assert candidate.exists()
    assert result["skipped"] == [f"{candidate}:process-inspection-failed"]


def test_matching_non_directory_and_disappeared_candidate_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    not_directory = tmp_path / "gludd-gate-unit-3-file"
    not_directory.write_text("not a tree", encoding="utf-8")
    disappeared = tmp_path / "gludd-gate-unit-3-gone"
    monkeypatch.setattr(
        module,
        "iter_candidates",
        lambda _root: [disappeared, not_directory],
    )

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=0)

    assert result == {"removed": [], "skipped": [f"{not_directory}:not-directory"]}


def test_remove_tree_tolerates_restrictive_mode_repair_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-gate-unit-3-modes"
    candidate.mkdir()
    (candidate / "result.bin").write_bytes(b"result")

    def fail_chmod(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated chmod race")

    monkeypatch.setattr(module.os, "chmod", fail_chmod)

    module._remove_tree(candidate)

    assert not candidate.exists()


def test_main_reports_active_refusal_and_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    active = tmp_path / "gludd-gate-unit-3-live"
    monkeypatch.setattr(
        module,
        "clean_ci_shard_scratch",
        lambda **kwargs: {"removed": [], "skipped": [f"{active}:active-pids=7"]},
    )

    assert module.main(["--tmp-root", str(tmp_path), "--min-age-seconds", "0"]) == 1
    output = capsys.readouterr().out
    assert f"skipped {active}:active-pids=7" in output
    assert "removed=0 skipped=1" in output


def test_main_reports_removed_root_and_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    removed = tmp_path / "gludd-gate-unit-3-stale"
    monkeypatch.setattr(
        module,
        "clean_ci_shard_scratch",
        lambda **kwargs: {"removed": [str(removed)], "skipped": []},
    )

    assert module.main(["--tmp-root", str(tmp_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert f"removed {removed}" in output
    assert "removed=1 skipped=0" in output


def test_dry_run_reports_stale_directory_without_removing_it(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-ci-shard-unit-2-111"
    stale.mkdir()
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        dry_run=True,
    )

    assert stale.exists()
    assert str(stale) in result["removed"]


def test_non_shard_gludd_directory_is_ignored(tmp_path: Path) -> None:
    module = _load_module()
    unrelated = tmp_path / "gludd-worktrees"
    unrelated.mkdir()

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=0)

    assert unrelated.exists()
    assert result == {"removed": [], "skipped": []}


def test_orphan_cleanup_preserves_nested_container_and_removes_only_sibling(
    tmp_path: Path,
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    container = worktree_root / "feature"
    registered = container / "active-worktree"
    orphan = container / "abandoned-output"
    (registered / ".venv").mkdir(parents=True)
    orphan.mkdir()
    (registered / ".venv" / "python").write_bytes(b"active")
    (orphan / "artifact.bin").write_bytes(b"orphan")

    result = module.clean_orphan_worktree_scratch(
        worktree_root=worktree_root,
        dry_run=False,
        active_process_pids=lambda _path: [],
        registered_worktree_paths=lambda _root: {registered.resolve()},
    )

    assert container.exists()
    assert registered.exists()
    assert not orphan.exists()
    assert result == {"eligible": [], "removed": [str(orphan)], "skipped": []}


@pytest.mark.parametrize("relationship", ["exact", "ancestor", "descendant"])
def test_orphan_cleanup_refuses_any_registration_relationship(
    tmp_path: Path, relationship: str
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    candidate = worktree_root / "candidate"
    candidate.mkdir(parents=True)
    if relationship == "exact":
        registered = candidate
    elif relationship == "ancestor":
        registered = worktree_root
    else:
        registered = candidate / "nested-registration"
        registered.mkdir()

    registry_calls = 0

    def registry(_root: Path) -> set[Path]:
        nonlocal registry_calls
        registry_calls += 1
        return set() if registry_calls == 1 else {registered.resolve()}

    def classifier(*args: object, **kwargs: object) -> list[object]:
        return [
            module.check_disk_usage.ScratchClassification(
                candidate,
                "orphan-worktree",
                observed_size_bytes=0,
                counted_size_bytes=0,
            )
        ]

    result = module.clean_orphan_worktree_scratch(
        worktree_root=worktree_root,
        dry_run=False,
        active_process_pids=lambda _path: [],
        registered_worktree_paths=registry,
        classify_worktree_children=classifier,
    )

    assert candidate.exists()
    assert result["removed"] == []
    assert result["skipped"] == [f"{candidate}:registration-conflict"]


def test_orphan_cleanup_refuses_active_process(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    candidate = worktree_root / "abandoned"
    candidate.mkdir(parents=True)

    result = module.clean_orphan_worktree_scratch(
        worktree_root=worktree_root,
        dry_run=False,
        active_process_pids=lambda _path: [7331],
        registered_worktree_paths=lambda _root: set(),
    )

    assert candidate.exists()
    assert result["removed"] == []
    assert result["skipped"] == [f"{candidate}:active-pids=7331"]


def test_orphan_cleanup_defaults_to_validation_only(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    candidate = worktree_root / "abandoned"
    candidate.mkdir(parents=True)

    result = module.clean_orphan_worktree_scratch(
        worktree_root=worktree_root,
        active_process_pids=lambda _path: [],
        registered_worktree_paths=lambda _root: set(),
    )

    assert candidate.exists()
    assert result == {"eligible": [str(candidate)], "removed": [], "skipped": []}


def test_orphan_cleanup_registry_failure_is_fail_closed(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    candidate = worktree_root / "abandoned"
    candidate.mkdir(parents=True)

    def fail_registry(_root: Path) -> set[Path]:
        raise module.check_disk_usage.WorktreeRegistryError("unavailable")

    result = module.clean_orphan_worktree_scratch(
        worktree_root=worktree_root,
        registered_worktree_paths=fail_registry,
    )

    assert candidate.exists()
    assert result == {
        "eligible": [],
        "removed": [],
        "skipped": [f"{worktree_root}:classification-failed"],
    }


def test_orphan_cleanup_cli_requires_explicit_delete_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-worktrees" / "abandoned"
    monkeypatch.setattr(
        module,
        "clean_orphan_worktree_scratch",
        lambda **kwargs: {
            "eligible": [str(candidate)],
            "removed": [],
            "skipped": [],
        },
    )

    assert module.main(["--tmp-root", str(tmp_path), "--worktree-orphans"]) == 0
    output = capsys.readouterr().out
    assert f"eligible {candidate}" in output
    assert "eligible=1 removed=0 skipped=0" in output
