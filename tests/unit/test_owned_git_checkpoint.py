"""Native Git checkpoint refusal and index-preservation regressions."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

ARTIFACT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
IS_ARTIFACT = ARTIFACT_ROOT.name == "owned-git-checkpoint"
OWNER_MAKEFILE = (
    ARTIFACT_ROOT / "Makefile" if IS_ARTIFACT
    else PROJECT_ROOT / "make" / "20-recovery-and-git.mk"
)
OWNER_SCRIPT = (
    ARTIFACT_ROOT / "owned_git_checkpoint.py" if IS_ARTIFACT
    else PROJECT_ROOT / "scripts" / "owned_git_checkpoint.py"
)
LABEL = "s98-owned-checkpoint-001"


class LiteralInputs(TypedDict, total=False):
    files: str
    label: str
    sha: str
    validate: str


@pytest.mark.parametrize("field", ("files", "label", "sha", "validate"))
def test_make_exports_literal_inputs_without_evaluating_functions(repo: Path, field: str) -> None:
    (repo / "owned.txt").write_text("owned\n")
    sentry = repo / "unexpected-make-expansion"
    expression = f"$(shell touch {sentry})"
    kwargs: LiteralInputs = {}
    if field == "files":
        kwargs["files"] = expression
    elif field == "label":
        kwargs["label"] = expression
    elif field == "sha":
        kwargs["sha"] = expression
    else:
        kwargs["validate"] = expression
    before = snapshot(repo)
    result = owner(repo, action="restore" if field == "sha" else "push", **kwargs)
    assert result.returncode != 0
    assert not sentry.exists(), "Make executed input data before native ownership admission"
    assert snapshot(repo) == before


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, text=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-b", "development")
    git(root, "config", "user.email", "checkpoint-tests@example.invalid")
    git(root, "config", "user.name", "Checkpoint regression")
    git(root, "config", "core.autocrlf", "false")
    (root / "scripts").mkdir()
    (root / "scripts" / "owned_git_checkpoint.py").symlink_to(OWNER_SCRIPT)
    for name in ("owned.txt", "outside.txt", "deleted.txt"):
        (root / name).write_text("base\n")
    git(root, "add", "--", "owned.txt", "outside.txt", "deleted.txt")
    git(root, "commit", "-m", "fixture")
    return root


def owner(
    repo: Path, action: str = "push", files: str = "owned.txt", label: str = LABEL,
    sha: str = "", validate: str = "0", extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("GIT_INDEX_FILE", None)
    environment.pop("GIT_DIR", None)
    environment.pop("GIT_WORK_TREE", None)
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(OWNER_MAKEFILE),
            f"git-owned-checkpoint-{action}", f"PYTHON={sys.executable}",
            f"FILES={files}", f"GIT_CHECKPOINT_LABEL={label}",
            f"GIT_CHECKPOINT_SHA={sha}", f"GIT_CHECKPOINT_VALIDATE_ONLY={validate}",
        ],
        cwd=repo, env=environment, check=False, capture_output=True, text=True,
    )


def snapshot(repo: Path) -> tuple[str, str, str, dict[str, bytes]]:
    return (
        git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout,
        git(repo, "ls-files", "--stage").stdout,
        git(repo, "stash", "list", "--format=%H:%s").stdout,
        {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*")
         if p.is_file() and ".git" not in p.relative_to(repo).parts},
    )


def pushed_sha(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"checkpoint_sha=([a-f0-9]{40,64})\b", result.stdout)
    assert match, result.stdout
    return match.group(1)


def test_native_roundtrip_keeps_partial_index_untracked_and_other_unstaged(repo: Path) -> None:
    (repo / "owned.txt").write_text("staged\n")
    git(repo, "add", "--", "owned.txt")
    (repo / "owned.txt").write_text("staged\nunstaged\n")
    (repo / "new.txt").write_text("untracked\n")
    (repo / "outside.txt").write_text("outside unstaged\n")
    before = snapshot(repo)
    sha = pushed_sha(owner(repo, files="owned.txt new.txt"))
    assert (repo / "owned.txt").read_text() == "base\n"
    assert not (repo / "new.txt").exists()
    assert (repo / "outside.txt").read_text() == "outside unstaged\n"
    assert git(repo, "diff", "--cached", "--name-only").stdout == ""
    result = owner(repo, "restore", files="owned.txt new.txt", sha=sha)
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(repo)
    assert after[:2] == before[:2]
    assert after[3] == before[3]
    assert git(repo, "rev-parse", "refs/stash").stdout.strip() == sha
    assert "checkpoint_retained=1" in result.stdout


@pytest.mark.parametrize("files", [
    "", ".", "..", "../outside", "/tmp/outside", "*", ":(all)",
    ".git/config", "missing.txt", "directory",
])
def test_unsafe_or_missing_paths_refused_without_mutation(repo: Path, files: str) -> None:
    (repo / "owned.txt").write_text("owned\n")
    (repo / "directory").mkdir()
    (repo / "directory" / "child.txt").write_text("unknown\n")
    before = snapshot(repo)
    result = owner(repo, files=files)
    assert result.returncode != 0
    assert "OWNED-CHECKPOINT-REFUSED" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_foreign_staged_index_refused_instead_of_capturing_it(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    (repo / "outside.txt").write_text("foreign staged\n")
    git(repo, "add", "--", "outside.txt")
    before = snapshot(repo)
    result = owner(repo)
    assert result.returncode != 0
    assert "outside explicit FILES" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_native_scoped_stash_really_captures_other_staged_paths(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    (repo / "outside.txt").write_text("foreign staged\n")
    git(repo, "add", "--", "outside.txt")
    git(repo, "stash", "push", "-u", "-m", "native negative control", "--", "owned.txt")
    actual = git(repo, "diff", "--name-only", "stash@{0}^1", "stash@{0}^2").stdout
    assert "outside.txt" in actual.splitlines()


def test_exact_top_sha_and_label_required_without_mutation(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    sha = pushed_sha(owner(repo))
    (repo / "outside.txt").write_text("foreign\n")
    git(repo, "stash", "push", "-m", "foreign owner", "--", "outside.txt")
    before = snapshot(repo)
    for attempt in (
        owner(repo, "restore", sha=sha),
        owner(repo, "restore", sha="0" * 40),
        owner(repo, "restore", sha="stash@{0}"),
        owner(repo, "restore", sha=git(repo, "rev-parse", "refs/stash").stdout.strip()),
    ):
        assert attempt.returncode != 0
        assert "OWNED-CHECKPOINT-REFUSED" in attempt.stdout + attempt.stderr
        assert snapshot(repo) == before


def test_owned_scope_and_unique_label_are_checked(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    sha = pushed_sha(owner(repo))
    (repo / "outside.txt").write_text("outside\n")
    before = snapshot(repo)
    assert owner(repo, "restore", files="outside.txt", sha=sha).returncode != 0
    assert owner(repo, files="outside.txt", label=LABEL).returncode != 0
    assert snapshot(repo) == before


def test_conflicting_work_is_preserved_and_checkpoint_retained(repo: Path) -> None:
    (repo / "owned.txt").write_text("checkpoint\n")
    sha = pushed_sha(owner(repo))
    (repo / "owned.txt").write_text("new work\n")
    before = snapshot(repo)
    result = owner(repo, "restore", sha=sha)
    assert result.returncode != 0
    assert snapshot(repo) == before
    assert git(repo, "show", f"{sha}:owned.txt").stdout == "checkpoint\n"


def test_deleted_tracked_file_roundtrips_as_a_deletion(repo: Path) -> None:
    (repo / "deleted.txt").unlink()
    before = snapshot(repo)
    sha = pushed_sha(owner(repo, files="deleted.txt"))
    assert (repo / "deleted.txt").read_text() == "base\n"
    result = owner(repo, "restore", files="deleted.txt", sha=sha)
    assert result.returncode == 0, result.stdout + result.stderr
    after = snapshot(repo)
    assert after[:2] == before[:2]
    assert after[3] == before[3]


def test_validate_only_is_non_mutating_and_not_a_checkpoint_receipt(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo, validate="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAN" in result.stdout
    assert "checkpoint_sha=" not in result.stdout
    assert snapshot(repo) == before


def test_symlink_input_refused_without_touching_target(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-sensitive.txt"
    outside.write_text("preserve\n")
    (repo / "link.txt").symlink_to(outside)
    before = snapshot(repo)
    result = owner(repo, files="link.txt")
    assert result.returncode != 0
    assert snapshot(repo) == before
    assert outside.read_text() == "preserve\n"


def test_clean_plan_does_not_require_or_claim_runtime_checkpoint(repo: Path) -> None:
    before = snapshot(repo)
    for result in (
        owner(repo, validate="1"),
        owner(repo, "restore", sha="0" * 40, validate="1"),
    ):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "native_ownership_verified=0" in result.stdout
        assert "checkpoint_sha=" not in result.stdout
        assert snapshot(repo) == before


def test_independent_commit_does_not_lose_previous_index(repo: Path) -> None:
    (repo / "owned.txt").write_text("staged\n")
    git(repo, "add", "--", "owned.txt")
    (repo / "owned.txt").write_text("staged\nunstaged\n")
    sha = pushed_sha(owner(repo))
    (repo / "outside.txt").write_text("completed independent change\n")
    git(repo, "add", "--", "outside.txt")
    git(repo, "commit", "-m", "independent complete change")
    committed = git(repo, "rev-parse", "HEAD").stdout
    result = owner(repo, "restore", sha=sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout == committed
    assert git(repo, "show", ":owned.txt").stdout == "staged\n"
    assert (repo / "owned.txt").read_text() == "staged\nunstaged\n"
    assert (repo / "outside.txt").read_text() == "completed independent change\n"


def test_actual_native_conflict_retains_checkpoint_and_current_commit(repo: Path) -> None:
    (repo / "owned.txt").write_text("checkpoint version\n")
    sha = pushed_sha(owner(repo))
    (repo / "owned.txt").write_text("completed conflicting version\n")
    git(repo, "add", "--", "owned.txt")
    git(repo, "commit", "-m", "conflicting complete change")
    committed = git(repo, "rev-parse", "HEAD").stdout
    result = owner(repo, "restore", sha=sha)
    assert result.returncode != 0
    assert "OWNED-CHECKPOINT-REFUSED" in result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout == committed
    assert git(repo, "rev-parse", "refs/stash").stdout.strip() == sha
    assert git(repo, "show", f"{sha}:owned.txt").stdout == "checkpoint version\n"
    assert git(repo, "show", "HEAD:owned.txt").stdout == "completed conflicting version\n"


@pytest.mark.parametrize("label", ["", "short", "has whitespace", "../outside", "Wrong-Case"])
def test_unsafe_label_refused_without_mutation(repo: Path, label: str) -> None:
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo, label=label)
    assert result.returncode != 0
    assert "OWNED-CHECKPOINT-REFUSED" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_staged_deletion_is_explicitly_refused_before_native_mutation(repo: Path) -> None:
    git(repo, "rm", "--", "deleted.txt")
    before = snapshot(repo)
    result = owner(repo, files="deleted.txt")
    assert result.returncode != 0
    assert "unsupported staged deletion" in result.stdout + result.stderr
    assert "OWNED-CHECKPOINT-START" not in result.stdout
    assert snapshot(repo) == before


def test_nonboolean_plan_value_is_refused(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo, validate="true")
    assert result.returncode != 0
    assert "must be 0 or 1" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_native_nul_decoder_imported_in_parent_process() -> None:
    spec = importlib.util.spec_from_file_location(
        "owned_checkpoint_artifact", OWNER_SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.names("") == set()
    assert module.names("one.txt\0two.txt\0") == {"one.txt", "two.txt"}


def test_redirected_index_refused_without_mutation(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo, extra_env={"GIT_INDEX_FILE": str(repo / ".git" / "foreign-index")})
    assert result.returncode != 0
    assert "redirected Git state" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_nonroot_invocation_refused_without_mutation(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo / "scripts", files="../owned.txt")
    assert result.returncode != 0
    assert "repository root" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_detached_head_refused_without_mutation(repo: Path) -> None:
    git(repo, "switch", "--detach", "HEAD")
    (repo / "owned.txt").write_text("owned\n")
    before = snapshot(repo)
    result = owner(repo)
    assert result.returncode != 0
    assert "named branch" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_clean_actual_push_refused_without_success_receipt(repo: Path) -> None:
    before = snapshot(repo)
    result = owner(repo)
    assert result.returncode != 0
    assert "actual tracked or untracked change" in result.stdout + result.stderr
    assert "checkpoint_sha=" not in result.stdout
    assert snapshot(repo) == before


def test_restore_does_not_change_new_foreign_staged_work(repo: Path) -> None:
    (repo / "owned.txt").write_text("owned\n")
    sha = pushed_sha(owner(repo))
    (repo / "outside.txt").write_text("foreign staged\n")
    git(repo, "add", "--", "outside.txt")
    before = snapshot(repo)
    result = owner(repo, "restore", sha=sha)
    assert result.returncode != 0
    assert "current staged work" in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_submodule_index_input_refused_without_mutation(repo: Path) -> None:
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},module")
    before = snapshot(repo)
    result = owner(repo, files="module")
    assert result.returncode != 0
    assert "submodule scope" in result.stdout + result.stderr
    assert snapshot(repo) == before


REDIRECTED_GIT_STATE = (
    "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
)


def retained_inventory(
    repo: Path, *, guard: bool = False, force: str = "0",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Exercise either the owner CLI or the actual repository Make guard."""
    environment = os.environ.copy()
    for key in REDIRECTED_GIT_STATE:
        environment.pop(key, None)
    if extra_env:
        environment.update(extra_env)
    command = (
        [
            "make", "--no-print-directory", "-f",
            str(PROJECT_ROOT / "make" / "50-development-and-guardrails.mk"),
            "_stash-leak-guard", f"PYTHON={sys.executable}", f"FORCE={force}",
        ]
        if guard else [sys.executable, str(OWNER_SCRIPT), "count-leaks"]
    )
    return subprocess.run(
        command, cwd=repo, env=environment, check=False,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("force", ("0", "1"))
def test_native_guard_refuses_redirected_state_even_with_no_stashes(repo: Path, force: str) -> None:
    before = snapshot(repo)
    result = retained_inventory(
        repo, guard=True, force=force,
        extra_env={"GIT_INDEX_FILE": str(repo / ".git" / "foreign-index")},
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "redirected Git state" in result.stdout + result.stderr
    assert "_stash-leak-guard: PASS" not in result.stdout
    assert snapshot(repo) == before


def retained_objects(repo: Path, sha: str) -> dict[str, str]:
    """Capture every stash commit and native parent before read-only admission."""
    commits = git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()
    return {commit: git(repo, "cat-file", "-p", commit).stdout for commit in commits}


def assert_retained_count(repo: Path, *, retained: int, unowned: int) -> subprocess.CompletedProcess[str]:
    before = snapshot(repo)
    index = (repo / ".git" / "index").read_bytes()
    result = retained_inventory(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"{unowned}\n"
    assert f"OWNED-CHECKPOINT-COUNT retained={retained} unowned={unowned}" in result.stderr
    assert (repo / ".git" / "index").read_bytes() == index
    assert snapshot(repo) == before
    return result


def test_retained_inventory_preserves_applied_partial_index_and_all_versions(repo: Path) -> None:
    (repo / "owned.txt").write_text("staged version\n")
    git(repo, "add", "--", "owned.txt")
    (repo / "owned.txt").write_text("staged version\nunstaged version\n")
    (repo / "new.txt").write_text("untracked checkpoint version\n")
    sha = pushed_sha(owner(repo, files="owned.txt new.txt"))
    objects = retained_objects(repo, sha)
    restored = owner(repo, "restore", files="owned.txt new.txt", sha=sha)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    (repo / "outside.txt").write_text("current independent staged version\n")
    git(repo, "add", "--", "outside.txt")
    result = assert_retained_count(repo, retained=1, unowned=0)
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    assert retained_objects(repo, sha) == objects
    assert git(repo, "show", f"{sha}^1:owned.txt").stdout == "base\n"
    assert git(repo, "show", f"{sha}^2:owned.txt").stdout == "staged version\n"
    assert git(repo, "show", f"{sha}:owned.txt").stdout == "staged version\nunstaged version\n"
    assert git(repo, "show", f"{sha}^3:new.txt").stdout == "untracked checkpoint version\n"
    assert git(repo, "show", ":owned.txt").stdout == "staged version\n"
    assert (repo / "owned.txt").read_text() == "staged version\nunstaged version\n"
    assert (repo / "new.txt").read_text() == "untracked checkpoint version\n"
    assert git(repo, "show", ":outside.txt").stdout == "current independent staged version\n"


def test_retained_inventory_checks_entire_stack_across_independent_commit_bases(repo: Path) -> None:
    (repo / "owned.txt").write_text("first checkpoint\n")
    first = pushed_sha(owner(repo))
    (repo / "deleted.txt").write_text("independent completed version\n")
    git(repo, "add", "--", "deleted.txt")
    git(repo, "commit", "-m", "independent complete change")
    committed = git(repo, "rev-parse", "HEAD").stdout
    (repo / "outside.txt").write_text("second checkpoint\n")
    second = pushed_sha(owner(repo, files="outside.txt", label=f"{LABEL}-002"))
    original_objects = {sha: retained_objects(repo, sha) for sha in (first, second)}
    assert git(repo, "rev-parse", f"{first}^1").stdout != git(repo, "rev-parse", f"{second}^1").stdout
    result = assert_retained_count(repo, retained=2, unowned=0)
    for sha in (first, second):
        assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    (repo / "owned.txt").write_text("foreign recoverable version\n")
    git(repo, "stash", "push", "-m", "ordinary unknown owner", "--", "owned.txt")
    foreign = git(repo, "rev-parse", "refs/stash").stdout.strip()
    foreign_objects = retained_objects(repo, foreign)
    result = assert_retained_count(repo, retained=2, unowned=1)
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={foreign}" not in result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout == committed
    assert retained_objects(repo, foreign) == foreign_objects
    for sha in (first, second):
        assert retained_objects(repo, sha) == original_objects[sha]
    assert git(repo, "show", f"{foreign}:owned.txt").stdout == "foreign recoverable version\n"


@pytest.mark.parametrize("topology", ("missing-index", "extra-parent", "foreign-index-base", "parented-untracked"))
def test_non_native_parent_topology_is_unowned_and_all_objects_are_retained(repo: Path, topology: str) -> None:
    (repo / "owned.txt").write_text("retained checkpoint\n")
    (repo / "new.txt").write_text("retained untracked checkpoint\n")
    original = pushed_sha(owner(repo, files="owned.txt new.txt"))
    _, base, index, untracked = git(repo, "rev-list", "--parents", "-n", "1", original).stdout.split()
    subject = git(repo, "show", "-s", "--format=%s", original).stdout.strip()
    parents = [base, index, untracked]
    if topology == "missing-index":
        parents = [base]
    elif topology == "extra-parent":
        parents.append(original)
    elif topology == "foreign-index-base":
        parents[1] = git(
            repo, "commit-tree", f"{index}^{{tree}}", "-p", original,
            "-m", "forged index with wrong base",
        ).stdout.strip()
    else:
        parents[2] = git(
            repo, "commit-tree", f"{untracked}^{{tree}}", "-p", base,
            "-m", "forged untracked parent with ancestry",
        ).stdout.strip()
    arguments = ["commit-tree", f"{original}^{{tree}}", "-m", subject]
    for parent in parents:
        arguments.extend(("-p", parent))
    forged = git(repo, *arguments).stdout.strip()
    git(repo, "update-ref", "--create-reflog", "-m", subject, "refs/stash", forged, original)
    objects = {sha: retained_objects(repo, sha) for sha in (original, forged)}
    result = assert_retained_count(repo, retained=1, unowned=1)
    assert f"OWNED-CHECKPOINT-UNOWNED checkpoint_sha={forged}" in result.stderr
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={forged}" not in result.stderr
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={original}" in result.stderr
    for sha in (original, forged):
        assert retained_objects(repo, sha) == objects[sha]


@pytest.mark.parametrize("key", REDIRECTED_GIT_STATE)
def test_retained_inventory_rejects_every_redirected_git_state_before_inventory(repo: Path, key: str) -> None:
    before = snapshot(repo)
    index = (repo / ".git" / "index").read_bytes()
    value = "1" if key == "GIT_CONFIG_COUNT" else str(repo / ".git")
    result = retained_inventory(repo, extra_env={key: value})
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "OWNED-CHECKPOINT-REFUSED redirected Git state" in result.stderr
    assert "OWNED-CHECKPOINT-COUNT" not in result.stderr
    assert (repo / ".git" / "index").read_bytes() == index
    assert snapshot(repo) == before


def test_actual_native_make_guard_accepts_retained_applied_checkpoint_without_force(repo: Path) -> None:
    (repo / "owned.txt").write_text("staged retained version\n")
    git(repo, "add", "--", "owned.txt")
    (repo / "owned.txt").write_text("staged retained version\nunstaged retained version\n")
    sha = pushed_sha(owner(repo))
    restored = owner(repo, "restore", sha=sha)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    before = snapshot(repo)
    objects = retained_objects(repo, sha)
    result = retained_inventory(repo, guard=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "_stash-leak-guard: PASS" in result.stdout
    assert "STASH-LEAK (FORCED)" not in result.stdout
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    assert "OWNED-CHECKPOINT-COUNT retained=1 unowned=0" in result.stderr
    assert snapshot(repo) == before
    assert retained_objects(repo, sha) == objects


@pytest.mark.parametrize("force", ("0", "1"))
def test_actual_native_make_guard_preserves_unknown_stash_and_existing_force_policy(repo: Path, force: str) -> None:
    (repo / "outside.txt").write_text("unknown recoverable version\n")
    git(repo, "stash", "push", "-m", "ordinary unknown owner", "--", "outside.txt")
    sha = git(repo, "rev-parse", "refs/stash").stdout.strip()
    before = snapshot(repo)
    objects = retained_objects(repo, sha)
    result = retained_inventory(repo, guard=True, force=force)
    assert "OWNED-CHECKPOINT-COUNT retained=0 unowned=1" in result.stderr
    if force == "1":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "STASH-LEAK (FORCED)" in result.stdout
        assert "_stash-leak-guard: PASS" in result.stdout
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "STASH-LEAK BLOCKED" in result.stdout
        assert "_stash-leak-guard: PASS" not in result.stdout
        state = repo / ".git" / "gludd-stash-leak-state.json"
        assert state.is_file(), "Guard receipt must belong to this Git worktree, not a shared /tmp file"
        assert '"block_reason":"_stash-leak-guard:stash-entries"' in state.read_text()
    assert snapshot(repo) == before
    assert retained_objects(repo, sha) == objects
