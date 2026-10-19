"""Confined native-Git retirement, evidence and checkpoint ownership controls."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

if TYPE_CHECKING:
    import automatic_disk_cleanup
    import clean_ci_shard_scratch
    import owned_git_checkpoint
    import prune_worktrees_safe
    import start_gate_background
    from workstream_registry import WorkstreamRegistry
else:
    from scripts import (
        automatic_disk_cleanup,
        clean_ci_shard_scratch,
        owned_git_checkpoint,
        prune_worktrees_safe,
        start_gate_background,
    )
    from scripts.workstream_registry import WorkstreamRegistry

BRANCH = "feature/retired"


def git(root: Path, *args: str) -> str:
    """Run native Git only inside the fixture repository."""
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=30,
    ).stdout


@pytest.fixture
def checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    root = (tmp_path / "repository").resolve()
    root.mkdir()
    git(root, "init", "-b", "development")
    git(root, "config", "user.name", "Retirement fixture")
    git(root, "config", "user.email", "retirement@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(
        "/.claude/\n/.gate-logs/\n/.gludd/\n/.venv/\n*.secret\n", encoding="utf-8",
    )
    (root / "owned.txt").write_text("base\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "retirement-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    git(root, "add", ".gitignore", "owned.txt", "pyproject.toml")
    git(root, "commit", "-m", "fixture base")
    target = root / ".claude" / "worktrees" / "retired"
    git(root, "worktree", "add", "-b", BRANCH, str(target))
    (target / "owned.txt").write_text("merged repair\n", encoding="utf-8")
    git(target, "commit", "-am", "real repair")
    git(root, "merge", "--ff-only", BRANCH)
    (target / ".gate-logs").mkdir()
    (target / ".gate-logs" / "failed-proof.log").write_bytes(b"FAILED original proof\n")
    (target / ".gludd").mkdir()
    (target / ".gludd" / "historical.json").write_bytes(b'{"state":"failed"}\n')
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        clean_ci_shard_scratch, "_active_process_pids", lambda _path: [],
    )
    return root, target, tmp_path / "workstreams.json"


def invoke(target: Path, registry: Path, *, plan: bool = False, branch: str = BRANCH) -> int:
    """Use the production CLI, preserving its refusal exit behavior."""
    try:
        return prune_worktrees_safe.main([
            "--retire-merged", "--branch", branch, "--worktree", str(target),
            "--registry", str(registry), "--retire-validate-only", "1" if plan else "0",
        ])
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


def archive(root: Path) -> Path:
    return root / ".git" / "gludd-release-evidence"


def assert_preserved(root: Path, target: Path, before_refs: str) -> None:
    assert target.is_dir()
    assert (target / ".gate-logs" / "failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (target / ".gludd" / "historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert git(root, "show-ref") == before_refs


def test_retirement_default_plan_verifies_without_writing_or_unregistering(
    checkout: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    assert invoke(target, registry, plan=True) == 0
    output = capsys.readouterr()
    receipt = json.loads(output.out.splitlines()[-1])
    assert receipt["validate_only"] is True
    assert receipt["native_ownership_verified"] == 1
    assert receipt["status"] == "planned"
    assert not archive(root).exists()
    assert not registry.exists()
    assert_preserved(root, target, before_refs)


def test_retirement_archives_ignored_proof_and_preserves_exact_branch_and_commit(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    head = git(target, "rev-parse", "HEAD").strip()
    assert invoke(target, registry) == 0
    assert not target.exists()
    manifests = list(archive(root).glob("*/manifest.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload == {
        "branch": BRANCH, "head": head, "original_worktree": str(target),
        "preserved": [".gate-logs", ".gludd"],
        "rehydrate": {"branch": BRANCH, "path": str(target)},
    }
    assert (manifests[0].parent / ".gate-logs" / "failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (manifests[0].parent / ".gludd" / "historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert git(root, "show-ref") == before_refs
    assert git(root, "show", f"{head}:owned.txt") == "merged repair\n"
    assert not registry.exists()


@pytest.mark.parametrize("reason", ["active", "alias", "lock", "unmerged", "dirty", "untracked", "unknown"])
def test_retirement_refuses_unfinished_locked_or_unknown_data(
    checkout: tuple[Path, Path, Path], reason: str,
) -> None:
    root, target, registry = checkout
    if reason in {"active", "alias"}:
        WorkstreamRegistry(registry).register(BRANCH if reason == "active" else "feature/alias", target)
    elif reason == "lock":
        git(root, "worktree", "lock", "--reason", "retained proof owner", str(target))
    elif reason == "unmerged":
        (target / "owned.txt").write_text("not integrated\n", encoding="utf-8")
        git(target, "commit", "-am", "unfinished work")
    elif reason == "dirty":
        (target / "owned.txt").write_text("uncommitted\n", encoding="utf-8")
    elif reason == "untracked":
        (target / "untracked.txt").write_text("unknown work\n", encoding="utf-8")
    else:
        (target / "private.secret").write_text("unknown ignored data\n", encoding="utf-8")
    before_refs = git(root, "show-ref")
    before_registry = registry.read_bytes() if registry.exists() else None
    assert invoke(target, registry) == 2
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()
    assert (registry.read_bytes() if registry.exists() else None) == before_registry


@pytest.mark.parametrize("kind", ["wrong-branch", "missing", "symlink", "foreign", "main"])
def test_retirement_requires_exact_native_registered_linked_owner(
    checkout: tuple[Path, Path, Path], tmp_path: Path, kind: str,
) -> None:
    root, target, registry = checkout
    candidate = target
    branch = BRANCH
    if kind == "wrong-branch":
        branch = "feature/not-the-owner"
    elif kind == "missing":
        candidate = tmp_path / "missing"
    elif kind == "symlink":
        candidate = tmp_path / "alias"
        candidate.symlink_to(target, target_is_directory=True)
    elif kind == "foreign":
        candidate = tmp_path / "foreign"
        candidate.mkdir()
        git(candidate, "init", "-b", BRANCH)
    else:
        candidate, branch = root, "development"
    before_refs = git(root, "show-ref")
    assert invoke(candidate, registry, branch=branch) == 2
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_retirement_validated_checkpoint_query_is_fresh_and_blocks_its_owner(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    assert owned_git_checkpoint.retained_checkpoint_owners(root) == frozenset()
    (target / "owned.txt").write_text("checkpoint bytes\n", encoding="utf-8")
    owned_git_checkpoint.push(target, ["owned.txt"], "retirement-owned-checkpoint", validate=False)
    stash = git(root, "rev-parse", "refs/stash")
    assert owned_git_checkpoint.retained_checkpoint_owners(root) == frozenset({target})
    assert owned_git_checkpoint.count_leaks(root) == 0
    before_refs = git(root, "show-ref")
    assert invoke(target, registry) == 2
    assert_preserved(root, target, before_refs)
    assert git(root, "rev-parse", "refs/stash") == stash
    assert not archive(root).exists()


def test_retirement_preserves_other_registered_checkpoint_owners(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    peer = target.parent / "peer"
    git(root, "worktree", "add", "-b", "feature/peer", str(peer))
    (peer / "owned.txt").write_text("peer checkpoint\n", encoding="utf-8")
    owned_git_checkpoint.push(peer, ["owned.txt"], "retirement-peer-checkpoint", validate=False)
    stash = git(root, "rev-parse", "refs/stash")
    assert invoke(target, registry) == 0
    assert not target.exists()
    assert peer.is_dir()
    assert git(root, "rev-parse", "refs/stash") == stash
    assert owned_git_checkpoint.retained_checkpoint_owners(root) == frozenset({peer})


def test_retirement_refuses_unowned_stash_without_changing_it(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    (root / "owned.txt").write_text("unowned checkpoint\n", encoding="utf-8")
    git(root, "stash", "push", "-m", "unknown owner")
    before_refs = git(root, "show-ref")
    assert invoke(target, registry) == 2
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


@pytest.mark.parametrize("inspection", ["live", "error"])
def test_retirement_fails_closed_for_process_ownership(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, inspection: str,
) -> None:
    root, target, registry = checkout

    def failed_inspection(_path: Path) -> NoReturn:
        raise automatic_disk_cleanup.ProcessInspectionError("fixture inspection refused")

    callback = failed_inspection if inspection == "error" else lambda _path: [4242]
    monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", callback)
    before_refs = git(root, "show-ref")
    assert invoke(target, registry) == 2
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_retirement_native_remove_refusal_rolls_back_every_archived_byte(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, target, registry = checkout
    native = prune_worktrees_safe._git
    remove_calls: list[tuple[str, ...]] = []

    def refusing_git(
        *args: str, check: bool = True, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "remove"):
            remove_calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "fixture native refusal")
        return native(*args, check=check, timeout=timeout)

    monkeypatch.setattr(prune_worktrees_safe, "_git", refusing_git)
    before_refs = git(root, "show-ref")
    assert invoke(target, registry) == 2
    assert len(remove_calls) == 1
    assert_preserved(root, target, before_refs)
    assert not list(archive(root).glob("*/manifest.json"))


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_facade(
    root: Path, target: Path, registry: Path, extra_makefiles: tuple[Path, ...] = (),
    inherited_makefiles: Path | None = None, /, **overrides: str,
) -> subprocess.CompletedProcess[str]:
    """Exercise the real public Make fragment and selected installed runtime."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((
        str(PROJECT_ROOT), str(PROJECT_ROOT / "src"), environment.get("PYTHONPATH", ""),
    ))
    if inherited_makefiles is not None:
        environment["MAKEFILES"] = str(inherited_makefiles)
    values = {
        "UV": os.environ.get("UV", "uv"),
        "UV_PROJECT_ENVIRONMENT": sys.prefix, "UV_PYTHON": sys.executable,
        "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONPATH": environment["PYTHONPATH"],
        "WT_PRUNE_RETIRE_MERGED": "1", "WT_PRUNE_RETIRE_VALIDATE_ONLY": "1",
        "WT_PRUNE_VALIDATE_ONLY": "1", "BRANCH": BRANCH, "WORKTREE": str(target),
        "ACTIVE_WORKSTREAM_REGISTRY": str(registry),
    }
    values.update(overrides)
    command = ["make", "--no-print-directory"]
    for makefile in extra_makefiles:
        command.extend(("-f", str(makefile)))
    command.extend(("-f", str(PROJECT_ROOT / "make/30-ci-and-release.mk"), "wt-prune-safe"))
    return subprocess.run(
        [*command, *(f"{key}={value}" for key, value in values.items())],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=60,
    )


def test_full_make_retirement_preserves_literal_native_branch_and_path(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, original, registry = checkout
    target = original.with_name("retired space'`literal`")
    branch = "feature/retired'`literal`"
    git(root, "worktree", "move", str(original), str(target))
    git(target, "branch", "-m", BRANCH, branch)
    before_refs = git(root, "show-ref")
    result = make_facade(root, target, registry, BRANCH=branch)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout.splitlines()[-1])
    assert receipt["worktree"] == str(target)
    assert receipt["branch"] == branch
    assert receipt["status"] == "planned"
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


@pytest.mark.parametrize("field", [
    "BRANCH", "WORKTREE", "ACTIVE_WORKSTREAM_REGISTRY",
    "WT_PRUNE_RETIRE_VALIDATE_ONLY", "WT_PRUNE_RETIRE_MERGED",
])
def test_full_make_retirement_refuses_make_expression_before_side_effect(
    checkout: tuple[Path, Path, Path], field: str,
) -> None:
    root, target, registry = checkout
    sentry = root / "must-not-execute"
    before_refs = git(root, "show-ref")
    result = make_facade(root, target, registry, **{field: f"$(shell touch {sentry})"})
    assert result.returncode != 0
    assert not sentry.exists(), result.stdout + result.stderr
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


@pytest.mark.parametrize("mode", ["", "2", "1 0"])
def test_full_make_invalid_mode_never_falls_back_to_broad_pruning(
    checkout: tuple[Path, Path, Path], mode: str,
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    result = make_facade(root, target, registry, WT_PRUNE_RETIRE_MERGED=mode)
    assert result.returncode != 0
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_default_plan_preserves_original_prune_output_and_behavior(
    checkout: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    assert prune_worktrees_safe.prune(registry_path=registry, validate_only=True) == 0
    expected = capsys.readouterr().out
    result = make_facade(root, target, registry, WT_PRUNE_RETIRE_MERGED="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == expected
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_absent_native_owner_plan_refuses_without_mutation(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    absent = target.parent / "absent"
    before_refs = git(root, "show-ref")
    result = make_facade(root, absent, registry)
    assert result.returncode != 0
    assert "canonical registered linked checkout" in result.stdout
    assert not absent.exists()
    assert not registry.exists()
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_foreign_then_approved_makefiles_refuse_before_archival(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    foreign = root / "foreign.mk"
    foreign.write_text("# unrelated source is never retirement authority\n", encoding="utf-8")
    before_refs = git(root, "show-ref")
    result = make_facade(
        root, target, registry, (foreign,), WT_PRUNE_RETIRE_VALIDATE_ONLY="0",
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_inherited_makefiles_refuse_before_archival(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    foreign = root / "foreign.mk"
    marker = "retirement foreign MAKEFILES source loaded"
    foreign.write_text(f"$(info {marker})\n", encoding="utf-8")
    before_refs = git(root, "show-ref")
    result = make_facade(
        root, target, registry, (), foreign, WT_PRUNE_RETIRE_VALIDATE_ONLY="0",
    )
    assert marker in result.stdout
    assert result.returncode != 0, result.stdout + result.stderr
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def controller_fixture(
    checkout: tuple[Path, Path, Path],
) -> tuple[prune_worktrees_safe._RetirementController, ...]:
    root, target, registry = checkout
    selected_uv = shutil.which(os.environ.get("UV", "uv"))
    selected_make = str(start_gate_background._native_make_executable())
    assert selected_uv
    python_argv = (
        sys.executable, "-m", "scripts.prune_worktrees_safe", "--retire-merged",
        "--branch", BRANCH, "--worktree", str(target), "--registry", str(registry),
        "--retire-validate-only", "1",
    )
    identity = prune_worktrees_safe._RetirementController
    uid = (os.getuid(), os.geteuid())
    return (
        identity(101, 102, 1.0, root, Path(sys.executable).resolve(), python_argv, uid),
        identity(102, 103, 2.0, root, Path(selected_uv).resolve(),
                 (selected_uv, "run", "python", *python_argv[1:]), uid),
        identity(103, 1, 3.0, root, Path(selected_make).resolve(), (
            selected_make, "--no-print-directory", "-f", str(PROJECT_ROOT / "make/30-ci-and-release.mk"),
            "wt-prune-safe", "WT_PRUNE_RETIRE_MERGED=1", f"BRANCH={BRANCH}", f"WORKTREE={target}",
        ), uid),
    )


@pytest.mark.parametrize("fault", [
    "none", "start", "target-cwd", "unknown-cwd", "uid", "command", "request",
    "executable", "ancestry", "make-source", "make-target", "duplicate-make-source", "makefiles",
])
def test_controller_exemption_requires_exact_argv_cwd_start_identity_and_ancestry(
    checkout: tuple[Path, Path, Path], fault: str,
) -> None:
    root, target, _registry = checkout
    records = list(controller_fixture(checkout))
    if fault == "start":
        records[1] = replace(records[1], started=0)
    elif fault == "target-cwd":
        records[1] = replace(records[1], cwd=target)
    elif fault == "unknown-cwd":
        records[1] = replace(records[1], cwd=root / "unknown")
    elif fault == "uid":
        records[1] = replace(records[1], uid=(-1, -1))
    elif fault == "command":
        records[1] = replace(records[1], argv=(str(records[1].executable), "unrelated"))
    elif fault == "request":
        records[0] = replace(records[0], argv=(sys.executable, "-m", "unrelated.owner"))
    elif fault == "executable":
        records[1] = replace(records[1], executable=root / "unknown-uv")
    elif fault == "ancestry":
        records[0] = replace(records[0], ppid=999)
    elif fault == "make-source":
        records[2] = replace(records[2], argv=(
            str(records[2].executable), "-f", str(root / "unknown.mk"), "wt-prune-safe",
        ))
    elif fault == "duplicate-make-source":
        foreign = root / "foreign.mk"
        foreign.write_text("# unrelated Make source\n", encoding="utf-8")
        records[2] = replace(records[2], argv=(
            str(records[2].executable), "-f", str(foreign), *records[2].argv[1:],
        ))
    elif fault == "makefiles":
        records[2] = replace(records[2], argv=(*records[2].argv, f"MAKEFILES={root / 'foreign.mk'}"))
    elif fault == "make-target":
        records[2] = replace(records[2], argv=(str(records[2].executable), "unrelated"))
    if fault == "none":
        assert prune_worktrees_safe._validate_retirement_controllers(
            tuple(records), root=root, worktree=target, branch=BRANCH,
        ) == tuple(records)
    else:
        with pytest.raises((ValueError, OSError)):
            prune_worktrees_safe._validate_retirement_controllers(
                tuple(records), root=root, worktree=target, branch=BRANCH,
            )


@pytest.mark.parametrize("change", ["started", "ppid"])
def test_retirement_rechecks_controller_identity_before_any_archive(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    root, target, registry = checkout
    records = controller_fixture(checkout)
    calls = 0

    def controller_chain(
        *, root: Path, worktree: Path, branch: str,
    ) -> tuple[prune_worktrees_safe._RetirementController, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return records
        changed = replace(records[1], started=999) if change == "started" else replace(records[1], ppid=999)
        return (records[0], changed, records[2])

    monkeypatch.setattr(prune_worktrees_safe, "_retirement_controllers", controller_chain)
    monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", lambda _path: [102, 103])
    before_refs = git(root, "show-ref")
    assert invoke(target, registry) == 2
    assert calls == 2
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_retirement_never_exempts_an_unrelated_live_target_child(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()", str(target)],
        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == "ready\n"
        assert child.poll() is None
        before_refs = git(root, "show-ref")
        result = make_facade(root, target, registry)
        assert result.returncode != 0
        assert str(child.pid) in result.stdout
        assert child.poll() is None
        assert_preserved(root, target, before_refs)
        assert not archive(root).exists()
    finally:
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)


def test_full_make_retirement_target_cwd_ancestor_is_refused(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    result = make_facade(target, target, registry)
    assert result.returncode != 0
    assert "current/main checkout" in result.stdout
    assert_preserved(root, target, before_refs)
    assert not archive(root).exists()


def test_full_make_retirement_apply_uses_native_archive_and_ref_preservation(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    result = make_facade(root, target, registry, WT_PRUNE_RETIRE_VALIDATE_ONLY="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists()
    assert git(root, "show-ref") == before_refs
    manifests = list(archive(root).glob("*/manifest.json"))
    assert len(manifests) == 1
    assert (manifests[0].parent / ".gate-logs" / "failed-proof.log").read_bytes() == b"FAILED original proof\n"


@pytest.mark.parametrize("change", ["active", "lock", "live", "head", "checkpoint", "ignored"])
def test_retirement_rechecks_authority_after_archival_and_rolls_back(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    root, target, registry = checkout
    native_replace = os.replace
    changed = False

    def replacing(source: str | Path, destination: str | Path) -> None:
        nonlocal changed
        native_replace(source, destination)
        if Path(source) != target / ".gate-logs" or changed:
            return
        changed = True
        if change == "active":
            WorkstreamRegistry(registry).register(BRANCH, target)
        elif change == "lock":
            git(root, "worktree", "lock", "--reason", "became retained", str(target))
        elif change == "live":
            monkeypatch.setattr(
                clean_ci_shard_scratch, "_active_process_pids", lambda _path: [4242],
            )
        elif change == "head":
            (target / "owned.txt").write_text("new work\n", encoding="utf-8")
            git(target, "commit", "-am", "new work after archival")
        elif change == "checkpoint":
            (target / "owned.txt").write_text("new checkpoint\n", encoding="utf-8")
            owned_git_checkpoint.push(target, ["owned.txt"], "retirement-race-checkpoint", validate=False)
        else:
            (target / "new.secret").write_bytes(b"do not delete")
    monkeypatch.setattr(os, "replace", replacing)
    assert invoke(target, registry) == 2
    assert changed
    assert target.is_dir()
    assert (target / ".gate-logs" / "failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (target / ".gludd" / "historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert not list(archive(root).glob("*/manifest.json"))
