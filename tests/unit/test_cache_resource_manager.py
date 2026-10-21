"""Tests for bounded, allowlisted cache resource maintenance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest
import scripts.cache_resource_manager as cache_manager
from scripts.cache_resource_manager import (
    CacheResourceError,
    inventory_cache_children,
    remove_cache_child,
)
from scripts.makefile_layout import compose_makefile


def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".cache"
    root.mkdir(parents=True)
    return root


def test_project_temp_root_is_inventory_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "tmp"
    root.mkdir()
    child = root / "gludd-resources"
    child.mkdir()
    (child / "payload.bin").write_bytes(b"x" * 4096)

    rows = inventory_cache_children(root, limit=5)

    assert [row.path for row in rows] == [child]
    with pytest.raises(CacheResourceError, match="root is not allowlisted for removal"):
        remove_cache_child(root, child, apply=False)


def test_project_temp_immediate_child_is_inventory_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    project_root = temp_root / "gludd-state-501"
    project_root.mkdir()
    payload = project_root / "sessions"
    payload.mkdir()

    rows = inventory_cache_children(project_root, limit=5)

    assert [row.path for row in rows] == [payload]
    assert inventory_cache_children(payload, limit=5) == []
    with pytest.raises(CacheResourceError, match="root is not allowlisted for removal"):
        remove_cache_child(project_root, payload, apply=False)


def test_project_namespace_root_is_last_inventory_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    temp_root = tmp_path / "tmp"
    namespace = temp_root / "gludd-state-501" / "development-abc"
    namespace.mkdir(parents=True)
    payload = namespace / "coverage"
    payload.mkdir()

    rows = inventory_cache_children(namespace, limit=5)

    assert [row.path for row in rows] == [payload]
    with pytest.raises(CacheResourceError, match="root is not allowlisted"):
        inventory_cache_children(payload, limit=5)


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

    def measure(path: Path, *, timeout: float = 30.0) -> int:
        if path == broken:
            raise CacheResourceError("permission denied")
        return real_measure(path, timeout=timeout)

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


def test_project_gate_logs_are_inventory_only_protected_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".gate-logs"
    root.mkdir()
    generated = root / "ci-artifacts"
    retained = root / "gate-status.json"
    generated.mkdir()
    retained.write_text("retained", encoding="utf-8")
    monkeypatch.setattr(
        cache_manager,
        "_project_gate_logs_root",
        lambda: root,
        raising=False,
    )

    assert {row.path for row in inventory_cache_children(root, limit=20)} == {generated, retained}
    with pytest.raises(CacheResourceError, match="root is not allowlisted for removal"):
        remove_cache_child(root, generated, apply=True)
    assert generated.is_dir()
    assert retained.read_text(encoding="utf-8") == "retained"


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
    makefile = compose_makefile(project_root / "Makefile")
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
    makefile = compose_makefile(project_root / "Makefile")
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
    makefile = compose_makefile(project_root / "Makefile")
    assert "override UV_NO_SYNC := 1" in makefile
    assert "export UV_NO_SYNC" in makefile
    assert "override UV := echo" not in makefile

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
        assert f"\n{target}:" in makefile


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
    monkeypatch.setattr(cache_manager, "_allocated_bytes", lambda _path, **_kwargs: 4096)

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


def test_inventory_entry_budget_is_incomplete_and_starts_no_extra_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    for index in range(3):
        (root / str(index)).mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "4\tfixture\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_ENTRIES", 2, raising=False)
    assert cache_manager.main(["inventory", "--root", str(root), "--limit", "2"]) == 2
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert len(calls) == 2
    assert rows[-1] == {"entries": 2, "status": "incomplete"}
    assert all(row["status"] == "measured" for row in rows[:-1])
    assert "entry bound" in captured.err
    assert all(child.is_dir() for child in root.iterdir())


def test_inventory_global_deadline_stops_before_another_child_and_announces_work_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    for index in range(3):
        (root / str(index)).mkdir()
    clock = [0.0]
    timeouts: list[object] = []
    progress: list[str] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        progress.append(capsys.readouterr().err)
        timeouts.append(kwargs["timeout"])
        clock[0] = 1.0
        return subprocess.CompletedProcess(command, 0, "4\tfixture\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cache_manager, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_SECONDS", 1.0, raising=False)
    assert cache_manager.main(["inventory", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    assert timeouts == [1.0]
    assert "cache inventory start" in progress[0]
    assert "deadline" in captured.err
    assert json.loads(captured.out.splitlines()[-1]) == {"entries": 1, "status": "incomplete"}


def test_inventory_passes_only_remaining_global_time_to_each_native_du(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    for index in range(2):
        (root / str(index)).mkdir()
    clock = [0.0]
    timeouts: list[object] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["/usr/bin/du", "-sk"]
        timeouts.append(kwargs["timeout"])
        clock[0] += 0.4
        return subprocess.CompletedProcess(command, 0, "4\tfixture\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cache_manager, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_SECONDS", 1.0, raising=False)
    assert len(inventory_cache_children(root, limit=2)) == 2
    assert timeouts == pytest.approx([1.0, 0.6])


def test_inventory_measurement_failure_never_reports_successful_complete_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    (root / "unreadable").mkdir()

    def denied(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, stderr="owned fixture denial")

    monkeypatch.setattr(subprocess, "run", denied)
    assert cache_manager.main(["inventory", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert rows[0]["status"] == "error" and "could not measure" in rows[0]["error"]
    assert rows[-1] == {"entries": 1, "status": "incomplete"}
    assert "measurement errors" in captured.err


def test_inventory_expired_validation_opens_no_directory_iterator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    clock = [0.0]
    opened: list[Path] = []
    real_validate = cache_manager._validate_root
    real_scandir = os.scandir

    def validate(path: Path) -> Path:
        result = real_validate(path)
        clock[0] = 1.0
        return result

    def scan(path: Path) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        opened.append(path)
        return real_scandir(path)

    monkeypatch.setattr(cache_manager, "_validate_root", validate)
    monkeypatch.setattr(cache_manager, "scandir", scan)
    monkeypatch.setattr(cache_manager, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_SECONDS", 1.0)
    with pytest.raises(CacheResourceError, match="deadline"):
        inventory_cache_children(root, limit=1)
    assert opened == []


def test_exact_entry_budget_can_finish_and_progress_is_not_one_line_per_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    for index in range(8):
        (root / str(index)).mkdir()
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_ENTRIES", 8)
    monkeypatch.setattr(cache_manager, "monotonic", lambda: 0.0)
    monkeypatch.setattr(cache_manager, "_allocated_bytes", lambda _path, **_kwargs: 4096)
    assert cache_manager.main(["inventory", "--root", str(root), "--limit", "2"]) == 0
    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 3
    assert "inspected=8 errors=0" in captured.err
    assert json.loads(captured.out.splitlines()[-1]) == {"entries": 2, "status": "complete"}


def test_late_scan_error_closes_iterator_and_keeps_partial_rows_explicitly_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    (root / "first").mkdir()
    real_scandir = os.scandir
    closed: list[bool] = []

    @contextmanager
    def scan(path: Path) -> Iterator[Iterator[os.DirEntry[str]]]:
        with real_scandir(path) as source:
            def fail_later() -> Iterator[os.DirEntry[str]]:
                yield next(source)
                raise PermissionError("owned fixture scan refusal")

            try:
                yield fail_later()
            finally:
                closed.append(True)

    monkeypatch.setattr(cache_manager, "scandir", scan)
    monkeypatch.setattr(cache_manager, "_allocated_bytes", lambda _path, **_kwargs: 4096)
    assert cache_manager.main(["inventory", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert closed == [True] and rows[0]["path"] == str(root / "first")
    assert rows[-1] == {"entries": 1, "status": "incomplete"}
    assert "directory inspection failed" in captured.err


def test_native_timeout_kills_and_reaps_owned_measurement_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cache_root(tmp_path, monkeypatch)
    (root / "slow").mkdir()
    pid_file = tmp_path / "measurement.pid"
    real_run = subprocess.run

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["/usr/bin/du", "-sk"]
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float)) and 0 < timeout <= 1.0
        return real_run([
            sys.executable, "-c", "import os,time\nfrom pathlib import Path\n"
            f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n",
        ], check=True, capture_output=True, text=True, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cache_manager, "INVENTORY_MAX_SECONDS", 1.0)
    assert cache_manager.main(["inventory", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert rows[0]["status"] == "error" and rows[-1]["status"] == "incomplete"
    assert "deadline" in captured.err
    with pytest.raises(ChildProcessError):
        os.waitpid(int(pid_file.read_text()), os.WNOHANG)


def test_native_make_inventory_keeps_system_python_and_stdout_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".cache"
    root.mkdir()
    for name in ("first", "second"):
        (root / name).write_bytes(b"owned fixture")
    result = subprocess.run([
        "make", "--no-print-directory", "cache-resource-inventory", f"CACHE_RESOURCE_ROOT={root}",
        "CACHE_RESOURCE_LIMIT=2", "CACHE_RESOURCE_VALIDATE_ONLY=1",
    ], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows[-1] == {"entries": 2, "status": "complete"}
    assert all(row["status"] == "measured" for row in rows[:-1])
    assert "cache inventory start" in result.stderr and "inspected=2 errors=0" in result.stderr
    assert all((root / name).read_bytes() == b"owned fixture" for name in ("first", "second"))
