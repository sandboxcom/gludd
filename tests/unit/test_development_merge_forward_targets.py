"""Structural and native controls for branch-reconciliation Make targets.

These assertions hold the recipe contracts so a refactor cannot silently
drop the transactional guards on development merge-forward, the patch
equivalence validation, the development-only conflict resolver, or the
worktree merge fan-in path.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_worktree_retirement import git as native_git

pytest_plugins = ("tests.unit.test_worktree_retirement",)
ROOT = Path(__file__).resolve().parents[2]

_TARGET_RE = r"^(?P<name>%s):\n(?P<recipe>(?:\t.*(?:\n|$))+)"


def _makefile() -> str:
    return compose_makefile(ROOT / "Makefile")


def _recipe(target: str) -> str:
    makefile = _makefile()
    match = re.search(_TARGET_RE % re.escape(target), makefile, re.MULTILINE)
    assert match, f"{target} target is missing"
    return match.group("recipe")


def test_development_merge_forward_target_exists() -> None:
    assert re.search(r"^development-merge-forward:\n", _makefile(), re.MULTILINE), (
        "development-merge-forward target is missing"
    )


def test_development_merge_forward_recipe_has_merge_head_abort_trap() -> None:
    recipe = _recipe("development-merge-forward")

    assert "abort_merge" in recipe
    assert "trap abort_merge EXIT HUP INT TERM" in recipe
    assert "merge --abort" in recipe
    assert "MERGE_HEAD" in recipe


def test_development_merge_forward_recipe_runs_collect_check() -> None:
    recipe = _recipe("development-merge-forward")

    assert "collect-check" in recipe
    assert "Collection check failed; aborting transaction" in recipe


def test_development_merge_forward_recipe_selects_strategy_per_mode() -> None:
    recipe = _recipe("development-merge-forward")

    assert "-X ours" in recipe
    assert "merge --no-ff -s ours --no-commit" in recipe
    assert "MODE must be explicitly set to content or ancestry-only" in recipe


def test_development_merge_forward_apply_requires_development_branch() -> None:
    recipe = _recipe("development-merge-forward")

    assert '"$$CURRENT_BRANCH" != "development"' in recipe
    assert "requires current branch development" in recipe
    assert "requires a clean development worktree" in recipe


def test_git_patch_equivalence_exists_and_validates_variables() -> None:
    recipe = _recipe("git-patch-equivalence")

    for fragment in (
        '"$$PATCH_UPSTREAM"',
        '"$$PATCH_HEAD"',
        '"$$PATCH_LIMIT"',
        "PATCH_LIMIT must be a non-negative integer",
        "git cherry",
        "patch-equivalent=",
    ):
        assert fragment in recipe, f"missing {fragment!r} in git-patch-equivalence recipe"


def test_resolve_development_conflicts_exists_and_refuses_other_branches() -> None:
    recipe = _recipe("resolve-development-conflicts")

    assert '"$$MERGE_SOURCE"' in recipe
    assert '"$$BRANCH" = "development"' in recipe
    assert "Refusing conflict resolution on branch" in recipe
    assert "expected development" in recipe


def test_branches_unmerged_target_exists() -> None:
    assert re.search(r"^branches-unmerged:\n", _makefile(), re.MULTILINE), "branches-unmerged target is missing"


def test_agent_merge_dev_exists_and_uses_no_ff() -> None:
    recipe = _recipe("agent-merge-dev")

    assert "git checkout development" in recipe
    assert "git merge --no-ff" in recipe
    assert "into development" in recipe


def _ancestry_make(
    root: Path, first: str, second: str, environment_overrides: bool,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"}
    }
    return subprocess.run(
        [
            "make", "--no-print-directory", *(["-e"] if environment_overrides else []),
            "-f", str(ROOT / "make/20-recovery-and-git.mk"),
            "git-is-ancestor", f"A={first}", f"B={second}",
        ],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("commit_relationship", ["same", "ancestor", "not-ancestor", "invalid"])
def test_ancestry_target_propagates_native_git_status(
    checkout: tuple[Path, Path, Path], environment_overrides: bool, commit_relationship: str,
) -> None:
    root, _target, _registry = checkout
    older = native_git(root, "rev-parse", "HEAD^").strip()
    newer = native_git(root, "rev-parse", "HEAD").strip()
    first, second = {
        "same": (newer, newer), "ancestor": (older, newer),
        "not-ancestor": (newer, older), "invalid": ("refs/heads/absent-ancestry-fixture", newer),
    }[commit_relationship]
    native = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "--", first, second],
        cwd=root, check=False, capture_output=True, text=True, timeout=30,
    )
    before_refs = native_git(root, "show-ref")
    before_status = native_git(root, "status", "--porcelain=v1")
    result = _ancestry_make(root, first, second, environment_overrides)
    assert result.returncode == (0 if native.returncode == 0 else 2), result.stdout + result.stderr
    assert f"exit={native.returncode}\n" in result.stdout
    if native.returncode:
        assert f"Error {native.returncode}" in result.stderr
    assert native_git(root, "show-ref") == before_refs
    assert native_git(root, "status", "--porcelain=v1") == before_status


@pytest.mark.parametrize("environment_overrides", [False, True])
@pytest.mark.parametrize("git_ref_field", ["A", "B"])
@pytest.mark.parametrize(
    "git_ref_value",
    ["$(shell touch TRIPWIRE)", "`touch TRIPWIRE`", '"; touch TRIPWIRE; #', "--not-an-option"],
)
def test_ancestry_target_keeps_commit_arguments_literal(
    checkout: tuple[Path, Path, Path], environment_overrides: bool, git_ref_field: str, git_ref_value: str,
) -> None:
    root, _target, _registry = checkout
    first, second = (git_ref_value, "HEAD") if git_ref_field == "A" else ("HEAD", git_ref_value)
    before_refs = native_git(root, "show-ref")
    result = _ancestry_make(root, first, second, environment_overrides)
    assert not (root / "TRIPWIRE").exists(), result.stdout + result.stderr
    assert result.returncode != 0, result.stdout + result.stderr
    assert "exit=128\n" in result.stdout
    assert "Error 128" in result.stderr
    assert native_git(root, "show-ref") == before_refs


def _merge_forward_make(
    root: Path, *, source: str = "HEAD", mode: str = "content", apply: str = "0",
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for variable in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL"):
        environment.pop(variable, None)
    return subprocess.run(
        [
            "make", "--no-print-directory", "-f",
            str(ROOT / "make/50-development-and-guardrails.mk"),
            "development-merge-forward", f"SOURCE={source}", f"MODE={mode}", f"APPLY={apply}",
        ],
        cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=30,
    )


def _merge_forward_state(root: Path) -> tuple[str, str, str, bytes, bool]:
    return (
        native_git(root, "show-ref"), native_git(root, "status", "--porcelain=v1"),
        native_git(root, "diff", "--cached", "--binary"), (root / "owned.txt").read_bytes(),
        (root / ".git" / "MERGE_HEAD").exists(),
    )


@pytest.mark.parametrize("mode", ["content", "ancestry-only"])
@pytest.mark.parametrize("staged", [False, True])
def test_merge_forward_plan_guidance_does_not_admit_integration(
    checkout: tuple[Path, Path, Path], mode: str, staged: bool,
) -> None:
    root, _target, _registry = checkout
    native_git(root, "checkout", "-b", "feature/plan-guidance")
    if staged:
        (root / "owned.txt").write_bytes(b"preserve this staged plan input\n")
        native_git(root, "add", "owned.txt")
    before = _merge_forward_state(root)
    expected_sha = native_git(root, "rev-parse", "HEAD").strip()

    result = _merge_forward_make(root, mode=mode)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"MERGE_FORWARD_DRY_RUN source=HEAD mode={mode} apply=0 sha={expected_sha}" in result.stdout
    assert "no repository changes were made" in result.stdout
    assert "AGENT_NEXT target=development-merge-forward outcome=planned" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert "only SOURCE and mode" in result.stderr
    assert "release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0" in result.stderr
    assert "capacity" in result.stderr and "APPLY=1" in result.stderr
    assert "outcome=passed" not in result.stderr
    assert _merge_forward_state(root) == before


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        ("source-required", "Usage: make development-merge-forward"),
        ("mode", "MODE must be explicitly set"),
        ("apply", "APPLY must be 0 or 1"),
        ("missing-source", "Invalid SOURCE ref"),
        ("master", "ancestry-only mode is forbidden for master"),
        ("branch", "requires current branch development"),
        ("dirty", "requires a clean development worktree"),
    ],
)
def test_merge_forward_refusal_guidance_preserves_native_status_and_state(
    checkout: tuple[Path, Path, Path], refusal: str, reason: str,
) -> None:
    root, _target, _registry = checkout
    source, mode, apply = "HEAD", "content", "0"
    if refusal == "source-required":
        source = ""
    elif refusal == "mode":
        mode = "unsupported"
    elif refusal == "apply":
        apply = "2"
    elif refusal == "missing-source":
        source = "refs/heads/absent-forward-fixture"
    elif refusal == "master":
        native_git(root, "branch", "master")
        source, mode = "master", "ancestry-only"
    elif refusal == "branch":
        native_git(root, "checkout", "-b", "feature/forward-refusal")
        apply = "1"
    else:
        (root / "owned.txt").write_bytes(b"preserve this staged refusal input\n")
        native_git(root, "add", "owned.txt")
        apply = "1"
    before = _merge_forward_state(root)

    result = _merge_forward_make(root, source=source, mode=mode, apply=apply)

    assert result.returncode == 2, result.stdout + result.stderr
    assert reason in result.stdout
    assert "Error 2" in result.stderr
    assert "AGENT_NEXT target=development-merge-forward outcome=refused" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert "worktree-state" in result.stderr and "repair" in result.stderr
    assert "outcome=passed" not in result.stderr
    assert _merge_forward_state(root) == before


def test_merge_forward_unchanged_guidance_keeps_missing_proof_explicit(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, _target, _registry = checkout
    before = _merge_forward_state(root)

    result = _merge_forward_make(root, apply="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "MERGE_FORWARD_NOOP" in result.stdout
    assert "AGENT_NEXT target=development-merge-forward outcome=unchanged" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert "exact-SHA" in result.stderr and "missing" in result.stderr
    assert "outcome=passed" not in result.stderr
    assert _merge_forward_state(root) == before


def test_merge_forward_guidance_runs_despite_namesake_file(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, _target, _registry = checkout
    (root / "development-merge-forward").write_text("preserve namesake\n", encoding="utf-8")
    before = _merge_forward_state(root)

    result = _merge_forward_make(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "MERGE_FORWARD_DRY_RUN" in result.stdout
    assert "outcome=planned" in result.stderr
    assert (root / "development-merge-forward").read_text(encoding="utf-8") == "preserve namesake\n"
    assert _merge_forward_state(root) == before


def test_merge_forward_native_merge_failure_guidance_retains_failed_status(
    checkout: tuple[Path, Path, Path],
) -> None:
    root, _target, _registry = checkout
    (root / ".gitattributes").write_text("owned.txt merge=forward_fixture\n", encoding="utf-8")
    native_git(root, "config", "merge.forward_fixture.driver", "false")
    native_git(root, "add", ".gitattributes")
    native_git(root, "commit", "-m", "fixture external driver")
    native_git(root, "checkout", "-b", "feature/forward-failure")
    (root / "owned.txt").write_bytes(b"incoming change\n")
    native_git(root, "commit", "-am", "fixture incoming change")
    native_git(root, "checkout", "development")
    (root / "owned.txt").write_bytes(b"local change\n")
    native_git(root, "commit", "-am", "fixture local change")
    before = _merge_forward_state(root)

    result = _merge_forward_make(root, source="feature/forward-failure", apply="1")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "Structural conflict while merging" in result.stdout
    assert "Error 1" in result.stderr
    assert "AGENT_NEXT target=development-merge-forward outcome=failed phase=merge" in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert "cleanup" in result.stderr and "unverified" in result.stderr
    assert "outcome=passed" not in result.stderr
    assert _merge_forward_state(root) == before
