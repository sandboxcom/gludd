"""Tests for bounded, allowlisted cache resource maintenance."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import scripts.cache_resource_manager as cache_manager
from scripts.cache_resource_manager import (
    CacheResourceError,
    inventory_cache_children,
    remove_cache_child,
)


def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "Library" / "Caches"
    root.mkdir(parents=True)
    return root


def test_inventory_is_bounded_and_largest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    for name, size in (("small", 1), ("large", 16), ("medium", 8)):
        child = root / name
        child.mkdir()
        (child / "payload.bin").write_bytes(b"x" * 1024 * size)

    rows = inventory_cache_children(root, limit=2)

    assert [row.path.name for row in rows] == ["large", "medium"]
    assert all(row.allocated_bytes > 0 for row in rows)
    assert json.loads(rows[0].to_json())["path"] == str(root / "large")


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_inventory_rejects_invalid_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    root = _cache_root(tmp_path, monkeypatch)

    with pytest.raises(CacheResourceError, match="limit must be between 1 and 100"):
        inventory_cache_children(root, limit=limit)


def test_inventory_rejects_root_outside_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "arbitrary"
    root.mkdir()

    with pytest.raises(CacheResourceError, match="root is not allowlisted"):
        inventory_cache_children(root, limit=5)


def test_inventory_rejects_symlink_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    target = tmp_path / "target"
    target.mkdir()
    root.rmdir()
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(CacheResourceError, match="root must not be a symlink"):
        inventory_cache_children(root, limit=5)


def test_inventory_reports_unreadable_child_without_hiding_other_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    broken = root / "broken"
    measured = root / "measured"
    broken.mkdir()
    measured.mkdir()
    (measured / "payload.bin").write_bytes(b"x" * 4096)
    real_measure = cache_manager._allocated_bytes

    def measure(path: Path) -> int:
        if path == broken:
            raise CacheResourceError("permission denied")
        return real_measure(path)

    monkeypatch.setattr(cache_manager, "_allocated_bytes", measure)

    rows = inventory_cache_children(root, limit=2)

    assert rows[0].path == broken
    assert rows[0].status == "error"
    assert rows[0].error == "permission denied"
    assert rows[1].path == measured
    assert rows[1].status == "measured"


def test_remove_validate_only_preserves_exact_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    child = root / "tool-cache"
    child.mkdir()

    removed = remove_cache_child(root, child, apply=False)

    assert removed is False
    assert child.is_dir()


def test_remove_apply_deletes_only_exact_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    child = root / "tool-cache"
    sibling = root / "keep"
    child.mkdir()
    sibling.mkdir()

    removed = remove_cache_child(root, child, apply=True)

    assert removed is True
    assert not child.exists()
    assert sibling.is_dir()


@pytest.mark.parametrize("candidate_kind", ["root", "nested", "outside"])
def test_remove_rejects_non_child_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_kind: str,
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    direct = root / "direct"
    direct.mkdir()
    candidates = {
        "root": root,
        "nested": direct / "nested",
        "outside": tmp_path / "outside",
    }
    candidate = candidates[candidate_kind]
    if candidate_kind != "root":
        candidate.mkdir(parents=True)

    with pytest.raises(CacheResourceError, match="exact immediate child"):
        remove_cache_child(root, candidate, apply=True)


def test_remove_rejects_symlink_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    candidate = root / "link"
    candidate.symlink_to(outside, target_is_directory=True)

    with pytest.raises(CacheResourceError, match="candidate must not be a symlink"):
        remove_cache_child(root, candidate, apply=True)
    assert outside.is_dir()


def test_make_targets_are_validate_first_and_contracted() -> None:
    project_root = Path(__file__).resolve().parents[2]
    makefile = (project_root / "Makefile").read_text(encoding="utf-8")
    contract = json.loads((project_root / "config" / "make_target_contract.json").read_text(encoding="utf-8"))

    assert "cache-resource-inventory:" in makefile
    assert "cache-resource-remove:" in makefile
    assert "CACHE_RESOURCE_VALIDATE_ONLY ?= 1" in makefile
    entries = {entry["name"]: entry for entry in contract["targets"]}
    assert "cache-resource-inventory" in entries
    assert "cache-resource-remove" in entries
    assert "CACHE_RESOURCE_VALIDATE_ONLY=1" in entries["cache-resource-remove"]["behavior"]


@pytest.mark.parametrize("target", ["cache-resource-inventory", "cache-resource-remove"])
def test_validate_only_make_targets_execute_path_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("HOME", str(tmp_path))
    if target == "cache-resource-inventory":
        root = tmp_path / ".cache"
        root.mkdir()
        root.rmdir()
        expected_error = "root is not a directory"
    else:
        root = _cache_root(tmp_path, monkeypatch)
        expected_error = "candidate does not exist"
    command = [
        "make",
        target,
        f"CACHE_RESOURCE_ROOT={root}",
        "CACHE_RESOURCE_VALIDATE_ONLY=1",
    ]
    if target == "cache-resource-remove":
        command.append(f"CACHE_RESOURCE_CANDIDATE={root / 'missing'}")

    result = subprocess.run(
        command,
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr


def test_script_runs_under_make_system_python() -> None:
    project_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            "/usr/bin/python3",
            str(project_root / "scripts" / "cache_resource_manager.py"),
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Inventory and remove allowlisted" in result.stdout


def test_worktree_cache_cleanup_reaches_nested_feature_branches() -> None:
    project_root = Path(__file__).resolve().parents[2]
    makefile = (project_root / "Makefile").read_text(encoding="utf-8")
    venv_section = makefile.split("clean-worktree-venvs:", 1)[1].split("clean-worktree-caches:", 1)[0]
    cache_section = makefile.split("clean-worktree-caches:", 1)[1].split("molecule-clean:", 1)[0]

    # Venv cleanup is registry-driven: delegated to
    # scripts/clean_worktree_venvs.py, which enumerates every registered
    # worktree (nested feature branches included) and preserves the invoking
    # worktree and any worktree with a visible active process.
    assert "$(SYSTEM_PYTHON) -m scripts.clean_worktree_venvs" in venv_section
    assert "/tmp/gludd-worktrees/*/.venv" not in venv_section
    # Cache cleanup still uses a recursive find so nested feature-branch
    # caches are reached rather than only one level of shell glob.
    assert "/usr/bin/find /tmp/gludd-worktrees -type d" in cache_section
    assert "/tmp/gludd-worktrees/*/.pytest_cache" not in cache_section


def test_read_only_resource_targets_do_not_bootstrap_uv() -> None:
    project_root = Path(__file__).resolve().parents[2]
    makefile = (project_root / "Makefile").read_text(encoding="utf-8")
    no_sync = makefile.split("_NO_UV_SYNC_GOALS :=", 1)[1].split("ifneq", 1)[0]
    no_sync_targets = set(no_sync.replace("\\", "").split())

    for target in (
        "grep",
        "cache-resource-inventory",
        "cache-resource-remove",
        "clean",
        "clean-artifacts",
        "development-merge-forward",
        "development-merge-forward-batch",
        "git-show-file-to",
        "rm-files",
        "agent-worktree",
        "agent-worktree-base",
    ):
        assert target in no_sync_targets


def test_measurement_failure_is_bounded_and_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    child = tmp_path / "cache"
    child.mkdir()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "invalid", ""),
    )

    with pytest.raises(CacheResourceError, match="could not measure cache child"):
        cache_manager._allocated_bytes(child)


def test_remove_apply_deletes_exact_file_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    child = root / "single-cache-file"
    child.write_text("regenerable", encoding="utf-8")

    assert remove_cache_child(root, child, apply=True) is True
    assert not child.exists()


def test_remove_rejects_missing_exact_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cache_root(tmp_path, monkeypatch)

    with pytest.raises(CacheResourceError, match="candidate does not exist"):
        remove_cache_child(root, root / "missing", apply=True)


def test_main_inventory_emits_json_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    child = root / "tool"
    child.mkdir()
    monkeypatch.setattr(cache_manager, "_allocated_bytes", lambda _path: 4096)

    assert cache_manager.main(["inventory", "--root", str(root), "--limit", "1"]) == 0

    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[0]["path"] == str(child)
    assert output[0]["allocated_bytes"] == 4096
    assert output[1] == {"entries": 1, "status": "complete"}


def test_main_remove_validate_only_is_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    child = root / "tool"
    child.mkdir()

    assert cache_manager.main(["remove", "--root", str(root), "--candidate", str(child)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "candidate": str(child),
        "removed": False,
        "status": "complete",
    }
    assert child.is_dir()


def test_main_reports_fail_closed_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "not-allowlisted"
    root.mkdir()

    assert cache_manager.main(["inventory", "--root", str(root)]) == 2
    assert "ERROR: root is not allowlisted" in capsys.readouterr().err
