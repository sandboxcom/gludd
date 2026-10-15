"""Native Git selection and fail-closed idle-worktree bytecode reclamation."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts import automatic_disk_cleanup as cleanup
from scripts import automatic_disk_cleanup_bytecode as engine
from scripts import staged_snapshot
from scripts.makefile_layout import compose_makefile

if TYPE_CHECKING:
    from prune_worktrees_safe import WorktreeRecord
else:
    from scripts.prune_worktrees_safe import WorktreeRecord


def _git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], cwd=root, capture_output=True, check=True, timeout=15,
    )
    return result.stdout


@pytest.fixture
def idle(tmp_path: Path) -> WorktreeRecord:
    main = tmp_path / "pytest-of-bytecode" / "repository"
    main.mkdir(parents=True)
    _git(main, "init", "--quiet")
    _git(main, "config", "user.name", "Bytecode Test")
    _git(main, "config", "user.email", "bytecode@example.invalid")
    _git(main, "config", "commit.gpgsign", "false")
    (main / ".gitignore").write_text(
        "__pycache__/\n*.py[co]\n.gludd/\n.gate-logs/\nci-attempt-logs/\n.venv/\n"
        "node_modules/\npreserved-evidence/\nrelease-evidence/\n", encoding="utf-8",
    )
    source = main / "src" / "module.py"
    source.parent.mkdir()
    source.write_text("SOURCE = 1\n", encoding="utf-8")
    tracked = main / "src" / "__pycache__" / "tracked.pyc"
    tracked.parent.mkdir()
    tracked.write_bytes(b"tracked fixture")
    _git(main, "add", ".gitignore", "src/module.py")
    _git(main, "add", "--force", "src/__pycache__/tracked.pyc")
    _git(main, "commit", "--quiet", "-m", "seed isolated bytecode fixture")
    path = tmp_path / "gludd-worktrees" / "idle"
    path.parent.mkdir()
    _git(main, "worktree", "add", "--quiet", "-b", "agent/idle", str(path))
    return WorktreeRecord(path=path.resolve(), branch="agent/idle", locked=False)


def _bytecode(record: WorktreeRecord, relative: str = "src/__pycache__/module.cpython-311.pyc") -> Path:
    path = record.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"regenerable bytecode")
    return path


def _clean(record: WorktreeRecord, *, dry_run: bool = False) -> cleanup.CleanupResult:
    return cleanup.clean_inactive_worktree_caches(
        records=[record], approved_roots=(record.path.parent,), protected_paths=frozenset(),
        repository_root=record.path.parent.parent / "pytest-of-bytecode" / "repository",
        active_branches=lambda: frozenset(), refresh_records=lambda: [record],
        active_process_pids=lambda _path: [], dry_run=dry_run,
    )


def test_native_cleanup_unlinks_only_ignored_untracked_bytecode_and_keeps_all_git_authority(
    idle: WorktreeRecord, tmp_path: Path,
) -> None:
    candidates = [
        _bytecode(idle), _bytecode(idle, "__pycache__/root.pyc"),
        _bytecode(idle, "a space\na newline/__pycache__/name with space\nand newline.pyo"),
    ]
    protected = [
        _bytecode(idle, f"{directory}/nested/__pycache__/proof.pyc")
        for directory in (
            ".gludd", ".gate-logs", "ci-attempt-logs", ".venv", "node_modules",
            "preserved-evidence", "release-evidence",
        )
    ]
    legacy = _bytecode(idle, "legacy.pyc")
    nonbytecode = _bytecode(idle, "src/__pycache__/keep.txt")
    external = tmp_path / "external"
    external.mkdir()
    victim = external / "victim.pyc"
    victim.write_bytes(b"outside ownership")
    linked_leaf = idle.path / "src" / "__pycache__" / "linked.pyc"
    linked_leaf.symlink_to(victim)
    (idle.path / "linked-parent").symlink_to(external, target_is_directory=True)
    index = _git(idle.path, "ls-files", "--stage", "-z")
    refs = _git(idle.path, "show-ref")
    source = (idle.path / "src/module.py").read_bytes()

    result = _clean(idle)

    assert all(not path.exists() for path in candidates)
    assert result.removed
    assert all(path.read_bytes() == b"regenerable bytecode" for path in [*protected, legacy, nonbytecode])
    assert (idle.path / "src/__pycache__/tracked.pyc").read_bytes() == b"tracked fixture"
    assert (idle.path / "src/module.py").read_bytes() == source
    assert linked_leaf.is_symlink() and victim.read_bytes() == b"outside ownership"
    assert (idle.path / "linked-parent").is_symlink()
    assert (idle.path / "src/__pycache__").is_dir(), "the phase never removes a parent directory"
    assert _git(idle.path, "ls-files", "--stage", "-z") == index
    assert _git(idle.path, "show-ref") == refs


@pytest.mark.parametrize("cap", ["candidates", "output", "deadline"])
def test_capacity_defers_without_mutation_errors_or_materialization_fallback(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, cap: str,
) -> None:
    candidate = _bytecode(idle)
    setting = {"candidates": "MAX_BYTECODE_FILES", "output": "MAX_GIT_OUTPUT_BYTES", "deadline": "MAX_BYTECODE_SECONDS"}
    monkeypatch.setattr(engine, setting[cap], 0)
    attempts: list[WorktreeRecord] = []

    def remove_materialization(record: WorktreeRecord, _dry: bool) -> cleanup.LifecycleDecision:
        attempts.append(record)
        return cleanup.LifecycleDecision(True, "unsafe")

    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        active_branches=lambda: frozenset(), refresh_records=lambda: [idle],
        active_process_pids=lambda _path: [],
        remove_materialization=remove_materialization,
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
    )
    assert candidate.is_file()
    assert not result.removed and not result.errors
    assert attempts == []
    assert any("deferred" in item for item in result.skipped)


def test_deadline_after_first_unlink_reports_actual_partial_reclaim_and_retains_remainder(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    candidates = [_bytecode(idle), _bytecode(idle, "src/__pycache__/second.pyc")]
    budget = engine.Budget()
    monkeypatch.setattr(engine, "Budget", lambda: budget)
    unlink = os.unlink

    def expire_after_unlink(path: str, *, dir_fd: int) -> None:
        unlink(path, dir_fd=dir_fd)
        budget.deadline = 0

    monkeypatch.setattr(os, "unlink", expire_after_unlink)
    result = _clean(idle)
    assert sum(path.is_file() for path in candidates) == 1
    assert any("bytecode files=1 bytes=20" in item for item in result.removed)
    assert not result.errors
    assert any("deferred" in item for item in result.skipped)
    output = capsys.readouterr().out
    assert "status=deferred" in output and "removed_files=1 removed_bytes=20" in output


@pytest.mark.parametrize("change", ["index", "ignore", "leaf", "pid", "inspection"])
def test_batch_revalidation_refuses_changed_index_ignore_leaf_and_process_ownership(
    idle: WorktreeRecord, change: str,
) -> None:
    candidate = _bytecode(idle)
    scans = 0

    def pids(_path: Path) -> list[int]:
        nonlocal scans
        scans += 1
        if scans == 4:
            if change == "index":
                _git(idle.path, "add", "--force", str(candidate.relative_to(idle.path)))
            elif change == "ignore":
                (idle.path / "src/.gitignore").write_text("# changed during cleanup\n", encoding="utf-8")
            elif change == "leaf":
                candidate.write_bytes(b"new owner output")
            elif change == "pid":
                return [77889]
            else:
                raise cleanup.ProcessInspectionError("process evidence unavailable")
        return []

    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        active_branches=lambda: frozenset(), refresh_records=lambda: [idle], active_process_pids=pids,
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
    )
    assert candidate.is_file()
    assert not result.removed
    assert result.errors


def test_parent_swap_after_descriptor_open_never_unlinks_original_or_external_data(
    idle: WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _bytecode(idle)
    external = tmp_path / "outside"
    external.mkdir()
    victim = external / candidate.name
    victim.write_bytes(b"not owned")
    original = engine._open_parent
    opens = 0
    moved = candidate.parent.with_name("preserved-original")

    @contextlib.contextmanager
    def swap(root: Path, relative: Path, metadata: os.stat_result) -> Iterator[int]:
        nonlocal opens
        with original(root, relative, metadata) as descriptor:
            opens += 1
            if opens == 2:
                candidate.parent.rename(moved)
                candidate.parent.symlink_to(external, target_is_directory=True)
            yield descriptor

    monkeypatch.setattr(engine, "_open_parent", swap)
    result = _clean(idle)
    assert result.errors and not result.removed
    assert (moved / candidate.name).read_bytes() == b"regenerable bytecode"
    assert victim.read_bytes() == b"not owned"


@pytest.mark.parametrize("name", [
    "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_LITERAL_PATHSPECS",
])
def test_inherited_git_routing_cannot_select_a_different_index_or_ignore_policy(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    candidate = _bytecode(idle)
    monkeypatch.setenv(name, "untrusted routing")
    result = _clean(idle)
    assert candidate.is_file() and not result.removed
    assert any("override" in item for item in result.errors)


@pytest.mark.parametrize("raw", [b"unterminated", b"../escape.pyc\0", b"a\0a\0", b"\xff\0", b"source.py\0"])
def test_malformed_native_inventory_never_authorizes_a_leaf_or_broader_removal(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, raw: bytes,
) -> None:
    candidate = _bytecode(idle)
    native = staged_snapshot._git

    def inventory(root: Path, arguments: object, **_kwargs: object) -> bytes:
        assert isinstance(arguments, list)
        if arguments[0] == "ls-files":
            return raw
        return native(root, arguments)

    monkeypatch.setattr(staged_snapshot, "_git", inventory)
    result = _clean(idle)
    assert candidate.is_file() and not result.removed and result.errors


@pytest.mark.parametrize("failure", ["timeout", "unsupported-fd", "unlink"])
def test_native_inspection_and_unlink_failures_preserve_bytecode_and_report_failure(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    candidate = _bytecode(idle)
    if failure == "timeout":
        def timeout(*_args: object, **_kwargs: object) -> bytes:
            raise staged_snapshot.SnapshotError("Git command timed out after 30s: ls-files")
        monkeypatch.setattr(staged_snapshot, "_git", timeout)
    elif failure == "unsupported-fd":
        monkeypatch.setattr(engine, "DIRECTORY_RELATIVE_SUPPORTED", False)
    else:
        def cannot_unlink(_path: str, *, dir_fd: int) -> None:
            raise PermissionError(f"cannot unlink through descriptor {dir_fd}")
        monkeypatch.setattr(os, "unlink", cannot_unlink)
    result = _clean(idle)
    assert candidate.is_file() and not result.removed and result.errors


def test_registration_change_between_inventory_and_batch_prevents_mutation(idle: WorktreeRecord) -> None:
    candidate = _bytecode(idle)
    refreshes = 0

    def records() -> list[WorktreeRecord]:
        nonlocal refreshes
        refreshes += 1
        return [idle] if refreshes < 3 else []

    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        active_branches=lambda: frozenset(), refresh_records=records, active_process_pids=lambda _path: [],
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
    )
    assert candidate.is_file() and not result.removed and result.errors


@pytest.mark.parametrize("phase", ["before", "inventory"])
def test_same_head_foreign_git_route_cannot_make_local_tracked_bytecode_disposable(
    idle: WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    candidate = _bytecode(idle)
    main = tmp_path / "pytest-of-bytecode" / "repository"
    foreign = tmp_path / "pytest-of-bytecode" / "foreign"
    _git(main, "clone", "--quiet", "--shared", str(main), str(foreign))
    _git(foreign, "update-index", "--force-remove", "src/__pycache__/tracked.pyc")
    assert _git(idle.path, "rev-parse", "HEAD") == _git(foreign, "rev-parse", "HEAD")
    marker = idle.path / ".git"
    original_marker = marker.read_bytes()
    index_path = Path(
        _git(idle.path, "rev-parse", "--path-format=absolute", "--git-path", "index").decode().rstrip("\n")
    )
    index = index_path.read_bytes()
    refs = _git(main, "show-ref")
    native = staged_snapshot._git
    swapped = False

    def swap() -> None:
        nonlocal swapped
        marker.write_text(f"gitdir: {foreign / '.git'}\n", encoding="utf-8")
        swapped = True

    def routed(root: Path, arguments: object, **_kwargs: object) -> bytes:
        assert isinstance(arguments, list)
        if root == idle.path and arguments[0] == "ls-files" and not swapped:
            swap()
        return native(root, arguments)

    if phase == "before":
        swap()
    else:
        monkeypatch.setattr(staged_snapshot, "_git", routed)
    try:
        result = _clean(idle)
    finally:
        marker.write_bytes(original_marker)
    tracked = idle.path / "src/__pycache__/tracked.pyc"
    assert tracked.is_file() and tracked.read_bytes() == b"tracked fixture"
    assert candidate.is_file() and not result.removed and result.errors
    assert index_path.read_bytes() == index
    assert _git(main, "show-ref") == refs


def test_bytecode_dry_run_reports_exact_eligible_files_and_bytes_without_mutation(
    idle: WorktreeRecord, capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = _bytecode(idle)
    result = _clean(idle, dry_run=True)
    assert candidate.is_file()
    assert not result.removed
    assert any("would remove bytecode" in item for item in result.skipped)
    output = capsys.readouterr().out
    assert "eligible_files=1" in output
    assert f"eligible_bytes={len(candidate.read_bytes())}" in output
    assert "status=dry-run" in output


@pytest.mark.parametrize("protection", ["main", "current", "locked", "detached", "prunable", "live", "fresh-active"])
def test_bytecode_keeps_protected_live_and_fresh_active_worktrees(
    idle: WorktreeRecord, protection: str,
) -> None:
    candidate = _bytecode(idle)
    record = WorktreeRecord(
        path=idle.path, branch=None if protection == "detached" else idle.branch,
        locked=protection == "locked", prunable=protection == "prunable",
    )
    active = frozenset({idle.branch or ""}) if protection == "fresh-active" else frozenset()
    lease = cleanup.WorkstreamLease(idle.branch or "", idle.path, 100)
    result = cleanup.clean_inactive_worktree_caches(
        records=[record], approved_roots=(idle.path.parent,),
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
        protected_paths=frozenset({idle.path}) if protection in {"main", "current"} else frozenset(),
        active_branches=lambda: active, refresh_records=lambda: [record],
        active_workstream_leases=lambda: {idle.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: cleanup.LifecycleDecision(False, "fresh active lease"),
        active_process_pids=lambda _path: [99887] if protection == "live" else [],
    )
    assert candidate.is_file()
    assert not result.removed


def test_bytecode_accepts_only_an_unchanged_positive_completion_proof(idle: WorktreeRecord) -> None:
    candidate = _bytecode(idle)
    lease = cleanup.WorkstreamLease(idle.branch or "", idle.path, 100)
    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
        active_branches=lambda: frozenset({idle.branch or ""}), refresh_records=lambda: [idle],
        active_workstream_leases=lambda: {idle.branch or "": lease},
        lifecycle_proof=lambda _record, _lease: cleanup.LifecycleDecision(
            True, "exact-head successful commit receipt", cache_only=True,
        ),
        active_process_pids=lambda _path: [],
    )
    assert not candidate.exists()
    assert result.removed


@pytest.mark.parametrize("dry_run", [True, False])
def test_many_directory_native_fixture_has_linear_leaf_metadata_cost_and_bounded_progress(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], dry_run: bool,
) -> None:
    count = 1200
    for index in range(count):
        _bytecode(idle, f"package-{index // 4}/__pycache__/module-{index}.pyc")
    stamp = engine._stamp
    inspections = 0

    def counted(path: Path) -> os.stat_result | None:
        nonlocal inspections
        inspections += 1
        return stamp(path)

    monkeypatch.setattr(engine, "_stamp", counted)
    started = time.monotonic()
    result = _clean(idle, dry_run=dry_run)
    elapsed = time.monotonic() - started
    assert not result.errors
    assert inspections < count * 100, "per-leaf checks cannot rescan every unrelated directory"
    assert bool(result.removed) is not dry_run
    output = capsys.readouterr().out
    assert f"eligible_files={count} eligible_bytes={count * 20}" in output
    assert len(output.splitlines()) < 10, "bytecode progress is batched, never per-file output"
    print(
        f"native-bytecode-fixture files={count} directories=300 dry_run={dry_run} "
        f"elapsed={elapsed:.3f}s stamps={inspections}"
    )


def test_initial_bytecode_process_inspection_failure_remains_fatal(idle: WorktreeRecord) -> None:
    candidate = _bytecode(idle)
    scans = 0

    def pids(_path: Path) -> list[int]:
        nonlocal scans
        scans += 1
        if scans == 3:
            raise cleanup.ProcessInspectionError("late process evidence unavailable")
        return []

    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        active_branches=lambda: frozenset(), refresh_records=lambda: [idle], active_process_pids=pids,
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
    )
    assert candidate.is_file() and not result.removed
    assert result.errors, "inspection uncertainty cannot become a successful cleanup skip"


@pytest.mark.parametrize("change", ["lease", "proof"])
def test_positive_completion_lease_or_proof_change_before_batch_preserves_bytecode(
    idle: WorktreeRecord, change: str,
) -> None:
    candidate = _bytecode(idle)
    lease = cleanup.WorkstreamLease(idle.branch or "", idle.path, 100)
    leases_read = proofs_read = 0

    def leases() -> dict[str, cleanup.WorkstreamLease]:
        nonlocal leases_read
        leases_read += 1
        observed = (
            cleanup.WorkstreamLease(lease.branch, lease.worktree, 101)
            if change == "lease" and leases_read == 4 else lease
        )
        return {idle.branch or "": observed}

    def proof(_record: WorktreeRecord, _lease: cleanup.WorkstreamLease) -> cleanup.LifecycleDecision:
        nonlocal proofs_read
        proofs_read += 1
        return cleanup.LifecycleDecision(not (change == "proof" and proofs_read == 4), "changed")

    result = cleanup.clean_inactive_worktree_caches(
        records=[idle], approved_roots=(idle.path.parent,), protected_paths=frozenset(),
        active_branches=lambda: frozenset({idle.branch or ""}), refresh_records=lambda: [idle],
        active_workstream_leases=leases, lifecycle_proof=proof, active_process_pids=lambda _path: [],
        repository_root=idle.path.parent.parent / "pytest-of-bytecode" / "repository",
    )
    assert candidate.is_file() and not result.removed and result.errors


def test_native_make_disk_dry_run_bootstraps_with_pinned_system_python_without_host_cleanup(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    composed = compose_makefile(project / "Makefile")
    recipe = composed.split("\ndisk-cleanup-preflight:\n", 1)[1].split("\n\n", 1)[0]
    pin = next(line for line in composed.splitlines() if line.startswith("override SYSTEM_PYTHON :="))
    (tmp_path / "Makefile").write_text(pin + "\ndisk-cleanup-preflight:\n" + recipe + "\n", encoding="utf-8")
    package = tmp_path / "scripts"
    package.mkdir()
    (package / "__init__.py").write_text(f"__path__.append({str(project / 'scripts')!r})\n", encoding="utf-8")
    source = project / "scripts" / "automatic_disk_cleanup.py"
    (package / "automatic_disk_cleanup.py").write_text(
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('scripts.native_probe', {str(source)!r})\n"
        "module = importlib.util.module_from_spec(spec)\nsys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "def harmless(**kw):\n"
        "    print('NATIVE_BOOTSTRAP ' + json.dumps({'dry_run': kw['dry_run'], 'python': sys.version_info[:2]}))\n"
        "    return module.CleanupResult((), (), ())\n"
        "module._automatic_cleanup = harmless\nnative = module.run_preflight\n"
        "usage = iter((module.DiskSnapshot(0, 96), module.DiskSnapshot(0, 0)))\n"
        "module.run_preflight = lambda **kw: native(inspect_usage=lambda: next(usage), **kw)\n"
        "raise SystemExit(module.main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    environment = {key: value for key, value in os.environ.items() if key not in {
        "MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS",
    }}
    environment["PYTHONPATH"] = str(tmp_path)
    result = subprocess.run([
        "make", "--no-print-directory", "disk-cleanup-preflight",
        "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0", "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1",
        "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
    ], cwd=tmp_path, env=environment, text=True, capture_output=True, check=False, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'NATIVE_BOOTSTRAP {"dry_run": true' in result.stdout


def test_planning_deadline_stops_before_opening_the_next_candidate(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_bytecode(idle), _bytecode(idle, "src/__pycache__/second.pyc")]
    budget = engine.Budget()
    monkeypatch.setattr(engine, "Budget", lambda: budget)
    original = engine._open_parent
    opens = 0

    @contextlib.contextmanager
    def expire(root: Path, relative: Path, metadata: os.stat_result) -> Iterator[int]:
        nonlocal opens
        with original(root, relative, metadata) as descriptor:
            opens += 1
            yield descriptor
        budget.deadline = 0

    monkeypatch.setattr(engine, "_open_parent", expire)
    result = _clean(idle)
    assert opens == 1
    assert all(path.is_file() for path in candidates) and not result.removed


def test_post_unlink_descriptor_close_error_keeps_actual_removed_counters(
    idle: WorktreeRecord, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _bytecode(idle)
    original = engine._open_parent
    opens = 0

    @contextlib.contextmanager
    def close_failure(root: Path, relative: Path, metadata: os.stat_result) -> Iterator[int]:
        nonlocal opens
        with original(root, relative, metadata) as descriptor:
            opens += 1
            yield descriptor
        if opens == 2:
            raise OSError("descriptor close failed after successful unlink")

    monkeypatch.setattr(engine, "_open_parent", close_failure)
    result = _clean(idle)
    assert not candidate.exists()
    assert any("bytecode files=1 bytes=20" in item for item in result.removed)
    assert result.errors
