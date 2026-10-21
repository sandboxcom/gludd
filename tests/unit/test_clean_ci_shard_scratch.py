from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_ci_shard_scratch.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("clean_ci_shard_scratch_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _age_path(path: Path, seconds: int) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


@pytest.fixture
def short_socket_root() -> Iterator[Path]:
    """Provide a namespaced root that stays below macOS AF_UNIX limits."""
    with tempfile.TemporaryDirectory(
        prefix=f"gludd-sock-{os.getpid()}-",
        dir=Path(os.sep) / "tmp",
    ) as directory:
        yield Path(directory)


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


def test_socket_owner_detector_parses_lsof_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-test-runtime.sock"
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/sbin/lsof")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            f"p7331\nn{candidate}\npbad\np{os.getpid()}\n",
            "",
        ),
    )

    assert module._active_socket_pids(candidate) == [7331]

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", ""),
    )
    assert module._active_socket_pids(candidate) == []

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, "", "denied"),
    )
    with pytest.raises(module.ProcessInspectionError):
        module._active_socket_pids(candidate)

    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    with pytest.raises(module.ProcessInspectionError):
        module._active_socket_pids(candidate)


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


def test_socket_inspection_and_revalidation_errors_refuse_cleanup(
    short_socket_root: Path,
) -> None:
    module = _load_module()

    def stale_socket(name: str) -> Path:
        path = short_socket_root / name
        owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        owner.bind(str(path))
        owner.close()
        _age_path(path, 7200)
        return path

    inspection_failure = stale_socket("gludd-test-inspection.sock")

    def fail_socket_inspection(_path: Path) -> list[int]:
        raise module.ProcessInspectionError("lsof unavailable")

    result = module.clean_ci_shard_scratch(
        tmp_root=short_socket_root,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
        active_socket_pids=fail_socket_inspection,
    )
    assert result["skipped"] == [f"{inspection_failure}:socket-inspection-failed"]
    inspection_failure.unlink()

    revalidation_failure = stale_socket("gludd-test-revalidation.sock")
    socket_checks = 0

    def fail_socket_revalidation(_path: Path) -> list[int]:
        nonlocal socket_checks
        socket_checks += 1
        if socket_checks == 2:
            raise module.ProcessInspectionError("lsof raced")
        return []

    result = module.clean_ci_shard_scratch(
        tmp_root=short_socket_root,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
        active_socket_pids=fail_socket_revalidation,
    )
    assert result["skipped"] == [
        f"{revalidation_failure}:socket-revalidation-failed"
    ]


@pytest.mark.parametrize(
    ("race", "reason"),
    [
        ("disappear", "identity-revalidation-failed"),
        ("change", "identity-changed"),
        ("active", "active-pids=7331"),
        ("inspection-error", "process-revalidation-failed"),
    ],
)
def test_generated_file_revalidation_races_fail_closed(
    tmp_path: Path, race: str, reason: str
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-test-raced.json"
    candidate.write_text("generated", encoding="utf-8")
    _age_path(candidate, 7200)
    process_checks = 0

    def process_state(path: Path) -> list[int]:
        nonlocal process_checks
        process_checks += 1
        if process_checks == 1:
            if race == "disappear":
                path.unlink()
            elif race == "change":
                path.write_text("changed after inspection", encoding="utf-8")
            return []
        if race == "active":
            return [7331]
        if race == "inspection-error":
            raise module.ProcessInspectionError("ps raced")
        return []

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=process_state,
    )

    assert result["removed"] == []
    assert result["skipped"] == [f"{candidate}:{reason}"]


def test_unsupported_generated_fifo_is_preserved(tmp_path: Path) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-test-runtime.pipe"
    os.mkfifo(candidate)
    _age_path(candidate, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert candidate.exists()
    assert result == {
        "removed": [],
        "skipped": [f"{candidate}:unsupported-file-type"],
    }


def test_stale_generated_files_are_removed_and_disappeared_candidate_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()
    stale_files = [
        tmp_path / "gludd-test-runtime.json",
        tmp_path / "gludd-test-plugin.js",
        tmp_path / "gludd-test-report.md",
    ]
    for stale_file in stale_files:
        stale_file.write_text("generated", encoding="utf-8")
        _age_path(stale_file, 7200)
    disappeared = tmp_path / "gludd-gate-unit-3-gone"
    monkeypatch.setattr(
        module,
        "iter_candidates",
        lambda _root: [disappeared, *stale_files],
    )

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert all(not path.exists() for path in stale_files)
    assert result == {
        "removed": [str(path) for path in stale_files],
        "skipped": [],
    }
    output = capsys.readouterr().out
    assert "phase=stale-scratch-scan status=starting candidates=4" in output
    assert "phase=stale-scratch-scan status=progress inspected=4 total=4" in output
    assert "phase=stale-scratch-scan status=complete inspected=4" in output


def test_recent_generated_file_is_preserved(tmp_path: Path) -> None:
    module = _load_module()
    recent = tmp_path / "gludd-test-runtime.json"
    recent.write_text("active", encoding="utf-8")

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert recent.exists()
    assert result == {"removed": [], "skipped": [f"{recent}:recent"]}


def test_stale_socket_with_open_owner_is_preserved(short_socket_root: Path) -> None:
    module = _load_module()
    socket_path = short_socket_root / "gludd-test-runtime.sock"
    owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        owner.bind(str(socket_path))
        _age_path(socket_path, 7200)

        result = module.clean_ci_shard_scratch(
            tmp_root=short_socket_root,
            min_age_seconds=3600,
            active_process_pids=lambda _path: [],
            active_socket_pids=lambda _path: [7331],
        )

        assert socket_path.exists()
        assert result == {
            "removed": [],
            "skipped": [f"{socket_path}:active-socket-pids=7331"],
        }
    finally:
        owner.close()


def test_stale_unowned_socket_is_removed(short_socket_root: Path) -> None:
    module = _load_module()
    socket_path = short_socket_root / "gludd-test-runtime.sock"
    owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    owner.bind(str(socket_path))
    owner.close()
    _age_path(socket_path, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=short_socket_root,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
        active_socket_pids=lambda _path: [],
    )

    assert not socket_path.exists()
    assert result == {"removed": [str(socket_path)], "skipped": []}


def test_firecracker_socket_lookalike_regular_file_is_refused(tmp_path: Path) -> None:
    module = _load_module()
    lookalike = tmp_path / "gludd-test-fc-31337-1790838250838.sock"
    lookalike.write_bytes(b"")
    _age_path(lookalike, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
        active_socket_pids=lambda _path: [],
    )

    assert lookalike.is_file()
    assert result == {
        "removed": [],
        "skipped": [f"{lookalike}:unsupported-generated-file"],
    }


def test_lease_markers_and_symlinks_are_never_removed(tmp_path: Path) -> None:
    module = _load_module()
    lease = tmp_path / "gludd-test-runtime.lock"
    lease.write_text("7331", encoding="utf-8")
    unknown_evidence = tmp_path / "gludd-test-private.key"
    unknown_evidence.write_text("unrecoverable", encoding="utf-8")
    target = tmp_path / "outside-evidence.md"
    target.write_text("preserve", encoding="utf-8")
    link = tmp_path / "gludd-test-evidence.md"
    link.symlink_to(target)
    _age_path(lease, 7200)
    _age_path(unknown_evidence, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert lease.exists()
    assert unknown_evidence.exists()
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "preserve"
    assert result == {
        "removed": [],
        "skipped": [
            f"{link}:symlink",
            f"{unknown_evidence}:unsupported-generated-file",
            f"{lease}:lease-marker",
        ],
    }


def test_remove_tree_tolerates_restrictive_mode_repair_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    candidate = tmp_path / "gludd-gate-unit-3-modes"
    candidate.mkdir()
    (candidate / "result.bin").write_bytes(b"result")

    def fail_fchmod(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated chmod race")

    monkeypatch.setattr(module.os, "fchmod", fail_fchmod)

    module._remove_tree(candidate)

    assert not candidate.exists()


@pytest.mark.parametrize(
    "reason",
    [
        "active-pids=7",
        "active-socket-pids=7",
        "socket-inspection-failed",
        "identity-changed",
        "removal-failed",
    ],
)
def test_main_reports_safety_refusal_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason: str,
) -> None:
    module = _load_module()
    active = tmp_path / "gludd-gate-unit-3-live"
    monkeypatch.setattr(
        module,
        "clean_ci_shard_scratch",
        lambda **kwargs: {"removed": [], "skipped": [f"{active}:{reason}"]},
    )

    assert module.main(["--tmp-root", str(tmp_path), "--min-age-seconds", "0"]) == 1
    output = capsys.readouterr().out
    assert f"skipped {active}:{reason}" in output
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


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_stale_scratch_nested_symlink_preserves_external_target_modes(
    tmp_path: Path, link_kind: str
) -> None:
    module = _load_module()
    outside = tmp_path / "retained-evidence"
    outside.mkdir(mode=0o750)
    outside.chmod(0o750)
    evidence = outside / "proof.txt"
    evidence.write_bytes(b"retained release proof")
    evidence.chmod(0o640)
    stale = tmp_path / "gludd-test-nested-symlink"
    stale.mkdir()
    nested = stale / "nested"
    nested.mkdir()
    target = evidence if link_kind == "file" else outside
    (nested / "retained-link").symlink_to(
        target, target_is_directory=link_kind == "directory"
    )
    original_directory_mode = outside.stat().st_mode
    original_evidence_mode = evidence.stat().st_mode
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert result == {"removed": [str(stale)], "skipped": []}
    assert not stale.exists()
    assert evidence.read_bytes() == b"retained release proof"
    assert outside.stat().st_mode == original_directory_mode
    assert evidence.stat().st_mode == original_evidence_mode


def test_stale_scratch_refuses_unreadable_real_directory(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-restrictive-directories"
    stale.mkdir()
    nested = stale / "nested"
    nested.mkdir()
    (nested / "result.txt").write_bytes(b"regenerable result")
    nested.chmod(0o000)
    stale.chmod(0o500)
    _age_path(stale, 7200)
    can_traverse = os.access(nested, os.R_OK | os.X_OK)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    if can_traverse:
        assert result == {"removed": [str(stale)], "skipped": []}
        assert not stale.exists()
    else:
        assert result == {"removed": [], "skipped": [f"{stale}:removal-failed"]}
        assert stale.is_dir()
        nested.chmod(0o700)
        assert (nested / "result.txt").read_bytes() == b"regenerable result"


def test_stale_scratch_removes_readable_readonly_real_directories(
    tmp_path: Path,
) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-readonly-directories"
    stale.mkdir()
    nested = stale / "nested"
    nested.mkdir()
    (nested / "result.txt").write_bytes(b"regenerable result")
    nested.chmod(0o500)
    stale.chmod(0o500)
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert result == {"removed": [str(stale)], "skipped": []}
    assert not stale.exists()


@pytest.mark.parametrize("capability", ["fwalk", "fchmod", "safe-rmtree"])
def test_stale_scratch_refuses_missing_safe_removal_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-required-safe-api"
    stale.mkdir(mode=0o500)
    original_mode = stale.stat().st_mode
    _age_path(stale, 7200)
    if capability == "safe-rmtree":
        monkeypatch.setattr(module.shutil.rmtree, "avoids_symlink_attacks", False)
    else:
        monkeypatch.setattr(module.os, capability, None)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert result == {"removed": [], "skipped": [f"{stale}:removal-failed"]}
    assert stale.stat().st_mode == original_mode


def test_stale_scratch_refuses_unsupported_native_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-unsupported-native-walk"
    stale.mkdir()
    (stale / "proof.txt").write_bytes(b"keep on refusal")
    _age_path(stale, 7200)

    def unavailable_fwalk(
        *_args: object, **_kwargs: object
    ) -> Iterator[tuple[str, list[str], list[str], int]]:
        raise NotImplementedError("native descriptor walk unavailable")

    monkeypatch.setattr(module.os, "fwalk", unavailable_fwalk)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert result == {"removed": [], "skipped": [f"{stale}:removal-failed"]}
    assert (stale / "proof.txt").read_bytes() == b"keep on refusal"


def test_stale_scratch_swap_before_native_open_preserves_external_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-swapped-nested-directory"
    stale.mkdir()
    nested = stale / "nested"
    nested.mkdir()
    outside = tmp_path / "retained-evidence"
    outside.mkdir()
    outside.chmod(0o750)
    evidence = outside / "proof.txt"
    evidence.write_bytes(b"retain original bytes")
    evidence.chmod(0o640)
    directory_mode = outside.stat().st_mode
    evidence_mode = evidence.stat().st_mode
    original_open = os.open
    swapped = False
    _age_path(stale, 7200)

    def swap_then_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if os.fsdecode(path) == "nested" and dir_fd is not None and not swapped:
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", swap_then_open)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert swapped
    assert result in (
        {"removed": [str(stale)], "skipped": []},
        {"removed": [], "skipped": [f"{stale}:removal-failed"]},
    )
    assert outside.stat().st_mode == directory_mode
    assert evidence.stat().st_mode == evidence_mode
    assert evidence.read_bytes() == b"retain original bytes"


def test_stale_scratch_swap_before_descriptor_chmod_preserves_external_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-test-swapped-chmod-ancestor"
    stale.mkdir()
    nested = stale / "nested"
    nested.mkdir()
    nested_inode = nested.stat().st_ino
    outside = tmp_path / "retained-evidence"
    outside.mkdir()
    outside.chmod(0o750)
    evidence = outside / "proof.txt"
    evidence.write_bytes(b"retain original bytes")
    evidence.chmod(0o640)
    directory_mode = outside.stat().st_mode
    evidence_mode = evidence.stat().st_mode
    original_fchmod = os.fchmod
    swapped = False
    _age_path(stale, 7200)

    def swap_then_fchmod(descriptor: int, mode: int) -> None:
        nonlocal swapped
        if os.fstat(descriptor).st_ino == nested_inode and not swapped:
            nested.rename(stale / "detached")
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(module.os, "fchmod", swap_then_fchmod)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        active_process_pids=lambda _path: [],
    )

    assert swapped
    assert result == {"removed": [str(stale)], "skipped": []}
    assert outside.stat().st_mode == directory_mode
    assert evidence.stat().st_mode == evidence_mode
    assert evidence.read_bytes() == b"retain original bytes"
