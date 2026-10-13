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
