"""Preserve native ownership and ignored proof during ordinary clean pruning."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.unit.test_worktree_retirement import archive, controller_fixture, git

if TYPE_CHECKING:
    import clean_ci_shard_scratch
    import owned_git_checkpoint
    import prune_worktrees_safe
else:
    from scripts import clean_ci_shard_scratch, owned_git_checkpoint, prune_worktrees_safe

pytest_plugins = ("tests.unit.test_worktree_retirement",)
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("environment_overrides", [False, True])
def test_cleanup_alias_applies_the_real_registered_native_retirement(
    checkout: tuple[Path, Path, Path], environment_overrides: bool,
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    environment.update({
        "PYTHONPATH": str(ROOT), "UV_PROJECT_ENVIRONMENT": sys.prefix,
        "UV_PYTHON": sys.executable, "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
    })
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(ROOT / "make/40-cross-version-and-worktrees.mk"), "agent-cleanup",
            "BRANCH=feature/retired", f"WORKTREE={target}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
            "AGENT_CLEANUP_VALIDATE_ONLY=0", "WT_PRUNE_RETIRE_MERGED=0", "UV=uv",
            f"MAKE=make --no-print-directory -f {shlex.quote(str(ROOT / 'make/30-ci-and-release.mk'))}",
        ],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists() and git(root, "show-ref") == before_refs
    assert [path.read_bytes() for path in archive(root).rglob("failed-proof.log")] == [b"FAILED original proof\n"]



@pytest.mark.parametrize("environment_flag", ["-e", "--environment-overrides"])
@pytest.mark.parametrize("fault", ["none", "plan", "source", "target", "path", "cwd", "start", "extra-parent"])
def test_alias_controller_requires_exact_same_native_ownership(
    checkout: tuple[Path, Path, Path], environment_flag: str, fault: str,
) -> None:
    root, target, _registry = checkout
    records = list(controller_fixture(checkout))
    records[0] = replace(records[0], argv=(*records[0].argv[:-1], "0"))
    records[1] = replace(records[1], argv=(*records[1].argv[:3], *records[0].argv[1:]))
    selected_make = records[2]
    argv = [
        selected_make.argv[0], environment_flag, "-f", str(ROOT / "make/40-cross-version-and-worktrees.mk"),
        "agent-cleanup", "AGENT_CLEANUP_VALIDATE_ONLY=0", "BRANCH=feature/retired", f"WORKTREE={target}",
    ]
    if fault == "plan":
        argv[5] = "AGENT_CLEANUP_VALIDATE_ONLY=1"
    elif fault == "source":
        argv[3] = str(ROOT / "make/30-ci-and-release.mk")
    elif fault == "target":
        argv[4] = "help"
    elif fault == "path":
        argv[-1] = f"WORKTREE={root}"
    records[2] = replace(selected_make, argv=tuple(argv))
    if fault == "cwd":
        records[2] = replace(records[2], cwd=target)
    elif fault == "start":
        records[2] = replace(records[2], started=0)
    elif fault == "extra-parent":
        records[2] = replace(records[2], ppid=104)
        records.append(replace(records[2], pid=104, ppid=1))
    selected = tuple(records)
    if fault == "none":
        assert prune_worktrees_safe._validate_retirement_controllers(
            selected, root=root, worktree=target, branch="feature/retired",
        ) == selected
    else:
        with pytest.raises(ValueError):
            prune_worktrees_safe._validate_retirement_controllers(
                selected, root=root, worktree=target, branch="feature/retired",
            )


def test_ordinary_pruning_archives_proof_and_preserves_unmerged_branch(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, target, registry = checkout
    git(target, "commit", "--allow-empty", "-m", "unmerged retained source")
    before_refs = git(root, "show-ref")
    assert prune_worktrees_safe.prune(registry_path=registry, validate_only=False) == 0
    assert not target.exists()
    assert git(root, "show-ref") == before_refs
    assert [path.read_bytes() for path in archive(root).rglob("failed-proof.log")] == [b"FAILED original proof\n"]
    assert [path.read_bytes() for path in archive(root).rglob("historical.json")] == [b'{"state":"failed"}\n']


@pytest.mark.parametrize("protection", ["process", "checkpoint", "unknown-process", "unknown-data"])
def test_ordinary_pruning_cannot_bypass_native_retirement_protections(
    checkout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, protection: str,
) -> None:
    root, target, registry = checkout
    if protection == "process":
        monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", lambda _path: [12345])
    elif protection == "checkpoint":
        monkeypatch.setattr(owned_git_checkpoint, "retained_checkpoint_owners", lambda _root: frozenset({target}))
    elif protection == "unknown-process":
        def unavailable(_path: Path) -> list[int]:
            raise RuntimeError("native process inventory unavailable")
        monkeypatch.setattr(clean_ci_shard_scratch, "_active_process_pids", unavailable)
    else:
        (target / "operator.secret").write_bytes(b"preserve unknown ignored data")
    before_refs = git(root, "show-ref")
    assert prune_worktrees_safe.prune(registry_path=registry, validate_only=False) == 0
    assert target.is_dir()
    assert (target / ".gate-logs/failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (target / ".gludd/historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert git(root, "show-ref") == before_refs
    if protection == "unknown-data":
        assert (target / "operator.secret").read_bytes() == b"preserve unknown ignored data"


@pytest.mark.parametrize("environment_overrides", [False, True])
def test_cleanup_alias_plans_the_real_registered_native_retirement(
    checkout: tuple[Path, Path, Path], environment_overrides: bool,
) -> None:
    root, target, registry = checkout
    before_refs = git(root, "show-ref")
    head = git(target, "rev-parse", "HEAD").strip()
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    environment.update({
        "PYTHONPATH": str(ROOT), "UV_PROJECT_ENVIRONMENT": sys.prefix,
        "VIRTUAL_ENV": sys.prefix, "UV_PYTHON": sys.executable,
        "UV_NO_SYNC": "1", "UV_PYTHON_DOWNLOADS": "never",
    })
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(ROOT / "make/40-cross-version-and-worktrees.mk"), "agent-cleanup",
            "BRANCH=feature/retired", f"WORKTREE={target}", f"ACTIVE_WORKSTREAM_REGISTRY={registry}",
            "AGENT_CLEANUP_VALIDATE_ONLY=1", "UV=uv",
        ],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "branch": "feature/retired", "worktree": str(target), "head": head,
        "validate_only": True, "native_ownership_verified": 1, "status": "planned",
    }
    assert target.is_dir()
    assert (target / ".gate-logs/failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (target / ".gludd/historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert git(root, "show-ref") == before_refs
    assert str(target) in git(root, "worktree", "list", "--porcelain")
    assert not archive(root).exists()
    assert not registry.exists()


@pytest.mark.parametrize("environment_flag", ["-e", "--environment-overrides"])
@pytest.mark.parametrize("python_mode", ["0", "1"])
@pytest.mark.parametrize("make_mode", ["0", "1"])
def test_alias_controller_requires_matching_native_retirement_mode(
    checkout: tuple[Path, Path, Path], environment_flag: str, python_mode: str, make_mode: str,
) -> None:
    root, target, _registry = checkout
    records = list(controller_fixture(checkout))
    records[0] = replace(records[0], argv=(*records[0].argv[:-1], python_mode))
    records[1] = replace(records[1], argv=(*records[1].argv[:3], *records[0].argv[1:]))
    records[2] = replace(records[2], argv=(
        records[2].argv[0], environment_flag,
        "-f", str(ROOT / "make/40-cross-version-and-worktrees.mk"), "agent-cleanup",
        f"AGENT_CLEANUP_VALIDATE_ONLY={make_mode}", "BRANCH=feature/retired", f"WORKTREE={target}",
    ))
    selected = tuple(records)
    if python_mode == make_mode:
        assert prune_worktrees_safe._validate_retirement_controllers(
            selected, root=root, worktree=target, branch="feature/retired",
        ) == selected
    else:
        with pytest.raises(ValueError, match="does not bind selected owner"):
            prune_worktrees_safe._validate_retirement_controllers(
                selected, root=root, worktree=target, branch="feature/retired",
            )


@pytest.mark.parametrize("environment_overrides", [False, True])
def test_retired_force_alias_cannot_remove_registered_worktree_or_ignored_proof(
    checkout: tuple[Path, Path, Path], environment_overrides: bool,
) -> None:
    root, target, registry = checkout
    git(root, "branch", "master")
    before_refs = git(root, "show-ref")
    before_registration = git(root, "worktree", "list", "--porcelain")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    result = subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(ROOT / "make/30-ci-and-release.mk"), "wt-prune-force-merged",
        ],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=30,
    )
    assert target.is_dir(), result.stdout + result.stderr
    assert (target / ".gate-logs/failed-proof.log").read_bytes() == b"FAILED original proof\n"
    assert (target / ".gludd/historical.json").read_bytes() == b'{"state":"failed"}\n'
    assert git(root, "show-ref") == before_refs
    assert git(root, "worktree", "list", "--porcelain") == before_registration
    assert not archive(root).exists()
    assert not registry.exists()
    assert result.returncode != 0
    assert "No rule to make target" in result.stderr
