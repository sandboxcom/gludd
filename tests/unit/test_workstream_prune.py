"""Regression coverage for active logical workstream pruning."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts import prune_worktrees_safe, workstream_registry
from scripts.prune_worktrees_safe import WorktreeRecord, parse_worktrees, pruning_decision
from scripts.workstream_registry import WorkstreamRegistry


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


def test_registry_is_atomic_and_unregisters_explicitly(tmp_path: Path) -> None:
    registry_path = tmp_path / "active-workstreams.json"
    registry = WorkstreamRegistry(registry_path)
    registry.register("feature/one", tmp_path / "one")
    registry.register("feature/two", tmp_path / "two")

    assert registry.active_branches() == frozenset({"feature/one", "feature/two"})
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert not list(tmp_path.glob("*.tmp"))

    registry.unregister("feature/one")

    assert registry.active_branches() == frozenset({"feature/two"})


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


def test_registry_cli_register_list_and_unregister(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry_path = tmp_path / "cli.json"
    worktree = tmp_path / "worktree"

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
            ["unregister", "--branch", "feature/cli", "--registry", str(registry_path)]
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    active = tmp_path / "active"
    idle = tmp_path / "idle"
    porcelain = (
        f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n\n"
        f"worktree {active}\nHEAD def\nbranch refs/heads/feature/active\n\n"
        f"worktree {idle}\nHEAD fed\nbranch refs/heads/feature/idle\n"
    )
    registry_path = tmp_path / "registry.json"
    WorkstreamRegistry(registry_path).register("feature/active", active)
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ("worktree", "list", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, porcelain, "")
        if args == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess(args, 0, f"{main}\n", "")
        assert args == ("worktree", "remove", str(idle.resolve()))
        assert check is False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(prune_worktrees_safe, "_git", fake_git)

    assert prune_worktrees_safe.prune(registry_path=registry_path, validate_only=False) == 0
    assert ("worktree", "remove", str(idle.resolve())) in calls
    assert ("worktree", "remove", str(active.resolve())) not in calls


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


def test_dirty_candidate_is_retained(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    main = tmp_path / "main"
    dirty = tmp_path / "dirty"
    porcelain = (
        f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n\n"
        f"worktree {dirty}\nHEAD def\nbranch refs/heads/feature/dirty\n"
    )

    def fake_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args == ("worktree", "list", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, porcelain, "")
        if args == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess(args, 0, f"{main}\n", "")
        assert args == ("worktree", "remove", str(dirty.resolve()))
        assert check is False
        return subprocess.CompletedProcess(args, 1, "", "contains modified files")

    monkeypatch.setattr(prune_worktrees_safe, "_git", fake_git)

    assert prune_worktrees_safe.prune(registry_path=tmp_path / "missing.json", validate_only=False) == 0


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
