"""Safety and lifecycle coverage for the automatic disk preflight."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import automatic_disk_cleanup
from scripts.automatic_disk_cleanup import DiskSnapshot
from scripts.makefile_layout import compose_makefile
from scripts.prune_worktrees_safe import WorktreeRecord

ROOT = Path(__file__).resolve().parents[2]


def _fake_git_for_lifecycle(
    *,
    status: str = "",
    head_epoch: int = 50,
    cherry: str = "",
    receipt: str = "",
):
    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert check is False
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, status, "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "branch-head\n", "")
        if "--format=%ct" in args:
            return subprocess.CompletedProcess(args, 0, f"{head_epoch}\n", "")
        if args[0] == "cherry":
            return subprocess.CompletedProcess(args, 0, cherry, "")
        if "reflog" in args:
            return subprocess.CompletedProcess(args, 0, receipt, "")
        raise AssertionError(f"unexpected git args: {args}")

    return run


def _record(path: Path, branch: str, *, locked: bool = False) -> WorktreeRecord:
    path.mkdir(parents=True, exist_ok=True)
    return WorktreeRecord(path=path.resolve(), branch=branch, locked=locked)


def test_cleanup_removes_only_allowlisted_caches_from_inactive_gludd_worktree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    inactive = _record(root / "finished", "feature/finished")
    active = _record(root / "active", "feature/active")
    outside = _record(tmp_path / "other-project", "feature/outside")
    for record in (inactive, active, outside):
        (record.path / ".git").write_text("gitdir: protected\n", encoding="utf-8")
        (record.path / "src").mkdir()
        (record.path / "src" / "keep.py").write_text("keep\n", encoding="utf-8")
        for cache_name in automatic_disk_cleanup.GENERATED_CACHE_DIR_NAMES:
            (record.path / cache_name).mkdir()
            (record.path / cache_name / "generated.bin").write_bytes(b"generated")

    removed: list[Path] = []

    def remove_tree(path: Path) -> None:
        removed.append(path)
        automatic_disk_cleanup._remove_tree(path)

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[inactive, active, outside],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({"feature/active"}),
        refresh_records=lambda: [inactive, active, outside],
        active_process_pids=lambda _path: [],
        remove_tree=remove_tree,
    )

    disposable_names = set(automatic_disk_cleanup.GENERATED_CACHE_DIR_NAMES) - set(
        automatic_disk_cleanup.TOOL_ENVIRONMENT_DIR_NAMES
    )
    assert set(removed) == {inactive.path / name for name in disposable_names}
    assert result.removed == tuple(str(path) for path in sorted(removed))
    assert any("active logical workstream" in item for item in result.skipped)
    assert any("outside approved namespace" in item for item in result.skipped)
    assert any(
        f"{inactive.path / '.venv'}:completion proof required" in item
        for item in result.skipped
    )
    assert (inactive.path / ".venv").exists()
    for record in (inactive, active, outside):
        assert record.path.exists()
        assert (record.path / ".git").exists()
        assert (record.path / "src" / "keep.py").read_text(encoding="utf-8") == "keep\n"
    assert all((active.path / name).exists() for name in automatic_disk_cleanup.GENERATED_CACHE_DIR_NAMES)
    assert all((outside.path / name).exists() for name in automatic_disk_cleanup.GENERATED_CACHE_DIR_NAMES)


@pytest.mark.parametrize("protected_reason", ["locked", "current"])
def test_cleanup_protects_locked_and_current_worktrees(
    tmp_path: Path, protected_reason: str
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / protected_reason, f"feature/{protected_reason}", locked=protected_reason == "locked")
    cache = record.path / ".venv"
    cache.mkdir()

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset({record.path}) if protected_reason == "current" else frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert cache.exists()
    assert not result.removed
    expected = "git worktree lock" if protected_reason == "locked" else "current/main checkout"
    assert any(expected in item for item in result.skipped)


def test_cleanup_reclaims_only_idle_invoking_worktree_disposable_caches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    (current.path / ".git").write_text("gitdir: protected\n", encoding="utf-8")
    (current.path / "src").mkdir()
    (current.path / "src" / "keep.py").write_text("keep\n", encoding="utf-8")
    for cache_name in automatic_disk_cleanup.GENERATED_CACHE_DIR_NAMES:
        (current.path / cache_name).mkdir()
        (current.path / cache_name / "generated.bin").write_bytes(b"generated")
    terraform_root = current.path / "infra" / "terraform"
    terraform_cache = terraform_root / ".plugin-cache"
    terraform_cache.mkdir(parents=True)
    terraform_marker = terraform_cache / ".gitkeep"
    terraform_marker.write_text("", encoding="utf-8")
    (terraform_cache / "provider.bin").write_bytes(b"regenerable")
    terraform_state = terraform_root / "stacks" / "azure-vllm" / "terraform.tfstate"
    terraform_state.parent.mkdir(parents=True)
    terraform_state.write_text("{}\n", encoding="utf-8")

    process_scans: list[Path] = []

    def active_process_pids(path: Path) -> list[int]:
        process_scans.append(path)
        return []

    result = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=active_process_pids,
    )

    expected = {
        str(current.path / cache_name)
        for cache_name in automatic_disk_cleanup.DISPOSABLE_CACHE_DIR_NAMES
    }
    expected.add(str(terraform_cache))
    assert set(result.removed) == expected
    assert result.skipped == ()
    assert result.errors == ()
    assert len(process_scans) >= 2
    assert set(process_scans) == {current.path}
    assert (current.path / ".venv").exists()
    assert terraform_state.read_text(encoding="utf-8") == "{}\n"
    assert terraform_cache.is_dir()
    assert terraform_marker.is_file()
    assert list(terraform_cache.iterdir()) == [terraform_marker]
    assert (current.path / "src" / "keep.py").read_text(encoding="utf-8") == "keep\n"


def test_invoking_cleanup_stops_when_a_process_appears_during_revalidation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    cache = current.path / ".pytest_cache"
    cache.mkdir()
    process_reads = iter(([], [8123]))

    result = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: next(process_reads),
        owned_process_pids=lambda: frozenset(),
    )

    assert result.removed == ()
    assert result.skipped == (f"{cache}:active-pids=8123",)
    assert result.errors == ()
    assert cache.exists()


def test_invoking_cleanup_ignores_only_owned_controller_processes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    cache = current.path / ".pytest_cache"
    cache.mkdir()

    allowed = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: [8123],
        owned_process_pids=lambda: frozenset({8123}),
    )

    assert allowed.removed == (str(cache),)
    assert allowed.skipped == ()
    assert allowed.errors == ()

    cache.mkdir()
    blocked = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: [8123, 9001],
        owned_process_pids=lambda: frozenset({8123}),
    )

    assert blocked.removed == ()
    assert blocked.skipped == (f"{current.path}:active-pids=9001",)
    assert blocked.errors == ()
    assert cache.is_dir()


def test_current_process_ancestry_excludes_children_and_pid_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        ["/bin/ps", "-axo", "pid=,ppid="],
        0,
        "10 9\n9 4\n4 1\n88 10\n",
        "",
    )
    monkeypatch.setattr(automatic_disk_cleanup.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *_args, **_kwargs: completed,
    )

    assert automatic_disk_cleanup._current_process_ancestry_pids() == frozenset(
        {4, 9, 10}
    )


def test_exact_approved_main_worktree_reclaims_only_terraform_provider_cache(
    tmp_path: Path,
) -> None:
    main = _record(tmp_path / "main", "development")
    terraform_root = main.path / "infra" / "terraform"
    terraform_cache = terraform_root / ".plugin-cache"
    terraform_cache.mkdir(parents=True)
    marker = terraform_cache / ".gitkeep"
    marker.write_text("", encoding="utf-8")
    (terraform_cache / "registry.terraform.io").mkdir()
    state = terraform_root / "stacks" / "azure-vllm" / "terraform.tfstate"
    state.parent.mkdir(parents=True)
    state.write_text("{}\n", encoding="utf-8")

    result = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=main.path,
        records=[main],
        approved_roots=(),
        approved_worktrees=(main.path,),
        cache_paths=(automatic_disk_cleanup.TERRAFORM_PLUGIN_CACHE_PATH,),
        refresh_records=lambda: [main],
        active_process_pids=lambda _path: [],
    )

    assert result.removed == (str(terraform_cache),)
    assert tuple(terraform_cache.iterdir()) == (marker,)
    assert state.read_text(encoding="utf-8") == "{}\n"


def test_invoking_cache_selection_and_registration_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    cache = current.path / ".pytest_cache"
    cache.mkdir()

    assert not automatic_disk_cleanup._invoking_cache_is_safe(
        current.path, Path("/absolute-cache")
    )
    assert not automatic_disk_cleanup._invoking_cache_is_safe(
        current.path, Path("missing-cache")
    )
    invalid = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        cache_paths=(Path("source"),),
        active_process_pids=lambda _path: [],
    )
    assert invalid.errors == (f"{current.path}:invalid-cache-selection",)

    states = (
        (WorktreeRecord(current.path, current.branch, True), "git worktree lock"),
        (
            WorktreeRecord(current.path, current.branch, False, prunable=True),
            "prunable registration",
        ),
        (WorktreeRecord(current.path, None, False), "detached worktree"),
    )
    for record, reason in states:
        result = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
            worktree=current.path,
            records=[record],
            approved_roots=(root,),
            active_process_pids=lambda _path: [],
        )
        assert result.skipped == (f"{current.path}:{reason}",)
    assert cache.is_dir()


def test_invoking_cleanup_reports_each_late_safety_failure(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    cache = current.path / ".pytest_cache"
    cache.mkdir()

    def inspect_failure(_path: Path) -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("ps unavailable")

    initial_failure = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        active_process_pids=inspect_failure,
    )
    assert initial_failure.errors == (f"{current.path}:process-inspection-failed",)
    active = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        active_process_pids=lambda _path: [42],
        owned_process_pids=lambda: frozenset(),
    )
    assert active.skipped == (f"{current.path}:active-pids=42",)

    refresh_failure = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: (_ for _ in ()).throw(OSError("git unavailable")),
        active_process_pids=lambda _path: [],
    )
    assert refresh_failure.errors == (f"{cache}:ownership-revalidation-failed",)

    process_reads = iter(([], automatic_disk_cleanup.ProcessInspectionError("late")))

    def late_inspection(_path: Path) -> list[int]:
        value = next(process_reads)
        if isinstance(value, Exception):
            raise value
        return value

    late_failure = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=late_inspection,
    )
    assert late_failure.errors == (f"{cache}:process-revalidation-failed",)

    removal_failure = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: [],
        remove_tree=lambda _path: (_ for _ in ()).throw(OSError("refused")),
    )
    assert removal_failure.errors == (f"{cache}:removal-failed",)
    verification_failure = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: [],
        remove_tree=lambda _path: None,
    )
    assert verification_failure.errors == (f"{cache}:removal-verification-failed",)
    assert cache.is_dir()


def test_terraform_plugin_cache_refuses_marker_and_special_entry(tmp_path: Path) -> None:
    cache = tmp_path / ".plugin-cache"
    cache.mkdir()
    (cache / ".gitkeep").mkdir()
    with pytest.raises(OSError, match="unsafe Terraform plugin-cache marker"):
        automatic_disk_cleanup._clear_terraform_plugin_cache(
            cache, automatic_disk_cleanup._remove_tree
        )

    (cache / ".gitkeep").rmdir()
    (cache / ".gitkeep").write_text("", encoding="utf-8")
    fifo = cache / "provider.pipe"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="unsafe Terraform plugin-cache entry"):
        automatic_disk_cleanup._clear_terraform_plugin_cache(
            cache, automatic_disk_cleanup._remove_tree
        )
    assert fifo.exists()


def test_invoking_cleanup_refuses_changed_registration_and_unsafe_cache_shapes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    current = _record(root / "current", "fix/current")
    changed_cache = current.path / ".pytest_cache"
    changed_cache.mkdir()
    changed = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [],
        active_process_pids=lambda _path: [],
    )
    assert changed.skipped == (f"{changed_cache}:registration changed",)
    assert changed_cache.exists()

    changed_cache.rmdir()
    external = tmp_path / "external"
    external.mkdir()
    symlink_cache = current.path / ".pytest_cache"
    symlink_cache.symlink_to(external, target_is_directory=True)
    file_cache = current.path / ".mypy_cache"
    file_cache.write_text("not a directory\n", encoding="utf-8")
    dry_run_cache = current.path / ".ruff_cache"
    dry_run_cache.mkdir()
    external_terraform = tmp_path / "external-terraform"
    external_terraform_cache = external_terraform / "terraform" / ".plugin-cache"
    external_terraform_cache.mkdir(parents=True)
    (current.path / "infra").symlink_to(external_terraform, target_is_directory=True)
    terraform_cache = current.path / "infra" / "terraform" / ".plugin-cache"
    dry_run = automatic_disk_cleanup.clean_invoking_worktree_disposable_caches(
        worktree=current.path,
        records=[current],
        approved_roots=(root,),
        refresh_records=lambda: [current],
        active_process_pids=lambda _path: [],
        dry_run=True,
    )

    assert dry_run.removed == ()
    assert dry_run.skipped == (f"{dry_run_cache}:would remove invoking cache",)
    assert set(dry_run.errors) == {
        f"{symlink_cache}:unsafe-cache",
        f"{file_cache}:unsafe-cache",
        f"{terraform_cache}:unsafe-cache",
    }
    assert symlink_cache.is_symlink()
    assert file_cache.is_file()
    assert dry_run_cache.is_dir()
    assert (external_terraform_cache).is_dir()


def test_cleanup_revalidates_workstream_and_process_state_before_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    newly_active = _record(root / "newly-active", "feature/newly-active")
    process_active = _record(root / "process-active", "feature/process-active")
    for record in (newly_active, process_active):
        (record.path / ".venv").mkdir()
    registry_reads = iter(
        (
            frozenset(),
            frozenset({"feature/newly-active"}),
            frozenset(),
            frozenset(),
        )
    )

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[newly_active, process_active],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: next(registry_reads),
        refresh_records=lambda: [newly_active, process_active],
        active_process_pids=lambda path: [8123] if path == process_active.path else [],
    )

    assert (newly_active.path / ".venv").exists()
    assert (process_active.path / ".venv").exists()
    assert any("became active" in item for item in result.skipped)
    assert any("active-pids=8123" in item for item in result.skipped)
    assert not result.removed


def test_active_thinking_agent_without_pid_keeps_fresh_lease(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "thinking", "feature/thinking")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=100,
    )
    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(
            automatic_disk_cleanup.IntegrationPoint("integration", 200),
        ),
        now_epoch=101,
        run_git=_fake_git_for_lifecycle(head_epoch=50, cherry=""),
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(
        reclaimable=False,
        reason="fresh active lease",
    )


def test_active_workstream_lease_reader_validates_and_filters_entries(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"

    class Registry:
        def _read(self):
            return {
                "workstreams": {
                    "feature/active": {
                        "branch": "feature/active",
                        "status": "active",
                        "updated_epoch": 42,
                        "worktree": str(worktree),
                    },
                    "feature/done": {
                        "branch": "feature/done",
                        "status": "complete",
                        "updated_epoch": 1,
                        "worktree": str(tmp_path / "done"),
                    },
                }
            }

    assert automatic_disk_cleanup._active_workstream_leases(Registry()) == {
        "feature/active": automatic_disk_cleanup.WorkstreamLease(
            "feature/active", worktree.resolve(), 42
        )
    }


@pytest.mark.parametrize(
    "workstreams",
    [
        None,
        {1: {}},
        {"feature/x": "not-an-entry"},
        {
            "feature/x": {
                "branch": "feature/other",
                "status": "active",
                "updated_epoch": 1,
                "worktree": "/tmp/gludd-worktrees/x",
            }
        },
        {
            "feature/x": {
                "branch": "feature/x",
                "status": "active",
                "updated_epoch": True,
                "worktree": "/tmp/gludd-worktrees/x",
            }
        },
        {
            "feature/x": {
                "branch": "feature/x",
                "status": "active",
                "updated_epoch": 1,
                "worktree": "relative/worktree",
            }
        },
    ],
)
def test_active_workstream_lease_reader_fails_closed_on_invalid_schema(
    workstreams: object,
) -> None:
    class Registry:
        def _read(self):
            return {"workstreams": workstreams}

    with pytest.raises(ValueError, match="active-workstream registry"):
        automatic_disk_cleanup._active_workstream_leases(Registry())


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("status", "worktree status inspection failed"),
        ("head", "worktree HEAD inspection failed"),
        ("head-time", "worktree commit-time inspection failed"),
        ("head-time-return", "worktree commit-time inspection failed"),
        ("cherry", "integration inspection failed"),
    ],
)
def test_completion_proof_inspection_errors_fail_closed(
    tmp_path: Path, mode: str, reason: str
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / mode, f"feature/{mode}")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert check is False
        if "status" in args:
            return subprocess.CompletedProcess(args, 1 if mode == "status" else 0, "", "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 1 if mode == "head" else 0, "head\n", "")
        if "--format=%ct" in args:
            value = "invalid\n" if mode == "head-time" else "2\n"
            return subprocess.CompletedProcess(
                args, 1 if mode == "head-time-return" else 0, value, ""
            )
        if args[0] == "cherry":
            return subprocess.CompletedProcess(args, 1 if mode == "cherry" else 0, "", "")
        raise AssertionError(args)

    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 2),),
        now_epoch=2,
        run_git=run,
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(False, reason, True)


def test_stale_registry_clean_integrated_worktree_is_reclaimable(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "stale", "feature/stale")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=100,
    )

    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(
            automatic_disk_cleanup.IntegrationPoint("integration", 200),
        ),
        now_epoch=100 + automatic_disk_cleanup.WORKSTREAM_LEASE_SECONDS + 1,
        run_git=_fake_git_for_lifecycle(
            head_epoch=50,
            cherry="+ 0123456789abcdef0123456789abcdef01234567\n",
        ),
    )

    assert decision.reclaimable is True
    assert decision.reason == "expired lease; clean committed worktree"


def test_clean_completed_worktree_is_reclaimable_without_manual_unregister(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "completed", "feature/completed")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=100,
    )

    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(
            automatic_disk_cleanup.IntegrationPoint("integration", 200),
        ),
        now_epoch=101,
        run_git=_fake_git_for_lifecycle(
            head_epoch=110,
            cherry="- 0123456789abcdef0123456789abcdef01234567\n",
        ),
    )

    assert decision.reclaimable is True
    assert decision.reason == "completed after workstream registration"


def test_fresh_exact_commit_receipt_prunes_cache_but_preserves_continuation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "continued", "feature/continued")
    tool_environment = record.path / ".venv"
    cache = record.path / ".pytest_cache"
    tool_environment.mkdir()
    cache.mkdir()
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=100
    )
    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 90),),
        now_epoch=111,
        run_git=_fake_git_for_lifecycle(
            head_epoch=110,
            cherry="+ branch-head\n",
            receipt="branch-head\x00110\x00commit: guarded work\n",
        ),
    )
    materialization_calls = 0

    def unexpected_materialization(
        _record: WorktreeRecord, _dry_run: bool
    ) -> automatic_disk_cleanup.LifecycleDecision:
        nonlocal materialization_calls
        materialization_calls += 1
        return automatic_disk_cleanup.LifecycleDecision(True, "removed")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: {record.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: decision,
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_materialization=unexpected_materialization,
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(
        True,
        "exact-head successful commit receipt",
        cache_only=True,
    )
    assert tool_environment.exists()
    assert not cache.exists()
    assert record.path.exists()
    assert materialization_calls == 0
    assert any("completion lease proof unavailable" in item for item in result.skipped)
    assert any("completion proof required" in item for item in result.skipped)


def test_exact_commit_receipt_retires_only_after_minimum_grace(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / "completed", "feature/completed")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1_000
    )
    receipt_epoch = 2_000
    grace = automatic_disk_cleanup.MIN_COMMIT_RECEIPT_GRACE_SECONDS
    assert grace >= 30 * 60

    continuing = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 900),),
        now_epoch=receipt_epoch + grace - 1,
        receipt_grace_seconds=grace,
        run_git=_fake_git_for_lifecycle(
            head_epoch=receipt_epoch,
            cherry="+ branch-head\n",
            receipt=f"branch-head\x00{receipt_epoch}\x00commit: guarded work\n",
        ),
    )
    retired = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 900),),
        now_epoch=receipt_epoch + grace,
        receipt_grace_seconds=grace,
        run_git=_fake_git_for_lifecycle(
            head_epoch=receipt_epoch,
            cherry="+ branch-head\n",
            receipt=f"branch-head\x00{receipt_epoch}\x00commit: guarded work\n",
        ),
    )

    assert continuing == automatic_disk_cleanup.LifecycleDecision(
        True, "exact-head successful commit receipt", cache_only=True
    )
    assert retired == automatic_disk_cleanup.LifecycleDecision(
        True, "exact-head commit receipt beyond retirement grace"
    )
    assert automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(),
        now_epoch=receipt_epoch,
        receipt_grace_seconds=grace - 1,
    ) == automatic_disk_cleanup.LifecycleDecision(
        False, "commit receipt grace below minimum", True
    )


def test_registry_activity_after_receipt_resets_retirement_grace(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / "active", "feature/active")
    grace = automatic_disk_cleanup.MIN_COMMIT_RECEIPT_GRACE_SECONDS
    old_receipt = "branch-head\x00110\x00commit: old work\n"
    renewed_lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=120
    )

    renewed = automatic_disk_cleanup._completion_proof(
        record,
        renewed_lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 90),),
        now_epoch=110 + grace + 1,
        receipt_grace_seconds=grace,
        run_git=_fake_git_for_lifecycle(
            head_epoch=110,
            cherry="+ branch-head\n",
            receipt=old_receipt,
        ),
    )
    new_receipt = automatic_disk_cleanup._completion_proof(
        record,
        renewed_lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 90),),
        now_epoch=121,
        receipt_grace_seconds=grace,
        run_git=_fake_git_for_lifecycle(
            head_epoch=120,
            cherry="+ branch-head\n",
            receipt="branch-head\x00120\x00commit: work after renewal\n",
        ),
    )

    assert renewed == automatic_disk_cleanup.LifecycleDecision(
        False, "unintegrated worktree"
    )
    assert new_receipt == automatic_disk_cleanup.LifecycleDecision(
        True, "exact-head successful commit receipt", cache_only=True
    )


@pytest.mark.parametrize(
    "receipt",
    [
        "other-head\x00110\x00commit: wrong head\n",
        "branch-head\x0099\x00commit: stale\n",
        "branch-head\x00invalid\x00commit: malformed timestamp\n",
        "branch-head\x00110\x00reset: not a commit\n",
    ],
)
def test_stale_or_mismatched_commit_receipt_keeps_fresh_worktree_protected(
    tmp_path: Path, receipt: str
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / "active", "feature/active")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=100
    )

    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(automatic_disk_cleanup.IntegrationPoint("main", 90),),
        now_epoch=111,
        run_git=_fake_git_for_lifecycle(
            head_epoch=110,
            cherry="+ branch-head\n",
            receipt=receipt,
        ),
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(
        False, "unintegrated worktree"
    )


def test_dirty_worktree_is_never_cache_reclaimed(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "dirty", "feature/dirty")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=100,
    )

    decision = automatic_disk_cleanup._completion_proof(
        record,
        lease,
        integration_points=(
            automatic_disk_cleanup.IntegrationPoint("integration", 200),
        ),
        now_epoch=100 + automatic_disk_cleanup.WORKSTREAM_LEASE_SECONDS + 1,
        run_git=_fake_git_for_lifecycle(status=" M src/work.py\x00"),
    )

    assert decision.reclaimable is False
    assert decision.reason == "dirty worktree"


def test_symlink_cache_is_refused_without_touching_external_data(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "finished", "feature/finished")
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("never delete\n", encoding="utf-8")
    (record.path / ".venv").symlink_to(external, target_is_directory=True)

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert sentinel.read_text(encoding="utf-8") == "never delete\n"
    assert not result.removed
    assert result.errors == (f"{record.path / '.venv'}:unsafe-cache",)


def test_registry_or_process_inspection_failure_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "finished", "feature/finished")
    (record.path / ".venv").mkdir()

    def fail_process(_path: Path) -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("ps failed")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=fail_process,
    )

    assert (record.path / ".venv").exists()
    assert result.errors == (f"{record.path}:process-inspection-failed",)


def test_initial_registry_failure_and_non_candidates_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    prunable = _record(root / "prunable", "feature/prunable")
    prunable = WorktreeRecord(
        path=prunable.path, branch=prunable.branch, locked=False, prunable=True
    )
    detached_path = root / "detached"
    detached_path.mkdir(parents=True)
    detached = WorktreeRecord(path=detached_path.resolve(), branch=None, locked=False)
    changed = _record(root / "changed", "feature/changed")

    def fail_registry() -> frozenset[str]:
        raise ValueError("corrupt")

    registry_failure = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[changed],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=fail_registry,
        refresh_records=lambda: [changed],
    )
    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[prunable, detached, changed],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [],
        active_process_pids=lambda _path: [],
    )

    assert registry_failure.errors == (
        "active-workstream-registry:inspection-failed",
    )
    assert any("prunable registration" in item for item in result.skipped)
    assert any("detached worktree" in item for item in result.skipped)
    assert any("registration changed" in item for item in result.skipped)


def test_revalidation_failures_and_late_process_are_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "finished", "feature/finished")

    def fail_refresh() -> list[WorktreeRecord]:
        raise subprocess.CalledProcessError(128, ["git"])

    ownership_error = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=fail_refresh,
        active_process_pids=lambda _path: [],
    )
    process_reads = iter(([], automatic_disk_cleanup.ProcessInspectionError("ps")))

    def fail_late_process(_path: Path) -> list[int]:
        value = next(process_reads)
        if isinstance(value, Exception):
            raise value
        return value

    process_error = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=fail_late_process,
    )
    late_pids = iter(([], [9442]))
    active_late = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: next(late_pids),
    )

    assert ownership_error.errors == (f"{record.path}:ownership-revalidation-failed",)
    assert process_error.errors == (f"{record.path}:process-revalidation-failed",)
    assert active_late.skipped == (f"{record.path}:active-pids=9442",)


def test_lease_refresh_during_revalidation_cancels_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "stale", "feature/stale")
    cache = record.path / ".venv"
    cache.mkdir()
    initial = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=1,
    )
    refreshed = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "",
        worktree=record.path,
        updated_epoch=999,
    )
    lease_reads = iter(
        (
            {record.branch: initial},
            {record.branch: refreshed},
        )
    )

    def lifecycle(
        _record: WorktreeRecord,
        lease: automatic_disk_cleanup.WorkstreamLease,
    ) -> automatic_disk_cleanup.LifecycleDecision:
        return automatic_disk_cleanup.LifecycleDecision(
            reclaimable=lease.updated_epoch == 1,
            reason=("stale complete" if lease.updated_epoch == 1 else "lease refreshed"),
        )

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: next(lease_reads),
        lifecycle_proof=lifecycle,
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert cache.exists()
    assert not result.removed
    assert result.skipped == (f"{record.path}:lease refreshed",)


def test_tool_environment_rechecks_lease_immediately_before_removal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "completed", "feature/completed")
    tool_environment = record.path / ".venv"
    disposable_cache = record.path / ".pytest_cache"
    tool_environment.mkdir()
    disposable_cache.mkdir()
    initial = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )
    renewed = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=2
    )
    lease_reads = iter(
        (
            {record.branch: initial},
            {record.branch: initial},
            {record.branch: renewed},
        )
    )

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: next(lease_reads),
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert not disposable_cache.exists()
    assert tool_environment.exists()
    assert any("tool environment lease changed" in item for item in result.skipped)


def test_tool_environment_rejects_completion_proof_downgrade_race(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "continued", "feature/continued")
    tool_environment = record.path / ".venv"
    disposable_cache = record.path / ".ruff_cache"
    tool_environment.mkdir()
    disposable_cache.mkdir()
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )
    lifecycle_reads = iter(
        (
            automatic_disk_cleanup.LifecycleDecision(True, "completed"),
            automatic_disk_cleanup.LifecycleDecision(True, "completed"),
            automatic_disk_cleanup.LifecycleDecision(
                True, "fresh continuation receipt", cache_only=True
            ),
        )
    )

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: {record.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: next(lifecycle_reads),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert not disposable_cache.exists()
    assert tool_environment.exists()
    assert any(
        "tool environment completion proof downgraded" in item
        for item in result.skipped
    )


def test_tool_environment_rechecks_processes_after_disposable_cache_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "running", "feature/running")
    tool_environment = record.path / ".venv"
    disposable_cache = record.path / ".mypy_cache"
    tool_environment.mkdir()
    disposable_cache.mkdir()
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )
    process_reads = iter(([], [], [5150]))

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: {record.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: next(process_reads),
    )

    assert not disposable_cache.exists()
    assert tool_environment.exists()
    assert any("active-pids=5150" in item for item in result.skipped)


def test_completed_idle_proof_allows_tool_environment_reclamation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "retired", "feature/retired")
    tool_environment = record.path / ".venv"
    tool_environment.mkdir()
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: {record.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
    )

    assert not tool_environment.exists()
    assert str(tool_environment) in result.removed


def test_dry_run_preserves_generated_caches_and_materialization(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "done", "feature/done")
    cache = record.path / ".venv"
    cache.mkdir()
    remove_calls: list[tuple[Path, bool]] = []
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )

    def remove_materialization(
        candidate: WorktreeRecord, dry_run: bool
    ) -> automatic_disk_cleanup.LifecycleDecision:
        remove_calls.append((candidate.path, dry_run))
        return automatic_disk_cleanup.LifecycleDecision(True, "eligible")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset({record.branch or ""}),
        active_workstream_leases=lambda: {record.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_materialization=remove_materialization,
        dry_run=True,
    )

    assert cache.exists()
    assert record.path.exists()
    assert remove_calls == [(record.path, True)]
    assert not result.removed
    assert any("would remove cache" in item for item in result.skipped)
    assert any("would remove materialization" in item for item in result.skipped)


def test_completed_materialization_removal_is_bounded_and_revalidated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-worktrees"
    first = _record(root / "first", "feature/first")
    second = _record(root / "second", "feature/second")
    removed: list[Path] = []
    leases = {
        record.branch or "": automatic_disk_cleanup.WorkstreamLease(
            branch=record.branch or "", worktree=record.path, updated_epoch=1
        )
        for record in (first, second)
    }

    def remove_materialization(
        candidate: WorktreeRecord, dry_run: bool
    ) -> automatic_disk_cleanup.LifecycleDecision:
        assert dry_run is False
        removed.append(candidate.path)
        automatic_disk_cleanup._remove_tree(candidate.path)
        return automatic_disk_cleanup.LifecycleDecision(True, "removed")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[first, second],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(leases),
        active_workstream_leases=lambda: leases,
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [first, second],
        active_process_pids=lambda _path: [],
        remove_materialization=remove_materialization,
        max_materializations=1,
    )

    assert removed == [first.path]
    assert not first.path.exists()
    assert second.path.exists()
    assert str(first.path) in result.removed
    assert any("materialization limit reached" in item for item in result.skipped)


def test_materialization_requires_completion_lease_proof(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "unregistered", "feature/unregistered")
    called = False

    def remove_materialization(
        _candidate: WorktreeRecord, _dry_run: bool
    ) -> automatic_disk_cleanup.LifecycleDecision:
        nonlocal called
        called = True
        return automatic_disk_cleanup.LifecycleDecision(True, "removed")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_materialization=remove_materialization,
    )

    assert called is False
    assert record.path.exists()
    assert result.skipped == (f"{record.path}:completion lease proof unavailable",)


def test_registry_disappearance_race_preserves_materialization(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "completed", "feature/completed")
    lease = automatic_disk_cleanup.WorkstreamLease(
        branch=record.branch or "", worktree=record.path, updated_epoch=1
    )
    branch_reads = iter(
        (
            frozenset({record.branch or ""}),
            frozenset({record.branch or ""}),
            frozenset(),
        )
    )
    lease_reads = iter(
        (
            {record.branch or "": lease},
            {record.branch or "": lease},
            {},
        )
    )
    called = False

    def remove_materialization(
        _candidate: WorktreeRecord, _dry_run: bool
    ) -> automatic_disk_cleanup.LifecycleDecision:
        nonlocal called
        called = True
        return automatic_disk_cleanup.LifecycleDecision(True, "removed")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: next(branch_reads),
        active_workstream_leases=lambda: next(lease_reads),
        lifecycle_proof=lambda _record, _lease: (
            automatic_disk_cleanup.LifecycleDecision(True, "completed")
        ),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_materialization=remove_materialization,
    )

    assert called is False
    assert record.path.exists()
    assert result.skipped == (f"{record.path}:completion lease changed",)


def test_git_materialization_removal_preserves_exact_branch_and_commit(
    tmp_path: Path,
) -> None:
    record = _record(
        tmp_path / "gludd-worktrees" / "completed", "feature/completed"
    )
    (record.path / "src.py").write_text("committed\n", encoding="utf-8")
    head = "0123456789abcdef0123456789abcdef01234567"
    calls: list[tuple[str, ...]] = []

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(args)
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, f"{head}\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(args, 0, f"{head}\n", "")
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("worktree", "remove"):
            automatic_disk_cleanup._remove_tree(record.path)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    archive_root = tmp_path / "release-evidence"
    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record,
        dry_run=False,
        run_git=run,
        evidence_archive_root=archive_root,
    )

    assert decision.reclaimable is True
    assert not record.path.exists()
    assert calls.count(
        ("rev-parse", "--verify", f"refs/heads/{record.branch}^{{commit}}")
    ) == 3
    assert calls.count(("cat-file", "-e", f"{head}^{{commit}}")) == 2
    assert ("worktree", "remove", "--", str(record.path)) in calls
    manifest = next(archive_root.glob("*/manifest.json"))
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    assert metadata == {
        "branch": record.branch,
        "head": head,
        "original_worktree": str(record.path),
        "preserved": [],
        "rehydrate": {
            "branch": record.branch,
            "path": str(record.path),
        },
    }


@pytest.mark.parametrize(
    ("status", "branch_head", "reason"),
    [
        ("?? untracked.txt\x00", "head", "dirty worktree"),
        ("", "other", "branch ref does not preserve worktree HEAD"),
    ],
)
def test_materialization_removal_refuses_dirty_or_unpreserved_source(
    tmp_path: Path, status: str, branch_head: str, reason: str
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / reason, "feature/refuse")

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert check is False
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, status, "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(args, 0, f"{branch_head}\n", "")
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record, dry_run=False, run_git=run
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(False, reason)
    assert record.path.exists()


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("detached", "detached worktree"),
        ("status", "worktree status inspection failed"),
        ("ignored", "ignored data inspection failed"),
        ("malformed", "ignored data inspection failed"),
        ("traversal", "ignored data inspection failed"),
    ],
)
def test_materialization_inspection_ambiguity_fails_closed(
    tmp_path: Path, mode: str, reason: str
) -> None:
    path = tmp_path / "gludd-worktrees" / mode
    path.mkdir(parents=True)
    record = WorktreeRecord(
        path=path.resolve(),
        branch=None if mode == "detached" else f"feature/{mode}",
        locked=False,
    )

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert check is False
        if "status" in args and "--ignored=matching" not in args:
            return subprocess.CompletedProcess(args, 1 if mode == "status" else 0, "", "")
        if "--ignored=matching" in args:
            output = ""
            if mode == "malformed":
                output = "?? unexpected\x00"
            elif mode == "traversal":
                output = "!! ../outside\x00"
            return subprocess.CompletedProcess(
                args, 1 if mode == "ignored" else 0, output, ""
            )
        raise AssertionError(args)

    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record,
        dry_run=False,
        run_git=run,
        evidence_archive_root=tmp_path / "release-evidence",
    )

    assert decision.reason == reason
    assert decision.reclaimable is False
    assert record.path.exists()


def test_materialization_removal_archives_ignored_release_evidence(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / "evidence", "feature/evidence")
    gate_logs = record.path / ".gate-logs"
    gate_logs.mkdir()
    (gate_logs / "proof.json").write_text('{"gate":"pass"}\n', encoding="utf-8")
    archive_root = tmp_path / "release-evidence"
    removed = False

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        nonlocal removed
        assert check is False
        if "status" in args:
            ignored = (
                "!! .gate-logs/\x00!! .gate-logs/proof.json\x00"
                if "--ignored=matching" in args
                else ""
            )
            return subprocess.CompletedProcess(args, 0, ignored, "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("worktree", "remove"):
            removed = True
            automatic_disk_cleanup._remove_tree(record.path)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record,
        dry_run=False,
        run_git=run,
        evidence_archive_root=archive_root,
    )

    assert decision.reclaimable is True
    assert removed is True
    archive = next(archive_root.iterdir())
    assert (archive / ".gate-logs" / "proof.json").read_text(encoding="utf-8") == (
        '{"gate":"pass"}\n'
    )
    assert (archive / "manifest.json").is_file()


def test_materialization_removal_refuses_ignored_non_evidence(tmp_path: Path) -> None:
    record = _record(tmp_path / "gludd-worktrees" / "private", "feature/private")
    removed = False

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        nonlocal removed
        assert check is False
        if "status" in args:
            ignored = "!! sandboxcom_github_rsa\x00" if "--ignored=matching" in args else ""
            return subprocess.CompletedProcess(args, 0, ignored, "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("worktree", "remove"):
            removed = True
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record,
        dry_run=False,
        run_git=run,
        evidence_archive_root=tmp_path / "release-evidence",
    )

    assert decision == automatic_disk_cleanup.LifecycleDecision(
        False, "ignored worktree data is not regenerable (sandboxcom_github_rsa)"
    )
    assert removed is False
    assert record.path.exists()


@pytest.mark.parametrize("mode", ["ref-race", "remove-refused"])
def test_evidence_archive_rolls_back_when_materialization_changes_or_refuses(
    tmp_path: Path, mode: str
) -> None:
    record = _record(tmp_path / "gludd-worktrees" / mode, f"feature/{mode}")
    gate_logs = record.path / ".gate-logs"
    gate_logs.mkdir()
    proof = gate_logs / "proof.json"
    proof.write_text('{"preserved":true}\n', encoding="utf-8")
    archive_root = tmp_path / "release-evidence"
    ref_reads = 0
    remove_calls = 0

    def run(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        nonlocal ref_reads, remove_calls
        assert check is False
        if "status" in args:
            ignored = "!! .gate-logs/proof.json\x00" if "--ignored=matching" in args else ""
            return subprocess.CompletedProcess(args, 0, ignored, "")
        if args[-2:] == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            ref_reads += 1
            value = "other\n" if mode == "ref-race" and ref_reads == 2 else "head\n"
            return subprocess.CompletedProcess(args, 0, value, "")
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("worktree", "remove"):
            remove_calls += 1
            return subprocess.CompletedProcess(args, 1, "", "refused")
        raise AssertionError(args)

    decision = automatic_disk_cleanup._remove_worktree_materialization(
        record,
        dry_run=False,
        run_git=run,
        evidence_archive_root=archive_root,
    )

    assert decision.error is True
    assert proof.read_text(encoding="utf-8") == '{"preserved":true}\n'
    assert remove_calls == (0 if mode == "ref-race" else 1)
    assert not archive_root.exists() or not tuple(archive_root.iterdir())


def test_preserved_evidence_relocates_out_of_scratch_without_deletion(
    tmp_path: Path,
) -> None:
    approved_root = tmp_path / "gludd-worktrees"
    worktree = approved_root / "completed"
    branch = "feature/completed"
    head = "0123456789abcdef0123456789abcdef01234567"
    legacy_root = approved_root / ".gludd-release-evidence"
    archive = legacy_root / f"archive-{head}"
    proof = archive / ".gate-logs" / "proof.json"
    proof.parent.mkdir(parents=True)
    proof.write_text('{"gate":"pass"}\n', encoding="utf-8")
    (archive / "manifest.json").write_text(
        (
            '{"branch":"feature/completed",'
            f'"head":"{head}",'
            f'"original_worktree":"{worktree}",'
            '"preserved":[".gate-logs/proof.json"]}\n'
        ),
        encoding="utf-8",
    )
    canonical_root = tmp_path / "git-common" / "gludd-release-evidence"
    lease = automatic_disk_cleanup.WorkstreamLease(branch, worktree, 1)

    result = automatic_disk_cleanup.relocate_preserved_evidence(
        leases={branch: lease},
        approved_roots=(approved_root,),
        canonical_root=canonical_root,
    )

    relocated = canonical_root / archive.name
    assert result.errors == ()
    assert result.removed == (str(archive),)
    assert not archive.exists()
    assert (relocated / ".gate-logs" / "proof.json").read_text(
        encoding="utf-8"
    ) == '{"gate":"pass"}\n'
    assert (relocated / "manifest.json").is_file()


def test_preserved_evidence_relocation_refuses_ambiguous_manifest(
    tmp_path: Path,
) -> None:
    approved_root = tmp_path / "gludd-worktrees"
    worktree = approved_root / "completed"
    branch = "feature/completed"
    archive = approved_root / ".gludd-release-evidence" / "ambiguous"
    archive.mkdir(parents=True)
    (archive / "manifest.json").write_text("{}\n", encoding="utf-8")

    result = automatic_disk_cleanup.relocate_preserved_evidence(
        leases={
            branch: automatic_disk_cleanup.WorkstreamLease(branch, worktree, 1)
        },
        approved_roots=(approved_root,),
        canonical_root=tmp_path / "git-common" / "gludd-release-evidence",
    )

    assert result.errors == (f"{archive}:evidence-manifest-invalid",)
    assert archive.exists()


def _owned_terraform_workspace(root: Path, name: str = "a" * 24) -> Path:
    workspace = root / name
    providers = workspace / ".terraform" / "providers"
    providers.mkdir(parents=True)
    (providers / "provider.bin").write_bytes(b"regenerable")
    marker = workspace / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text(
        json.dumps(
            {
                "operation_digest": name + ("b" * (64 - len(name))),
                "protocol": "gludd-azure-containerapp-live-proof-v1",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o600)
    (workspace / "terraform.tfstate").write_text("{}\n", encoding="utf-8")
    return workspace


def test_owned_terraform_provider_cleanup_preserves_state_and_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    workspace = _owned_terraform_workspace(root)
    providers = workspace / ".terraform" / "providers"
    marker = workspace / ".gludd-azure-containerapp-live-proof.json"
    process_checks: list[Path] = []

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,),
        active_process_pids=lambda path: process_checks.append(path) or [],
    )

    assert result == automatic_disk_cleanup.CleanupResult(
        removed=(str(providers),), skipped=(), errors=()
    )
    assert process_checks == [workspace.resolve(), workspace.resolve()]
    assert not providers.exists()
    assert marker.is_file()
    assert (workspace / "terraform.tfstate").read_text(encoding="utf-8") == "{}\n"


def test_owned_terraform_provider_cleanup_preserves_active_and_dry_run_workspaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    workspace = _owned_terraform_workspace(root)
    providers = workspace / ".terraform" / "providers"

    active = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: [8123]
    )
    dry_run = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: [], dry_run=True
    )

    assert active.skipped == (f"{workspace}:active-pids=8123",)
    assert dry_run.skipped == (f"{providers}:would remove terraform provider cache",)
    assert providers.is_dir()


def test_owned_terraform_provider_cleanup_rejects_invalid_marker_and_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    invalid = _owned_terraform_workspace(root, "c" * 24)
    marker = invalid / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text('{"protocol":"wrong"}\n', encoding="utf-8")
    marker.chmod(0o600)
    linked = _owned_terraform_workspace(root, "d" * 24)
    providers = linked / ".terraform" / "providers"
    automatic_disk_cleanup._remove_tree(providers)
    external = tmp_path / "external-providers"
    external.mkdir()
    (external / "keep.bin").write_bytes(b"keep")
    providers.symlink_to(external, target_is_directory=True)

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: []
    )

    assert result.errors == tuple(
        sorted(
            (
                f"{invalid}:terraform-ownership-invalid",
                f"{providers}:unsafe-terraform-provider-cache",
            )
        )
    )
    assert (invalid / ".terraform" / "providers").is_dir()
    assert (external / "keep.bin").read_bytes() == b"keep"


def test_owned_terraform_provider_cleanup_treats_absent_root_as_converged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: []
    )

    assert result == automatic_disk_cleanup.CleanupResult(
        removed=(), skipped=(f"{root}:terraform-root-absent",), errors=()
    )


def test_owned_terraform_provider_cleanup_bounds_and_late_failures(
    tmp_path: Path,
) -> None:
    invalid_limit = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(), max_workspaces=0
    )
    assert invalid_limit.errors == ("terraform-provider-cache:invalid-limit",)

    absent_root = tmp_path / "absent-provider"
    absent_workspace = _owned_terraform_workspace(absent_root, "e" * 24)
    absent_providers = absent_workspace / ".terraform" / "providers"
    automatic_disk_cleanup._remove_tree(absent_providers)
    absent = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(absent_root,), active_process_pids=lambda _path: []
    )
    assert absent.skipped == (
        f"{absent_providers}:terraform-provider-cache-absent",
    )

    raced_root = tmp_path / "late-process"
    raced_workspace = _owned_terraform_workspace(raced_root, "f" * 24)
    process_reads = iter(([], [7331]))
    raced = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(raced_root,), active_process_pids=lambda _path: next(process_reads)
    )
    assert raced.skipped == (f"{raced_workspace}:active-pids=7331",)
    assert (raced_workspace / ".terraform" / "providers").is_dir()

    failed_root = tmp_path / "failed-removal"
    failed_workspace = _owned_terraform_workspace(failed_root, "1" * 24)
    failed_providers = failed_workspace / ".terraform" / "providers"

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("refused")

    failed = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(failed_root,),
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )
    assert failed.errors == (f"{failed_providers}:removal-failed",)
    assert failed_providers.is_dir()


def test_shared_uv_cache_prunes_only_after_two_idle_checks(tmp_path: Path) -> None:
    cache = tmp_path / "gludd-uv-cache-public-v2"
    cache.mkdir()
    checks = iter(([], [], []))
    pruned: list[Path] = []
    cleaned: list[Path] = []

    result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(checks),
        run_prune=lambda path: pruned.append(path) is None,
        run_clean=lambda path: cleaned.append(path) is None,
        dry_run=False,
    )

    assert pruned == [cache]
    assert cleaned == [cache]
    assert result.removed == (str(cache),)


def test_shared_uv_cache_preserves_active_or_ambiguous_owners(tmp_path: Path) -> None:
    cache = tmp_path / "gludd-uv-cache-public-v2"
    cache.mkdir()
    called = False

    def unexpected_prune(_path: Path) -> bool:
        nonlocal called
        called = True
        return True

    active = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [321],
        run_prune=unexpected_prune,
        dry_run=False,
    )
    reads = iter(([], [654]))
    raced = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(reads),
        run_prune=unexpected_prune,
        dry_run=False,
    )

    assert called is False
    assert active.skipped == (f"{cache}:active-uv-pids=321",)
    assert raced.skipped == (f"{cache}:active-uv-pids=654",)

    final_reads = iter(([], [], [987]))
    pruned: list[Path] = []
    cleaned: list[Path] = []
    final_race = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(final_reads),
        run_prune=lambda path: pruned.append(path) is None,
        run_clean=lambda path: cleaned.append(path) is None,
    )
    assert pruned == [cache]
    assert cleaned == []
    assert final_race.skipped == (f"{cache}:active-uv-pids=987",)


def test_unsafe_cache_file_and_removal_failure_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "finished", "feature/finished")
    unsafe = record.path / ".venv"
    unsafe.write_text("not a directory\n", encoding="utf-8")
    failed_removal = record.path / ".pytest_cache"
    failed_removal.mkdir()

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("denied")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )

    assert result.errors == tuple(
        sorted(
            (
                f"{failed_removal}:removal-failed",
                f"{unsafe}:unsafe-cache",
            )
        )
    )


def test_stale_generated_scratch_cleanup_maps_refusals_and_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    def cleanup_runner(*, dry_run: bool) -> dict[str, list[str]]:
        calls.append(dry_run)
        return {
            "removed": ["/tmp/gludd-test-old.json"],
            "skipped": [
                "/tmp/gludd-test-current.json:recent",
                "/tmp/gludd-test-live.sock:active-socket-pids=7331",
                "/tmp/gludd-test-state.lock:lease-marker",
                "/tmp/gludd-test-unknown.sock:socket-inspection-failed",
                "/tmp/gludd-test-raced.json:identity-changed",
            ],
        }

    result = automatic_disk_cleanup.clean_stale_generated_scratch(
        dry_run=True,
        cleanup_runner=cleanup_runner,
    )

    assert calls == [True]
    assert result.removed == ("/tmp/gludd-test-old.json",)
    assert result.skipped == (
        "/tmp/gludd-test-current.json:recent",
        "/tmp/gludd-test-live.sock:active-socket-pids=7331",
        "/tmp/gludd-test-state.lock:lease-marker",
    )
    assert result.errors == (
        "/tmp/gludd-test-unknown.sock:socket-inspection-failed",
        "/tmp/gludd-test-raced.json:identity-changed",
    )
    output = capsys.readouterr().out
    assert "action=stale-generated-scratch status=starting" in output
    assert "action=stale-generated-scratch status=complete" in output


def test_stale_owned_node_cache_cleanup_is_exact_and_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"
    cache.mkdir()
    payload = cache / "_cacache" / "content.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"regenerable")
    ambiguous = approved / "gludd-npm-cache-public-v1-copy"
    ambiguous.mkdir()
    for path in (payload, payload.parent, cache, ambiguous):
        os.utime(path, (100.0, 100.0))

    result = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache, ambiguous),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )

    assert result.removed == (str(cache),)
    assert result.skipped == (f"{ambiguous}:unapproved-cache-name",)
    assert result.errors == ()
    assert not cache.exists()
    assert ambiguous.is_dir()
    output = capsys.readouterr().out
    assert "action=node-download-cache status=starting candidates=2" in output
    assert "action=node-download-cache status=complete inspected=2 removed=1" in output


def test_node_cache_cleanup_refuses_unsafe_fresh_and_unbounded_candidates(
    tmp_path: Path,
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"

    cache.write_text("not a directory\n", encoding="utf-8")
    regular = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert regular.skipped == (f"{cache}:unsupported-file-type",)
    cache.unlink()

    external = tmp_path / "external-cache"
    external.mkdir()
    cache.symlink_to(external, target_is_directory=True)
    linked = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert linked.skipped == (f"{cache}:symlink",)
    cache.unlink()

    cache.mkdir()
    fresh = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert fresh.skipped == (f"{cache}:recent",)
    os.utime(cache, (100.0, 100.0))
    (cache / "one").write_bytes(b"1")
    (cache / "two").write_bytes(b"2")
    for child in cache.iterdir():
        os.utime(child, (100.0, 100.0))
    os.utime(cache, (100.0, 100.0))
    bounded = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        max_entries=1,
        active_process_pids=lambda _path: [],
    )
    assert bounded.skipped == (f"{cache}:entry-limit",)
    assert cache.is_dir()


def test_node_cache_cleanup_refuses_active_and_raced_owners(tmp_path: Path) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"
    cache.mkdir()
    os.utime(cache, (100.0, 100.0))

    active = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [7331],
    )
    assert active.skipped == (f"{cache}:active-pids=7331",)

    calls = 0

    def race_after_initial_inspection(path: Path) -> list[int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            (path / "raced").write_text("new cache entry\n", encoding="utf-8")
        return []

    raced = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=race_after_initial_inspection,
    )
    assert raced.skipped == (f"{cache}:identity-changed",)
    assert cache.is_dir()


def test_node_cache_cleanup_additional_fail_closed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"

    invalid = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(),
        approved_tmp_root=approved,
        min_age_seconds=-1,
        active_process_pids=lambda _path: [],
    )
    assert invalid.errors == ("node-download-cache:invalid-bound",)

    missing_root = tmp_path / "missing-root"
    unavailable = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(),
        approved_tmp_root=missing_root,
        active_process_pids=lambda _path: [],
    )
    assert unavailable.errors == (f"{missing_root}:temp-root-inspection-failed",)

    absent = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        active_process_pids=lambda _path: [],
    )
    assert absent.skipped == (f"{cache}:cache-absent",)

    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside = outside_root / cache.name
    outside.mkdir()
    outside_result = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(outside,),
        approved_tmp_root=approved,
        active_process_pids=lambda _path: [],
    )
    assert outside_result.skipped == (f"{outside}:outside-canonical-temp-root",)

    cache.mkdir()
    linked_target = tmp_path / "linked-target"
    linked_target.write_text("preserve\n", encoding="utf-8")
    (cache / "link").symlink_to(linked_target)
    os.utime(cache, (100.0, 100.0))
    unsafe = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert unsafe.skipped == (f"{cache}:unsafe-tree-entry",)
    assert linked_target.read_text(encoding="utf-8") == "preserve\n"
    (cache / "link").unlink()

    def stale_cache() -> None:
        for child in cache.iterdir():
            if child.is_dir():
                automatic_disk_cleanup._remove_tree(child)
            else:
                child.unlink()
        os.utime(cache, (100.0, 100.0))

    stale_cache()

    def fail_process(_path: Path) -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("unavailable")

    initial_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=fail_process,
    )
    assert initial_failure.errors == (f"{cache}:process-inspection-failed",)

    process_reads: list[list[int] | Exception] = [
        [],
        automatic_disk_cleanup.ProcessInspectionError("late"),
    ]

    def fail_process_recheck(_path: Path) -> list[int]:
        value = process_reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    recheck_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=fail_process_recheck,
    )
    assert recheck_failure.errors == (f"{cache}:process-revalidation-failed",)

    active_reads = iter(([], [8123]))
    late_active = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: next(active_reads),
    )
    assert late_active.skipped == (f"{cache}:active-pids=8123",)

    dry_run = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        dry_run=True,
    )
    assert dry_run.skipped == (f"{cache}:would remove node download cache",)

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("refused")

    removal_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )
    assert removal_failure.errors == (f"{cache}:removal-failed",)

    verification_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        remove_tree=lambda _path: None,
    )
    assert verification_failure.errors == (f"{cache}:removal-verification-failed",)


def test_default_node_package_manager_census_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_output = (
        "bad-line\n"
        "not-a-pid npm ci\n"
        "123 /usr/local/bin/npm ci\n"
        "456 /usr/bin/node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci\n"
        "789 /usr/bin/node application.js\n"
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, process_output, ""
        ),
    )
    monkeypatch.setattr(automatic_disk_cleanup.os, "getpid", lambda: 999)

    assert automatic_disk_cleanup._active_node_package_manager_pids(
        Path("/tmp/gludd-npm-cache-public-v1")
    ) == [123, 456]

    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    with pytest.raises(automatic_disk_cleanup.ProcessInspectionError):
        automatic_disk_cleanup._active_node_package_manager_pids(
            Path("/tmp/gludd-npm-cache-public-v1")
        )


def test_preflight_is_noop_when_both_thresholds_are_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    cleanup_calls = 0

    def unexpected_cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise AssertionError("healthy preflight must not clean")

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: DiskSnapshot(scratch_mb=10.0, disk_pct=40.0),
        cleanup=unexpected_cleanup,
    )

    assert result == 0
    assert cleanup_calls == 0
    assert "phase=inspect status=healthy" in capsys.readouterr().out


def test_preflight_cleans_then_rechecks_both_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=120.0, disk_pct=91.0),
            DiskSnapshot(scratch_mb=20.0, disk_pct=70.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=("/tmp/gludd-worktrees/done/.venv",), skipped=(), errors=()
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
    )

    assert result == 0
    assert cleanup_calls == 1
    output = capsys.readouterr().out
    assert "phase=cleanup status=starting" in output
    assert "phase=recheck status=healthy" in output


def test_preflight_repeats_cleanup_while_usage_improves_until_healthy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=995.5, disk_pct=92.0),
            DiskSnapshot(scratch_mb=114.5, disk_pct=92.0),
            DiskSnapshot(scratch_mb=42.0, disk_pct=89.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=(f"/tmp/gludd-worktrees/done-{cleanup_calls}/.venv",),
            skipped=(),
            errors=(),
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
    )

    assert result == 0
    assert cleanup_calls == 2
    output = capsys.readouterr().out
    assert "phase=cleanup status=starting pass=1" in output
    assert "phase=recheck status=pressure pass=1" in output
    assert "phase=cleanup status=starting pass=2" in output
    assert "phase=recheck status=healthy pass=2" in output


def test_preflight_stops_fail_closed_when_cleanup_makes_no_measurable_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=114.5, disk_pct=92.0)
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=("/tmp/gludd-worktrees/done/.pytest_cache",),
            skipped=(),
            errors=(),
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: high,
        cleanup=cleanup,
    )

    assert result == 1
    assert cleanup_calls == 1
    captured = capsys.readouterr()
    assert "phase=recheck status=failed pass=1 reason=no-progress" in captured.err


def test_preflight_bounds_repetitive_cleanup_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=114.5, disk_pct=92.0)
    detail_count = automatic_disk_cleanup.MAX_CLEANUP_DETAILS_PER_KIND + 5

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: high,
        cleanup=lambda: automatic_disk_cleanup.CleanupResult(
            removed=(),
            skipped=tuple(f"/tmp/gludd-test-{index}:recent" for index in range(detail_count)),
            errors=(),
        ),
    )

    assert result == 1
    output = capsys.readouterr().out
    assert output.count("action=skip pass=1 detail=") == (
        automatic_disk_cleanup.MAX_CLEANUP_DETAILS_PER_KIND
    )
    assert "action=skip-summary pass=1 shown=20 omitted=5" in output


def test_preflight_stops_at_bounded_cleanup_pass_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=140.0, disk_pct=94.0),
            DiskSnapshot(scratch_mb=130.0, disk_pct=93.0),
            DiskSnapshot(scratch_mb=120.0, disk_pct=92.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult((), (), ())

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
        max_cleanup_passes=2,
    )

    assert result == 1
    assert cleanup_calls == 2
    assert "phase=recheck status=failed pass=2 reason=pass-limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    "after",
    [
        DiskSnapshot(scratch_mb=101.0, disk_pct=20.0),
        DiskSnapshot(scratch_mb=20.0, disk_pct=91.0),
    ],
)
def test_preflight_fails_closed_when_either_threshold_remains_high(
    after: DiskSnapshot, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshots = iter((after, after))

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=lambda: automatic_disk_cleanup.CleanupResult((), (), ()),
    )

    assert result == 1
    assert "phase=recheck status=failed" in capsys.readouterr().err


def test_preflight_fails_closed_on_cleanup_or_inspection_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=120.0, disk_pct=20.0)
    assert (
        automatic_disk_cleanup.run_preflight(
            inspect_usage=lambda: high,
            cleanup=lambda: automatic_disk_cleanup.CleanupResult(
                (), (), ("registry unavailable",)
            ),
        )
        == 1
    )

    def fail_inspection() -> DiskSnapshot:
        raise automatic_disk_cleanup.DiskInspectionError("df unavailable")

    assert automatic_disk_cleanup.run_preflight(inspect_usage=fail_inspection) == 1
    assert "status=failed" in capsys.readouterr().err


def test_preflight_fails_closed_when_cleanup_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_cleanup() -> automatic_disk_cleanup.CleanupResult:
        raise OSError("cleanup unavailable")

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: DiskSnapshot(scratch_mb=120.0, disk_pct=91.0),
        cleanup=fail_cleanup,
    )

    assert result == 1
    assert "phase=cleanup status=failed pass=1" in capsys.readouterr().err


def test_canonical_inspection_and_worktree_discovery_are_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        automatic_disk_cleanup.check_disk_usage,
        "_gludd_tmp_inspection",
        lambda: (12.5, []),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.check_disk_usage, "_disk_usage_pct", lambda: 33.0
    )
    main = tmp_path / "main"
    main.mkdir()
    porcelain = f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n"
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, porcelain, ""),
    )

    assert automatic_disk_cleanup.inspect_usage() == DiskSnapshot(12.5, 33.0)
    assert automatic_disk_cleanup._registered_worktrees() == [
        WorktreeRecord(main.resolve(), "development", False)
    ]

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError, match="returned no worktrees"
    ):
        automatic_disk_cleanup._registered_worktrees()


def test_git_common_evidence_root_and_integration_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common = tmp_path / ".git"
    common.mkdir()
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, 0, f"{common}\n", ""
        ),
    )
    assert automatic_disk_cleanup._canonical_evidence_archive_root() == (
        common / "gludd-release-evidence"
    )
    with pytest.raises(automatic_disk_cleanup.DiskInspectionError):
        automatic_disk_cleanup._integration_points([])

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 1, "", "bad"),
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="common directory is unavailable",
    ):
        automatic_disk_cleanup._canonical_evidence_archive_root()


def test_git_common_evidence_root_rejects_non_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_a_directory = tmp_path / "git-common-file"
    not_a_directory.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, 0, f"{not_a_directory}\n", ""
        ),
    )

    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="common directory is invalid",
    ):
        automatic_disk_cleanup._canonical_evidence_archive_root()


def test_integration_points_skip_unavailable_empty_and_invalid_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = _record(tmp_path / "main", "development")

    def one_valid(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("-C", str(main.path)):
            return subprocess.CompletedProcess(args, 0, "main-head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            if "development" in args[-1]:
                return subprocess.CompletedProcess(args, 1, "", "missing")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ("show", "-s", "--format=%ct"):
            return subprocess.CompletedProcess(args, 0, "42\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe, "_git", one_valid
    )
    assert automatic_disk_cleanup._integration_points([main]) == (
        automatic_disk_cleanup.IntegrationPoint("main-head", 42),
    )

    def no_valid(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("-C", str(main.path)):
            return subprocess.CompletedProcess(args, 0, "bad-time\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            revision = "negative-time" if "development" in args[-1] else "failed-time"
            return subprocess.CompletedProcess(args, 0, f"{revision}\n", "")
        if args[-1] == "bad-time":
            return subprocess.CompletedProcess(args, 0, "invalid\n", "")
        if args[-1] == "negative-time":
            return subprocess.CompletedProcess(args, 0, "-1\n", "")
        if args[-1] == "failed-time":
            return subprocess.CompletedProcess(args, 1, "7\n", "failed")
        raise AssertionError(args)

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe, "_git", no_valid
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="no trusted integration commit",
    ):
        automatic_disk_cleanup._integration_points([main])


def test_default_uv_process_and_prune_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process_output = "bad-line\nnot-a-pid /usr/bin/uv\n123 /usr/bin/uv run\n456 python test.py\n"
    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, process_output, ""
        ),
    )
    monkeypatch.setattr(automatic_disk_cleanup.os, "getpid", lambda: 999)
    assert automatic_disk_cleanup._active_uv_process_pids() == [123]

    monkeypatch.setattr(automatic_disk_cleanup.shutil, "which", lambda _name: None)
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is False

    observed: dict[str, object] = {}

    def run(*args, **kwargs):
        observed["args"] = args[0]
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(automatic_disk_cleanup.shutil, "which", lambda _name: "/bin/uv")
    monkeypatch.setattr(automatic_disk_cleanup.subprocess, "run", run)
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is True
    assert observed["args"] == ["/bin/uv", "cache", "prune"]
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["UV_CACHE_DIR"] == str(tmp_path)
    assert automatic_disk_cleanup._run_uv_cache_clean(tmp_path) is True
    assert observed["args"] == ["/bin/uv", "cache", "clean"]

    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is False
    assert automatic_disk_cleanup._run_uv_cache_clean(tmp_path) is False
    with pytest.raises(automatic_disk_cleanup.ProcessInspectionError):
        automatic_disk_cleanup._active_uv_process_pids()


def test_shared_uv_cache_additional_fail_closed_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=missing,
        approved_cache_root=missing,
    ).errors == (f"{missing}:uv-cache-inspection-failed",)

    cache = tmp_path / "cache"
    cache.mkdir()
    other_cache = tmp_path / "other-cache"
    other_cache.mkdir()
    unsafe = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=other_cache,
    )
    assert unsafe.errors == (f"{cache}:unsafe-uv-cache",)
    external = tmp_path / "external-cache"
    external.mkdir()
    symlink_cache = tmp_path / "symlink-cache"
    symlink_cache.symlink_to(external, target_is_directory=True)
    symlink_result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=symlink_cache,
        approved_cache_root=symlink_cache,
    )
    assert symlink_result.errors == (f"{symlink_cache}:unsafe-uv-cache",)
    file_cache = tmp_path / "file-cache"
    file_cache.write_text("not a directory\n", encoding="utf-8")
    file_result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=file_cache,
        approved_cache_root=file_cache,
    )
    assert file_result.errors == (f"{file_cache}:unsafe-uv-cache",)

    def fail_process() -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("unavailable")

    initial_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_process,
    )
    assert initial_failure.errors == (f"{cache}:uv-process-inspection-failed",)

    recheck_reads = iter(([], automatic_disk_cleanup.ProcessInspectionError("late")))

    def fail_recheck() -> list[int]:
        value = next(recheck_reads)
        if isinstance(value, Exception):
            raise value
        return value

    recheck_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_recheck,
    )
    assert recheck_failure.errors == (f"{cache}:uv-process-revalidation-failed",)
    dry_run = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        dry_run=True,
    )
    assert dry_run.skipped == (f"{cache}:would prune shared uv cache",)
    prune_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        run_prune=lambda _path: False,
    )
    assert prune_failure.errors == (f"{cache}:uv-cache-prune-failed",)

    final_reads = iter(
        ([], [], automatic_disk_cleanup.ProcessInspectionError("final"))
    )

    def fail_final_check() -> list[int]:
        value = next(final_reads)
        if isinstance(value, Exception):
            raise value
        return value

    final_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_final_check,
        run_prune=lambda _path: True,
    )
    assert final_failure.errors == (f"{cache}:uv-process-final-check-failed",)
    clean_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        run_prune=lambda _path: True,
        run_clean=lambda _path: False,
    )
    assert clean_failure.errors == (f"{cache}:uv-cache-clean-failed",)


def test_shared_uv_cache_missing_is_clean_only_for_convergent_cleanup(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"

    result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=missing,
        approved_cache_root=missing,
        missing_is_clean=True,
    )

    assert result.removed == ()
    assert result.skipped == (f"{missing}:uv-cache-absent",)
    assert result.errors == ()


def test_default_cleanup_discovers_and_preserves_git_worktrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = _record(tmp_path / "main", "development")
    root = tmp_path / "gludd-worktrees"
    finished = _record(root / "finished", "feature/finished")
    cache = finished.path / ".venv"
    cache.mkdir()
    records = [main, finished]
    registry_path = tmp_path / "active.json"
    monkeypatch.setattr(automatic_disk_cleanup, "DEFAULT_TMP_WORKTREE_ROOT", root)
    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", lambda: records)
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "_integration_points",
        lambda _records: (automatic_disk_cleanup.IntegrationPoint("main-head", 1),),
    )
    evidence_archive = tmp_path / "git-common" / "gludd-release-evidence"
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "_canonical_evidence_archive_root",
        lambda: evidence_archive,
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.workstream_registry,
        "default_registry_path",
        lambda: registry_path,
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.clean_ci_shard_scratch,
        "_active_process_pids",
        lambda _path: [],
    )
    stale_file = tmp_path / "gludd-test-stale.json"
    monkeypatch.setattr(
        automatic_disk_cleanup.clean_ci_shard_scratch,
        "clean_ci_shard_scratch",
        lambda **kwargs: {"removed": [str(stale_file)], "skipped": []},
    )
    uv_cleanup_calls: list[tuple[bool, bool]] = []
    node_cleanup_calls: list[bool] = []
    terraform_cleanup_calls: list[bool] = []
    invoking_cleanup_calls: list[dict[str, object]] = []
    invoking_cache = tmp_path / "invoking" / ".pytest_cache"
    main_cache = main.path / automatic_disk_cleanup.TERRAFORM_PLUGIN_CACHE_PATH

    def invoking_cleanup(**kwargs: object) -> automatic_disk_cleanup.CleanupResult:
        invoking_cleanup_calls.append(kwargs)
        removed = main_cache if kwargs["worktree"] == main.path else invoking_cache
        return automatic_disk_cleanup.CleanupResult((str(removed),), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_invoking_worktree_disposable_caches",
        invoking_cleanup,
    )

    def uv_cleanup(
        *, dry_run: bool = False, missing_is_clean: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        uv_cleanup_calls.append((dry_run, missing_is_clean))
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(automatic_disk_cleanup, "prune_shared_uv_cache", uv_cleanup)

    def node_cleanup(
        *, dry_run: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        node_cleanup_calls.append(dry_run)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup, "clean_stale_node_download_caches", node_cleanup
    )

    def terraform_cleanup(
        *, dry_run: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        terraform_cleanup_calls.append(dry_run)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_owned_terraform_provider_caches",
        terraform_cleanup,
    )

    result = automatic_disk_cleanup._automatic_cleanup()

    assert result.removed == (str(invoking_cache), str(main_cache), str(stale_file))
    assert any("completion proof required" in item for item in result.skipped)
    assert cache.exists()
    assert finished.path.exists()
    assert main.path.exists()
    assert uv_cleanup_calls == [(False, True)]
    assert node_cleanup_calls == [False]
    assert terraform_cleanup_calls == [False]
    assert len(invoking_cleanup_calls) == 2
    assert invoking_cleanup_calls[0]["records"] == records
    assert invoking_cleanup_calls[0]["approved_roots"] == (
        main.path / ".claude/worktrees",
        root,
    )
    assert invoking_cleanup_calls[0]["dry_run"] is False
    assert invoking_cleanup_calls[1]["worktree"] == main.path
    assert invoking_cleanup_calls[1]["approved_worktrees"] == (main.path,)
    assert invoking_cleanup_calls[1]["cache_paths"] == (
        automatic_disk_cleanup.TERRAFORM_PLUGIN_CACHE_PATH,
    )
    assert invoking_cleanup_calls[1]["dry_run"] is False


def test_default_cleanup_and_recheck_inspection_fail_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_discovery() -> list[WorktreeRecord]:
        raise automatic_disk_cleanup.DiskInspectionError("git unavailable")

    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", fail_discovery)
    assert automatic_disk_cleanup._automatic_cleanup().errors == (
        "worktree-discovery:inspection-failed",
    )

    calls = 0

    def fail_recheck() -> DiskSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            return DiskSnapshot(120.0, 20.0)
        raise automatic_disk_cleanup.DiskInspectionError("df unavailable")

    assert (
        automatic_disk_cleanup.run_preflight(
            inspect_usage=fail_recheck,
            cleanup=lambda: automatic_disk_cleanup.CleanupResult(
                (), ("active worktree",), ()
            ),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "action=skip" in captured.out
    assert "phase=recheck status=failed" in captured.err


def test_default_cleanup_runs_independent_reclaimers_after_discovery_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_discovery() -> list[WorktreeRecord]:
        raise automatic_disk_cleanup.DiskInspectionError("git unavailable")

    stale = tmp_path / "stale-scratch"
    uv_cache = tmp_path / "uv-cache"
    providers = tmp_path / "providers"
    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", fail_discovery)
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_stale_generated_scratch",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(stale),), (), ()),
    )
    node_cache = tmp_path / "node-cache"
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_stale_node_download_caches",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult(
            (str(node_cache),), (), ()
        ),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "prune_shared_uv_cache",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(uv_cache),), (), ()),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_owned_terraform_provider_caches",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(providers),), (), ()),
    )

    result = automatic_disk_cleanup._automatic_cleanup()

    assert result.removed == (
        str(stale),
        str(node_cache),
        str(uv_cache),
        str(providers),
    )
    assert result.errors == ("worktree-discovery:inspection-failed",)


def test_main_delegates_to_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", lambda: 7)

    assert automatic_disk_cleanup.main() == 7


def test_main_dry_run_injects_non_mutating_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_modes: list[bool] = []

    def cleanup(
        *,
        dry_run: bool = False,
        receipt_grace_seconds: int = (
            automatic_disk_cleanup.DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS
        ),
    ) -> automatic_disk_cleanup.CleanupResult:
        assert (
            receipt_grace_seconds
            == automatic_disk_cleanup.DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS
        )
        cleanup_modes.append(dry_run)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    def preflight(*, cleanup=None) -> int:
        assert cleanup is not None
        cleanup()
        return 7

    monkeypatch.setattr(automatic_disk_cleanup, "_automatic_cleanup", cleanup)
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", preflight)

    assert automatic_disk_cleanup.main(["--dry-run"]) == 7
    assert cleanup_modes == [True]


def test_main_configures_receipt_grace_and_enforces_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_graces: list[int] = []

    def cleanup(
        *, dry_run: bool = False, receipt_grace_seconds: int
    ) -> automatic_disk_cleanup.CleanupResult:
        assert dry_run is False
        observed_graces.append(receipt_grace_seconds)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    def preflight(*, cleanup=None) -> int:
        assert cleanup is not None
        cleanup()
        return 7

    monkeypatch.setattr(automatic_disk_cleanup, "_automatic_cleanup", cleanup)
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", preflight)

    assert automatic_disk_cleanup.main(["--receipt-grace-seconds", "3600"]) == 7
    assert observed_graces == [3600]
    with pytest.raises(SystemExit):
        automatic_disk_cleanup.main(["--receipt-grace-seconds", "1799"])
    with pytest.raises(SystemExit):
        automatic_disk_cleanup.main(["--receipt-grace-seconds", "not-an-integer"])


def test_make_and_precommit_gates_run_the_automatic_preflight() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    target = makefile.split("\ndisk-cleanup-preflight:\n", 1)[1].split(
        "\ncheck-disk:", 1
    )[0]
    gate = makefile.split("\ngate:", 1)[1].split("\n\n", 1)[0]
    gate_preflights = makefile.split("GATE_PREFLIGHT_TARGETS :=", 1)[1].split(
        "GATE_PREFLIGHT_STATUS", 1
    )[0]
    gate_fast = makefile.split("\ngate-fast:", 1)[1].split("\n", 1)[0]
    gate_lite = makefile.split("\ngate-lite:", 1)[1].split("\n", 1)[0]

    assert "$(SYSTEM_PYTHON) -m scripts.automatic_disk_cleanup" in target
    assert "DISK_CLEANUP_PREFLIGHT_DRY_RUN" in target
    assert "DISK_CLEANUP_RECEIPT_GRACE_SECONDS" in target
    assert "--receipt-grace-seconds" in target
    assert "--dry-run" in target
    assert "$(UV) run" not in target
    assert "disk-cleanup-preflight" in gate_preflights
    assert "_gate-preflights" in gate
    assert "disk-cleanup-preflight" in gate_fast
    assert "disk-cleanup-preflight" in gate_lite
    assert "entry: make check-disk CHECK_DISK_VALIDATE_ONLY=0" in hooks


def test_feature_document_records_zdd_rollback_and_long_lived_reports() -> None:
    document = (ROOT / "docs" / "automatic-disk-cleanup.md").read_text(encoding="utf-8")

    assert "Zero-downtime" in document
    assert "Rollback" in document
    assert "github.com/astral-sh/uv/issues/11432" in document
    assert "github.com/astral-sh/uv/issues/11694" in document
    assert "github.com/stablyai/orca/issues/10562" in document
    assert "github.com/python/cpython/issues/111246" in document
    assert "github.com/pytest-dev/pytest/discussions/10325" in document
    assert "Eight passes" in document
    assert "lsof" in document


def test_gate_lifecycle_documents_owned_node_cache_reclamation() -> None:
    document = (
        ROOT / "docs" / "features" / "GATE_RESOURCE_LIFECYCLE.md"
    ).read_text(encoding="utf-8")

    assert "gludd-npm-cache-public-v1" in document
    assert "50,000" in document
    assert "zero-byte regular-file lookalikes" in document
    assert "github.com/npm/cli/issues/3176" in document
    assert "github.com/npm/npm/issues/2500" in document
