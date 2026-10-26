"""Regression coverage for active logical workstream pruning."""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import prune_worktrees_safe
    import workstream_registry
    from prune_worktrees_safe import WorktreeRecord, parse_worktrees, pruning_decision
    from workstream_registry import WorkstreamRegistry
else:
    from scripts import prune_worktrees_safe, workstream_registry
    from scripts.prune_worktrees_safe import WorktreeRecord, parse_worktrees, pruning_decision
    from scripts.workstream_registry import WorkstreamRegistry


pytest_plugins = ("tests.unit.test_worktree_retirement",)


def test_active_logical_workstream_is_never_pruned(tmp_path: Path) -> None:
    """A clean worktree remains protected while its branch is registered active."""
    registry_path = tmp_path / "active-workstreams.json"
    registry = WorkstreamRegistry(registry_path)
    worktree = tmp_path / "clean-worktree"
    registry.register("fix-beta4-shared-infra-failures", worktree)

    record = WorktreeRecord(
        path=worktree,
        branch="fix-beta4-shared-infra-failures",
        locked=False,
    )
    decision = pruning_decision(
        record,
        active_branches=registry.active_branches(),
        protected_paths=frozenset(),
    )

    assert decision.action == "protect"
    assert decision.reason == "active logical workstream"


def test_registry_is_atomic_and_unregisters_explicitly(
    tmp_path: Path, checkout: tuple[Path, Path, Path],
) -> None:
    _root, one, registry_path = checkout
    _retention_git(one, "branch", "-m", "feature/one")
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/one", one)
    registry.register("feature/two", tmp_path / "two")

    assert registry.active_branches() == frozenset({"feature/one", "feature/two"})
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert not list(tmp_path.glob("*.tmp"))

    registry.unregister("feature/one", one, _retention_git(one, "rev-parse", "HEAD").strip())

    assert registry.active_branches() == frozenset({"feature/two"})


def test_registry_refuses_active_path_reassignment_without_write(tmp_path: Path) -> None:
    registry = WorkstreamRegistry(tmp_path / "registry.json")
    retained, requested = tmp_path / "retained", tmp_path / "requested"
    registry.register("feature/owned", retained)
    registry.register("feature/peer", tmp_path / "peer")
    before = registry.path.read_bytes()
    with pytest.raises(ValueError, match=r"active workstream path mismatch.*reconcile"):
        registry.register("feature/owned", requested)
    assert registry.path.read_bytes() == before
    assert registry.active_branches() == frozenset({"feature/owned", "feature/peer"})
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("registry_same_path_kind", ("same", "symlink", "tmp-alias"))
def test_registry_renews_same_physical_owner(
    tmp_path: Path, registry_same_path_kind: str,
) -> None:
    registry = WorkstreamRegistry(tmp_path / "registry.json")
    retained = (tmp_path / "retained").resolve()
    retained.mkdir()
    registry.register("feature/owned", retained)
    requested = retained
    if registry_same_path_kind == "symlink":
        requested = tmp_path / "owner-alias"
        requested.symlink_to(retained, target_is_directory=True)
    elif registry_same_path_kind == "tmp-alias":
        requested = (Path(str(retained).replace("/private/tmp/", "/tmp/", 1))
                     if str(retained).startswith("/private/tmp/")
                     else retained / ".." / retained.name)
    assert requested.resolve() == retained
    registry.register("feature/owned", requested)
    entry = json.loads(registry.path.read_text(encoding="utf-8"))["workstreams"]["feature/owned"]
    assert entry["branch"] == "feature/owned" and entry["status"] == "active"
    assert entry["worktree"] == str(retained)
    assert registry.active_branches() == frozenset({"feature/owned"})


@pytest.mark.parametrize("registry_invalid_active_path", [None, "", 17])
def test_registry_refuses_unverifiable_active_path_without_write(
    tmp_path: Path, registry_invalid_active_path: object,
) -> None:
    registry = WorkstreamRegistry(tmp_path / "registry.json")
    payload = {"version": 1, "workstreams": {"feature/owned": {
        "branch": "feature/owned", "status": "active", "worktree": registry_invalid_active_path,
    }}}
    registry.path.write_text(json.dumps(payload), encoding="utf-8")
    before = registry.path.read_bytes()
    with pytest.raises(ValueError, match="active workstream path mismatch"):
        registry.register("feature/owned", tmp_path / "requested")
    assert registry.path.read_bytes() == before


def test_registry_can_replace_an_explicitly_inactive_path(tmp_path: Path) -> None:
    registry = WorkstreamRegistry(tmp_path / "registry.json")
    registry.path.write_text(json.dumps({"version": 1, "workstreams": {"feature/owned": {
        "branch": "feature/owned", "status": "inactive", "worktree": str(tmp_path / "old"),
    }}}), encoding="utf-8")
    registry.register("feature/owned", tmp_path / "requested")
    entry = json.loads(registry.path.read_text(encoding="utf-8"))["workstreams"]["feature/owned"]
    assert entry["worktree"] == str((tmp_path / "requested").resolve())
    assert entry["status"] == "active"


def test_locked_and_main_worktrees_remain_protected(tmp_path: Path) -> None:
    main = WorktreeRecord(path=tmp_path / "main", branch="development", locked=False)
    locked = WorktreeRecord(path=tmp_path / "locked", branch="feature/locked", locked=True)

    main_decision = pruning_decision(
        main,
        active_branches=frozenset(),
        protected_paths=frozenset({main.path}),
    )
    locked_decision = pruning_decision(
        locked,
        active_branches=frozenset(),
        protected_paths=frozenset(),
    )

    assert (main_decision.action, main_decision.reason) == ("protect", "current/main checkout")
    assert (locked_decision.action, locked_decision.reason) == ("protect", "git worktree lock")


def test_porcelain_parser_handles_active_locked_and_detached_worktrees(tmp_path: Path) -> None:
    output = (
        f"worktree {tmp_path / 'main'}\nHEAD abc\nbranch refs/heads/development\n\n"
        f"worktree {tmp_path / 'locked'}\nHEAD def\nbranch refs/heads/feature/locked\nlocked agent-active\n\n"
        f"worktree {tmp_path / 'detached'}\nHEAD fed\ndetached\n"
    )

    records = parse_worktrees(output)

    assert [record.branch for record in records] == ["development", "feature/locked", None]
    assert [record.locked for record in records] == [False, True, False]


def test_prunable_registration_is_parsed_and_pruned_without_path_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    missing = tmp_path / "missing"
    porcelain = (
        f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n\n"
        f"worktree {missing}\nHEAD def\nbranch refs/heads/feature/missing\n"
        "prunable gitdir file points to non-existent location\n"
    )
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ("worktree", "list", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, porcelain, "")
        if args == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess(args, 0, f"{main}\n", "")
        assert args == ("worktree", "prune", "--expire", "now")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(prune_worktrees_safe, "_git", fake_git)

    records = parse_worktrees(porcelain)
    assert records[1].prunable is True
    assert prune_worktrees_safe.prune(
        registry_path=tmp_path / "missing.json",
        validate_only=False,
    ) == 0
    assert ("worktree", "prune", "--expire", "now") in calls
    assert ("worktree", "remove", str(missing.resolve())) not in calls


def test_unregistered_candidate_is_removable(tmp_path: Path) -> None:
    record = WorktreeRecord(path=tmp_path / "idle", branch="feature/idle", locked=False)

    decision = pruning_decision(
        record,
        active_branches=frozenset({"feature/other"}),
        protected_paths=frozenset(),
    )

    assert (decision.action, decision.reason) == ("remove", "unregistered clean candidate")


def test_corrupt_registry_fails_closed(tmp_path: Path) -> None:
    registry_path = tmp_path / "broken.json"
    registry_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid active-workstream registry"):
        WorkstreamRegistry(registry_path).active_branches()


@pytest.mark.parametrize("branch", ["", "-bad", "bad..branch", "bad branch", "bad/"])
def test_invalid_branch_names_are_rejected(tmp_path: Path, branch: str) -> None:
    with pytest.raises(ValueError, match="invalid workstream branch"):
        WorkstreamRegistry(tmp_path / "registry.json").register(branch, tmp_path)


def test_default_registry_path_honors_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    expected = tmp_path / "registry.json"
    monkeypatch.setenv("GLUDD_ACTIVE_WORKSTREAM_REGISTRY", str(expected))

    assert workstream_registry.default_registry_path(tmp_path) == expected


def test_default_registry_path_is_shared_by_git_common_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    common_dir = tmp_path / "repository.git"
    temp_root = tmp_path / "scratch"
    monkeypatch.delenv("GLUDD_ACTIVE_WORKSTREAM_REGISTRY", raising=False)
    monkeypatch.setenv("TMPDIR", str(temp_root))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, f"{common_dir}\n", ""),
    )

    first = workstream_registry.default_registry_path(tmp_path / "worktree-one")
    second = workstream_registry.default_registry_path(tmp_path / "worktree-two")

    assert first == second
    assert first.parent == temp_root / "gludd-active-workstreams"


def test_unsupported_registry_schema_fails_closed(tmp_path: Path) -> None:
    registry_path = tmp_path / "old.json"
    registry_path.write_text('{"version": 0, "workstreams": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported active-workstream registry schema"):
        WorkstreamRegistry(registry_path).active_branches()


def test_registry_cli_register_list_and_unregister(
    checkout: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    _root, worktree, registry_path = checkout
    _retention_git(worktree, "branch", "-m", "feature/cli")

    assert (
        workstream_registry.main(
            [
                "register",
                "--branch",
                "feature/cli",
                "--worktree",
                str(worktree),
                "--registry",
                str(registry_path),
            ]
        )
        == 0
    )
    assert workstream_registry.main(["list", "--registry", str(registry_path)]) == 0
    assert '"feature/cli"' in capsys.readouterr().out
    assert (
        workstream_registry.main(
            ["unregister", "--branch", "feature/cli", "--registry", str(registry_path),
             "--worktree", str(worktree), "--expected-head", _retention_git(worktree, "rev-parse", "HEAD").strip()]
        )
        == 0
    )
    assert WorkstreamRegistry(registry_path).active_branches() == frozenset()


def test_registry_cli_requires_action_arguments(tmp_path: Path) -> None:
    registry = str(tmp_path / "registry.json")
    with pytest.raises(SystemExit, match="--branch is required"):
        workstream_registry.main(["register", "--registry", registry])
    with pytest.raises(SystemExit, match="--worktree is required"):
        workstream_registry.main(
            ["register", "--branch", "feature/missing-path", "--registry", registry]
        )


def test_prune_calls_git_remove_only_for_unregistered_candidate(
    monkeypatch: pytest.MonkeyPatch, checkout: tuple[Path, Path, Path],
) -> None:
    main, idle, registry_path = checkout
    active = main / ".claude/worktrees/active"
    _retention_git(main, "worktree", "add", "-b", "feature/active", str(active))
    WorkstreamRegistry(registry_path).register("feature/active", active)
    before_refs = _retention_git(main, "show-ref")
    calls: list[tuple[str, ...]] = []
    native = prune_worktrees_safe._git

    def recording_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return native(*args, check=check, timeout=timeout)

    monkeypatch.setattr(prune_worktrees_safe, "_git", recording_git)

    assert prune_worktrees_safe.prune(registry_path=registry_path, validate_only=False) == 0
    assert ("worktree", "remove", "--", str(idle.resolve())) in calls
    assert not any(args[:2] == ("worktree", "remove") and str(active.resolve()) in args for args in calls)
    assert not idle.exists() and active.is_dir()
    assert _retention_git(main, "show-ref") == before_refs


def test_validate_only_never_calls_git_remove(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    main = tmp_path / "main"
    idle = tmp_path / "idle"
    porcelain = (
        f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n\n"
        f"worktree {idle}\nHEAD def\nbranch refs/heads/feature/idle\n"
    )

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args == ("worktree", "list", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, porcelain, "")
        assert args == ("rev-parse", "--show-toplevel")
        return subprocess.CompletedProcess(args, 0, f"{main}\n", "")

    monkeypatch.setattr(prune_worktrees_safe, "_git", fake_git)

    assert prune_worktrees_safe.prune(registry_path=tmp_path / "missing.json", validate_only=True) == 0


def test_prune_handles_empty_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        prune_worktrees_safe,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    assert prune_worktrees_safe.prune(registry_path=tmp_path / "missing.json", validate_only=False) == 0


def test_dirty_candidate_is_retained(checkout: tuple[Path, Path, Path]) -> None:
    main, dirty, registry = checkout
    (dirty / "owned.txt").write_bytes(b"retain uncommitted source")
    before_refs = _retention_git(main, "show-ref")

    assert prune_worktrees_safe.prune(registry_path=registry, validate_only=False) == 0
    assert dirty.is_dir() and (dirty / "owned.txt").read_bytes() == b"retain uncommitted source"
    assert (dirty / ".gate-logs/failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert _retention_git(main, "show-ref") == before_refs


def test_prune_cli_passes_registry_and_validate_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    observed: dict[str, object] = {}

    def fake_prune(*, registry_path: Path, validate_only: bool) -> int:
        observed.update(registry_path=registry_path, validate_only=validate_only)
        return 0

    monkeypatch.setattr(prune_worktrees_safe, "prune", fake_prune)

    assert (
        prune_worktrees_safe.main(
            ["--registry", str(registry_path), "--validate-only"]
        )
        == 0
    )
    assert observed == {"registry_path": registry_path, "validate_only": True}


def _release_cli(branch: str, worktree: Path | None, head: str, registry: Path) -> int:
    """Retain the underlying native CLI status as well as enclosing Make controls."""
    arguments = ["unregister", "--branch", branch, "--expected-head", head, "--registry", str(registry)]
    if worktree is not None:
        arguments.extend(("--worktree", str(worktree)))
    try:
        return workstream_registry.main(arguments)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


def _release_make(
    root: Path, registry: Path, branch: str, worktree: Path | None, head: str,
) -> subprocess.CompletedProcess[str]:
    """Exercise the actual public release recipe in a confined native repository."""
    uv = shutil.which("uv")
    assert uv is not None
    environment = dict(os.environ)
    for key in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS", "MAKEFILES"):
        environment.pop(key, None)
    environment.update(
        PYTHONPATH=str(PROJECT_ROOT), PYTHONDONTWRITEBYTECODE="1",
        UV_PROJECT_ENVIRONMENT=sys.prefix, VIRTUAL_ENV=sys.prefix, UV_NO_SYNC="1",
        UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never",
    )
    return subprocess.run(
        [
            "make", "--no-print-directory", "-f",
            str(PROJECT_ROOT / "make/40-cross-version-and-worktrees.mk"),
            "workstream-unregister", f"UV={shlex.quote(uv)}",
            f"BRANCH={branch}", f"WORKTREE={worktree}" if worktree is not None else "WORKTREE=",
            f"WORKSTREAM_EXPECTED_HEAD={head}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
        ],
        cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("release_context", ("missing-path", "wrong-path", "wrong-head"))
def test_workstream_unregister_refuses_unmatched_make_context_without_writing(
    checkout: tuple[Path, Path, Path], release_context: str,
) -> None:
    """The old branch-only recipe must not erase a different or unpinned owner."""
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    before = registry_path.read_bytes()
    metadata = registry_path.stat()
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    selected = (
        None if release_context == "missing-path"
        else owner.parent / "foreign" if release_context == "wrong-path" else owner
    )
    if release_context == "wrong-head":
        head = "0" * 40
    result = _release_make(root, registry_path, "feature/retired", selected, head)
    assert result.returncode != 0, result.stdout + result.stderr
    assert _release_cli("feature/retired", selected, head, registry_path) != 0
    assert registry_path.read_bytes() == before
    after = registry_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (metadata.st_ino, metadata.st_mtime_ns)
    assert registry.active_branches() == frozenset({"feature/retired"})
    assert owner.is_dir()


def test_workstream_release_retains_verified_tuple_and_reconciles_idempotently(
    checkout: tuple[Path, Path, Path],
) -> None:
    """The existing registry retains independent path/head release authority."""
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    refs = _retention_git(root, "show-ref")
    result = _release_make(root, registry_path, "feature/retired", owner, head)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "released" and receipt["native_ownership_verified"] == 1
    assert (receipt["branch"], receipt["worktree"], receipt["head"]) == ("feature/retired", str(owner), head)
    row = json.loads(registry_path.read_text(encoding="utf-8"))["workstreams"]["feature/retired"]
    assert row["status"] == "released" and row["head"] == head and row["worktree"] == str(owner)
    assert registry.active_branches() == frozenset()
    before = registry_path.read_bytes()
    metadata = registry_path.stat()
    repeated = _release_make(root, registry_path, "feature/retired", owner, head)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert json.loads(repeated.stdout) == receipt
    assert _release_cli("feature/retired", owner, head, registry_path) == 0
    assert registry_path.read_bytes() == before
    after = registry_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (metadata.st_ino, metadata.st_mtime_ns)
    assert _retention_git(root, "show-ref") == refs and owner.is_dir()


def test_workstream_release_unknown_owner_creates_no_registry_or_lock(
    checkout: tuple[Path, Path, Path],
) -> None:
    """Missing ownership cannot become an empty newly-written success record."""
    root, owner, registry = checkout
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    lock = registry.with_suffix(registry.suffix + ".lock")
    assert not registry.exists() and not lock.exists()
    result = _release_make(root, registry, "feature/retired", owner, head)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not registry.exists() and not lock.exists()
    assert owner.is_dir()


@pytest.mark.parametrize("release_head", ("", "HEAD", "f" * 39, "g" * 40))
def test_workstream_release_requires_an_immutable_full_head_without_writing(
    checkout: tuple[Path, Path, Path], release_head: str,
) -> None:
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    before = registry_path.read_bytes()
    result = _release_make(root, registry_path, "feature/retired", owner, release_head)
    assert result.returncode != 0, result.stdout + result.stderr
    assert _release_cli("feature/retired", owner, release_head, registry_path) == 2
    assert registry_path.read_bytes() == before


def test_workstream_release_rechecks_native_head_under_the_existing_lock(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real commit at flock acquisition cannot release the old head's lease."""
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    before = registry_path.read_bytes()
    native_flock = fcntl.flock
    changed = False

    def advance_after_lock(fd: int, operation: int) -> None:
        nonlocal changed
        native_flock(fd, operation)
        if operation == fcntl.LOCK_EX and not changed:
            changed = True
            _retention_git(owner, "commit", "--allow-empty", "-m", "changed release owner")

    monkeypatch.setattr(fcntl, "flock", advance_after_lock)
    try:
        status = workstream_registry.main([
            "unregister", "--branch", "feature/retired", "--worktree", str(owner),
            "--expected-head", head, "--registry", str(registry_path),
        ])
    except SystemExit as exc:
        status = int(exc.code) if isinstance(exc.code, int) else 1
    assert status != 0 and changed
    assert registry_path.read_bytes() == before
    assert _retention_git(owner, "rev-parse", "HEAD").strip() != head
    assert registry.active_branches() == {"feature/retired"}
    assert root.is_dir()


def test_workstream_release_cannot_reuse_a_released_row_for_a_new_head(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    first = _release_make(root, registry_path, "feature/retired", owner, head)
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout)["status"] == "released"
    before = registry_path.read_bytes()
    _retention_git(owner, "commit", "--allow-empty", "-m", "new unleased head")
    changed_head = _retention_git(owner, "rev-parse", "HEAD").strip()
    stale = _release_make(root, registry_path, "feature/retired", owner, changed_head)
    assert stale.returncode != 0, stale.stdout + stale.stderr
    assert registry_path.read_bytes() == before
    assert registry.active_branches() == frozenset()


@pytest.mark.parametrize("native_context", ("missing", "relative", "symlink", "foreign", "branch", "detached"))
def test_workstream_release_refuses_noncanonical_or_foreign_native_owners(
    checkout: tuple[Path, Path, Path], tmp_path: Path, native_context: str,
) -> None:
    root, owner, registry_path = checkout
    selected, branch = owner, "feature/retired"
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    if native_context == "missing":
        selected = tmp_path / "missing-native-owner"
    elif native_context == "relative":
        selected = owner.relative_to(root)
    elif native_context == "symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(owner, target_is_directory=True)
    elif native_context == "foreign":
        selected = tmp_path / "foreign"
        selected.mkdir()
        _retention_git(selected, "init", "-b", branch)
        _retention_git(selected, "config", "user.name", "Foreign release fixture")
        _retention_git(selected, "config", "user.email", "foreign@example.invalid")
        _retention_git(selected, "commit", "--allow-empty", "-m", "foreign native root")
        head = _retention_git(selected, "rev-parse", "HEAD").strip()
    elif native_context == "branch":
        branch = "feature/claimed-other-owner"
    else:
        _retention_git(owner, "checkout", "--detach", head)
    registry = WorkstreamRegistry(registry_path)
    registry.register(branch, root / selected if not selected.is_absolute() else selected)
    before = registry_path.read_bytes()
    refs = _retention_git(root, "show-ref")
    result = _release_make(root, registry_path, branch, selected, head)
    assert result.returncode != 0, result.stdout + result.stderr
    assert _release_cli(branch, selected, head, registry_path) == 2
    assert registry_path.read_bytes() == before and registry.active_branches() == {branch}
    assert _retention_git(root, "show-ref") == refs and owner.is_dir()


@pytest.mark.parametrize("bad_row", ("branch", "worktree", "status", "epoch-string", "epoch-boolean", "missing"))
def test_workstream_release_corrupt_row_refusal_never_rewrites_registry(
    checkout: tuple[Path, Path, Path], bad_row: str,
) -> None:
    """Deliberately invalid native state is a negative, never release proof."""
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    row = payload["workstreams"]["feature/retired"]
    if bad_row == "missing":
        payload["workstreams"].pop("feature/retired")
    elif bad_row == "epoch-string":
        row["updated_epoch"] = "unverified"
    elif bad_row == "epoch-boolean":
        row["updated_epoch"] = True
    else:
        row[bad_row] = "foreign-or-unknown"
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    before = registry_path.read_bytes()
    metadata = registry_path.stat()
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    result = _release_make(root, registry_path, "feature/retired", owner, head)
    assert result.returncode != 0, result.stdout + result.stderr
    assert registry_path.read_bytes() == before
    after = registry_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (metadata.st_ino, metadata.st_mtime_ns)
    assert owner.is_dir()


def test_registry_renews_same_active_path_and_refuses_native_owner_reassignment(
    checkout: tuple[Path, Path, Path],
) -> None:
    _root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    registry.register("feature/retired", owner)
    before = registry_path.read_bytes()
    metadata = registry_path.stat()
    with pytest.raises(ValueError, match="active workstream path mismatch"):
        registry.register("feature/retired", owner.parent / "different-native-owner")
    assert registry_path.read_bytes() == before
    after = registry_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (metadata.st_ino, metadata.st_mtime_ns)
    assert registry.active_branches() == {"feature/retired"}


def test_workstream_release_rechecks_replaced_native_backlink_at_flock(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real same-content backlink replacement cannot inherit release authority."""
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    before = registry_path.read_bytes()
    native_flock = fcntl.flock
    changed = False

    def replace_after_lock(fd: int, operation: int) -> None:
        nonlocal changed
        native_flock(fd, operation)
        if operation == fcntl.LOCK_EX and not changed:
            changed = True
            backlink = owner / ".git"
            previous_inode = backlink.stat().st_ino
            replacement = owner / ".git.release-fixture"
            replacement.write_bytes(backlink.read_bytes())
            replacement.replace(backlink)
            assert backlink.stat().st_ino != previous_inode

    monkeypatch.setattr(fcntl, "flock", replace_after_lock)
    assert _release_cli("feature/retired", owner, head, registry_path) == 2
    assert changed and registry_path.read_bytes() == before
    assert registry.active_branches() == {"feature/retired"}
    assert _retention_git(owner, "rev-parse", "HEAD").strip() == head and root.is_dir()


@pytest.mark.parametrize("head_state", ("released", "new-current"))
def test_released_workstream_rearm_keeps_legitimate_native_current_owner(
    checkout: tuple[Path, Path, Path], head_state: str,
) -> None:
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    released_head = _retention_git(owner, "rev-parse", "HEAD").strip()
    assert registry.unregister("feature/retired", owner, released_head)["status"] == "released"
    if head_state == "new-current":
        _retention_git(owner, "commit", "--allow-empty", "-m", "new legitimate native owner")
    refs = _retention_git(root, "show-ref")
    registry.register("feature/retired", owner)
    row = json.loads(registry_path.read_text(encoding="utf-8"))["workstreams"]["feature/retired"]
    assert row["status"] == "active" and row["worktree"] == str(owner) and "head" not in row
    assert registry.active_branches() == {"feature/retired"}
    assert owner.is_dir() and _retention_git(root, "show-ref") == refs


def test_released_workstream_rearm_refuses_actual_removed_materialization(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, owner, registry_path = checkout
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/retired", owner)
    head = _retention_git(owner, "rev-parse", "HEAD").strip()
    assert registry.unregister("feature/retired", owner, head)["status"] == "released"
    released = registry_path.read_bytes()
    refs = _retention_git(root, "show-ref")
    assert prune_worktrees_safe.retire_merged(
        branch="feature/retired", worktree=owner, registry_path=registry_path,
        validate_only=False, expected_head=head,
    ) == 0
    assert not owner.exists() and _retention_git(root, "show-ref") == refs
    with pytest.raises((OSError, ValueError, subprocess.SubprocessError)):
        registry.register("feature/retired", owner)
    assert registry_path.read_bytes() == released and registry.active_branches() == frozenset()


# Retention uses the native lock already honored by both cleanup owners.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _retention_git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        timeout=30,
    ).stdout


@pytest.fixture
def retention_checkout(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    root.mkdir()
    _retention_git(root, "init", "-b", "development")
    _retention_git(root, "config", "user.name", "Retention fixture")
    _retention_git(root, "config", "user.email", "retention@example.invalid")
    (root / "owned.txt").write_bytes(b"owned source\n")
    (root / ".gitignore").write_text(".venv/\n.pytest_cache/\n")
    _retention_git(root, "add", "owned.txt", ".gitignore")
    _retention_git(root, "commit", "-m", "retention fixture")
    owner = tmp_path / "gludd-worktrees" / "retained"
    _retention_git(root, "worktree", "add", "-b", "feature/retained", str(owner))
    return root, owner.resolve()


def _retain_make(
    root: Path, owner: Path | str, *, branch: str = "feature/retained",
    reason: str = "preserve owned checkpoint", validate: str | None = "0",
    make_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    args = [
        "make", "--no-print-directory", "-f",
        str(PROJECT_ROOT / "make" / "40-cross-version-and-worktrees.mk"),
        "agent-worktree-retain", f"SYSTEM_PYTHON={sys.executable}",
        f"PYTHONPATH={environment['PYTHONPATH']}",
        f"BRANCH={branch}", f"WORKTREE={owner}",
        f"WORKTREE_RETAIN_REASON={reason}", *make_args,
    ]
    if validate is not None:
        args.append(f"WORKTREE_RETAIN_VALIDATE_ONLY={validate}")
    return subprocess.run(
        args, cwd=root, env=environment, capture_output=True, text=True,
        check=False, timeout=30,
    )


def test_retention_make_default_plan_is_readonly_and_unverified(
    retention_checkout: tuple[Path, Path],
) -> None:
    root, owner = retention_checkout
    absent = owner.parent / "absent-owner"
    before = _retention_git(root, "show-ref")
    result = _retain_make(root, absent, validate=None)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["validate_only"] is True
    assert receipt["native_ownership_verified"] == 0
    assert receipt["worktree"] == str(absent)
    assert receipt["reason"] == "preserve owned checkpoint"
    assert not absent.exists()
    assert _retention_git(root, "show-ref") == before


def test_retention_make_apply_uses_native_lock_and_literal_reason(
    retention_checkout: tuple[Path, Path],
) -> None:
    root, owner = retention_checkout
    sentry = root / "must-not-execute"
    reason = f"checkpoint owner'; touch {sentry}; #"
    before = _retention_git(root, "show-ref")
    result = _retain_make(root, owner, reason=reason)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["native_ownership_verified"] == 1
    assert receipt["locked"] is True
    assert receipt["already_locked"] is False
    admin = Path(_retention_git(owner, "rev-parse", "--absolute-git-dir").strip())
    assert (admin / "locked").read_text() == reason + "\n"
    assert _retention_git(root, "show-ref") == before
    assert (owner / "owned.txt").read_bytes() == b"owned source\n"
    assert not sentry.exists()


@pytest.mark.parametrize("field", ("BRANCH", "WORKTREE", "WORKTREE_RETAIN_REASON",
                                    "WORKTREE_RETAIN_VALIDATE_ONLY"))
def test_retention_make_never_expands_hostile_literals(
    retention_checkout: tuple[Path, Path], field: str,
) -> None:
    root, owner = retention_checkout
    sentry = root / "make-tripwire"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    args = [
        "make", "--no-print-directory", "-f",
        str(PROJECT_ROOT / "make" / "40-cross-version-and-worktrees.mk"),
        "agent-worktree-retain", f"SYSTEM_PYTHON={sys.executable}",
        "BRANCH=feature/retained",
        f"WORKTREE={owner.parent / 'absent'}", "WORKTREE_RETAIN_REASON=fixture",
        "WORKTREE_RETAIN_VALIDATE_ONLY=1", f"{field}=$(shell touch {sentry})",
    ]
    result = subprocess.run(
        args, cwd=root, env=environment, capture_output=True, text=True,
        check=False, timeout=30,
    )
    assert result.returncode != 0
    assert not sentry.exists(), result.stdout + result.stderr
    assert not (owner.parent / "absent").exists()


def test_retention_plan_starts_no_native_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("a retention plan must start no native child")
    monkeypatch.setattr(subprocess, "run", forbidden)
    absent = tmp_path / "not-created"
    assert prune_worktrees_safe.main([
        "--retain", "--branch", "feature/plan", "--worktree", str(absent),
        "--reason", "explicit plan",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["native_ownership_verified"] == 0
    assert not absent.exists()


@pytest.mark.parametrize("case", ("wrong-branch", "foreign", "unregistered", "symlink", "main"))
def test_retention_make_refuses_unowned_or_noncanonical_checkout(
    retention_checkout: tuple[Path, Path], case: str,
) -> None:
    root, owner = retention_checkout
    requested = owner
    branch = "feature/retained"
    if case == "wrong-branch":
        branch = "feature/other"
    elif case == "foreign":
        requested = root.parent / "foreign"
        requested.mkdir()
        _retention_git(requested, "init", "-b", branch)
    elif case == "unregistered":
        requested = root.parent / "unregistered"
        requested.mkdir()
    elif case == "symlink":
        requested = owner.parent / "linked"
        requested.symlink_to(owner, target_is_directory=True)
    else:
        requested = root
        branch = "development"
    before = _retention_git(root, "worktree", "list", "--porcelain", "-z")
    result = _retain_make(root, requested, branch=branch)
    assert result.returncode != 0
    assert "refused" in result.stdout + result.stderr
    assert _retention_git(root, "worktree", "list", "--porcelain", "-z") == before


def test_retention_make_preserves_existing_native_lock_reason(
    retention_checkout: tuple[Path, Path],
) -> None:
    root, owner = retention_checkout
    _retention_git(root, "worktree", "lock", "--reason", "original reason", str(owner))
    admin = Path(_retention_git(owner, "rev-parse", "--absolute-git-dir").strip())
    before = (admin / "locked").stat()
    result = _retain_make(root, owner, reason="must not replace")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["already_locked"] is True
    assert (admin / "locked").read_text() == "original reason\n"
    after = (admin / "locked").stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize("validate", ("", "2", "1 extra", "0; exit 0"))
def test_retention_make_refuses_invalid_apply_policy(
    retention_checkout: tuple[Path, Path], validate: str,
) -> None:
    root, owner = retention_checkout
    before = _retention_git(root, "worktree", "list", "--porcelain", "-z")
    result = _retain_make(root, owner, validate=validate)
    assert result.returncode != 0
    assert _retention_git(root, "worktree", "list", "--porcelain", "-z") == before


@pytest.mark.parametrize("failure", ("native-error", "native-timeout", "unverified", "inventory"))
def test_retention_native_failures_never_report_success(
    retention_checkout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    root, owner = retention_checkout
    monkeypatch.chdir(root)
    native = prune_worktrees_safe._git

    def failing_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if failure == "inventory" and args == ("worktree", "list", "--porcelain", "-z"):
            return subprocess.CompletedProcess(args, 0, "malformed", "")
        if args[:2] == ("worktree", "lock"):
            if failure == "native-error":
                raise subprocess.CalledProcessError(1, args, stderr="native fixture refusal")
            if failure == "native-timeout":
                raise subprocess.TimeoutExpired(args, 30)
            assert failure == "unverified"
            return subprocess.CompletedProcess(args, 0, "", "")
        return native(*args, check=check, timeout=timeout)

    monkeypatch.setattr(prune_worktrees_safe, "_git", failing_git)
    assert prune_worktrees_safe.main([
        "--retain", "--branch", "feature/retained", "--worktree", str(owner),
        "--reason", "native failure control", "--retain-validate-only", "0",
    ]) == 2
    assert "refused" in capsys.readouterr().out
    assert "locked" not in _retention_git(root, "worktree", "list", "--porcelain")


def test_retention_rechecks_replaced_native_backlink_before_lock(
    retention_checkout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, owner = retention_checkout
    monkeypatch.chdir(root)
    native = prune_worktrees_safe._git
    reads = 0
    locks: list[tuple[str, ...]] = []

    def replacing_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal reads
        if args == ("worktree", "list", "--porcelain", "-z"):
            reads += 1
            if reads == 2:
                replacement = owner / "replacement"
                replacement.write_bytes((owner / ".git").read_bytes())
                replacement.replace(owner / ".git")
        if args[:2] == ("worktree", "lock"):
            locks.append(args)
        return native(*args, check=check, timeout=timeout)

    monkeypatch.setattr(prune_worktrees_safe, "_git", replacing_git)
    with pytest.raises(ValueError, match="identity or registration changed"):
        prune_worktrees_safe.retain(
            branch="feature/retained", worktree=owner,
            reason="replaced backlink", validate_only=False,
        )
    assert locks == []
    assert reads == 2
    assert "locked" not in _retention_git(root, "worktree", "list", "--porcelain")


@pytest.mark.parametrize("missing", ("branch", "worktree", "reason", "relative", "newline"))
def test_retention_invalid_plan_inputs_start_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("invalid input must be refused before native Git")
    monkeypatch.setattr(subprocess, "run", forbidden)
    args = [
        "--retain", "--branch", "" if missing == "branch" else "feature/retained",
        "--reason", "" if missing == "reason" else "bad\nreason" if missing == "newline" else "fixture",
    ]
    if missing != "worktree":
        args.extend(("--worktree", "relative" if missing == "relative" else str(tmp_path / "absent")))
    assert prune_worktrees_safe.main(args) == 2


def test_retention_make_supports_exact_literal_worktree_path(
    retention_checkout: tuple[Path, Path],
) -> None:
    root, _owner = retention_checkout
    owner = root.parent / "gludd-worktrees" / "owner ' literal"
    _retention_git(root, "worktree", "add", "-b", "feature/literal", str(owner))
    result = _retain_make(root, owner, branch="feature/literal", reason="literal path")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["worktree"] == str(owner)
    assert any(record.path == owner and record.locked for record in parse_worktrees(
        _retention_git(root, "worktree", "list", "--porcelain")
    ))


def test_retention_make_plan_needs_no_uv_or_project_environment(
    retention_checkout: tuple[Path, Path],
) -> None:
    root, owner = retention_checkout
    environment = owner.parent / "must-not-create-venv"
    before = _retention_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    result = _retain_make(
        root, owner.parent / "absent", validate="1",
        make_args=("UV=/must/not/execute/uv", f"UV_PROJECT_ENVIRONMENT={environment}"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["native_ownership_verified"] == 0
    assert not environment.exists()
    assert not (root / "__pycache__").exists()
    assert _retention_git(root, "status", "--porcelain=v1", "--untracked-files=all") == before
