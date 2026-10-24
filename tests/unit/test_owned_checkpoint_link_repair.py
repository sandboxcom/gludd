"""Confined native recovery of one verified checkpoint owner's missing Git link."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from scripts import owned_git_checkpoint as checkpoint_owner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OWNER_SCRIPT = PROJECT_ROOT / "scripts" / "owned_git_checkpoint.py"
OWNER_MAKEFILE = PROJECT_ROOT / "make" / "20-recovery-and-git.mk"
BRANCH = "checkpoint-link-owner"
LABEL = "checkpoint-link-native-regression"


def child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS",
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    ):
        environment.pop(name, None)
    return environment


def native(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", *arguments], cwd=root, env=child_environment(),
        check=check, capture_output=True, text=True, timeout=30,
    )


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


@dataclass(frozen=True)
class LinkFixture:
    repository: Path
    worktree: Path
    sibling: Path
    administration: Path
    checkpoint: str
    head: str
    pointer: bytes


@pytest.fixture
def link_fixture(tmp_path: Path) -> LinkFixture:
    repository = (tmp_path / "native repository").resolve()
    repository.mkdir()
    native(repository, "init", "-b", "development")
    native(repository, "config", "user.name", "Checkpoint link regression")
    native(repository, "config", "user.email", "checkpoint-link@example.invalid")
    native(repository, "config", "core.autocrlf", "false")
    for name in ("owned.txt", "outside.txt"):
        (repository / name).write_text("base\n")
    native(repository, "add", "--", "owned.txt", "outside.txt")
    native(repository, "commit", "-m", "native fixture base")
    worktree = tmp_path / "selected linked worktree"
    sibling = tmp_path / "foreign broken sibling"
    native(repository, "worktree", "add", "-b", BRANCH, str(worktree))
    native(repository, "worktree", "add", "-b", "foreign-sibling", str(sibling))
    worktree = worktree.resolve()
    sibling = sibling.resolve()
    (worktree / "owned.txt").write_text("checkpoint staged\n")
    native(worktree, "add", "--", "owned.txt")
    (worktree / "owned.txt").write_text("checkpoint staged\ncheckpoint unstaged\n")
    pushed = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(OWNER_MAKEFILE),
            "git-owned-checkpoint-push", f"PYTHON={sys.executable}",
            "FILES=owned.txt", f"GIT_CHECKPOINT_LABEL={LABEL}", "GIT_CHECKPOINT_VALIDATE_ONLY=0",
        ],
        cwd=worktree, env=child_environment(), check=False, capture_output=True, text=True, timeout=30,
    )
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr
    checkpoint = native(repository, "rev-parse", "refs/stash").stdout.strip()
    native(repository, "worktree", "lock", "--reason", f"checkpoint-{checkpoint}", str(worktree))
    native(repository, "worktree", "lock", "--reason", "foreign-lock", str(sibling))
    administration = Path(native(worktree, "rev-parse", "--absolute-git-dir").stdout.strip())
    head = native(worktree, "rev-parse", "HEAD").stdout.strip()
    pointer = (worktree / ".git").read_bytes()
    (worktree / "outside.txt").write_text("current selected staged\n")
    native(worktree, "add", "--", "outside.txt")
    (worktree / "outside.txt").write_text("current selected staged\ncurrent selected unstaged\n")
    (worktree / "untracked.txt").write_text("selected untracked bytes\n")
    (sibling / "outside.txt").write_text("foreign sibling bytes\n")
    # Only these two fixture-owned pointer files are deliberately removed.
    (worktree / ".git").unlink()
    (sibling / ".git").unlink()
    return LinkFixture(repository, worktree, sibling, administration, checkpoint, head, pointer)


def repair(
    fixture: LinkFixture, *, validate: str | None = "0", environment_override: bool = False,
    replacements: dict[str, str] | None = None, origin: str = "", entrypoint: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    values = {
        "GIT_CHECKPOINT_SHA": fixture.checkpoint,
        "GIT_CHECKPOINT_WORKTREE": str(fixture.worktree),
        "GIT_CHECKPOINT_ADMIN_DIR": str(fixture.administration),
        "GIT_CHECKPOINT_EXPECTED_HEAD": fixture.head,
        "GIT_CHECKPOINT_EXPECTED_BRANCH": BRANCH,
        "GIT_CHECKPOINT_VALIDATE_ONLY": validate if validate is not None else "1",
    }
    if validate is None:
        values.pop("GIT_CHECKPOINT_VALIDATE_ONLY")
    if replacements:
        values.update(replacements)
    environment = child_environment()
    if validate is None:
        environment.pop("GIT_CHECKPOINT_VALIDATE_ONLY", None)
    arguments = ["make", "--no-print-directory", "-f", str(entrypoint or OWNER_MAKEFILE)]
    if environment_override or origin in {"environment", "override"}:
        if environment_override or origin == "override":
            arguments.append("-e")
        environment.update(values)
    else:
        arguments.extend(f"{key}={value}" for key, value in values.items())
    arguments.extend(("git-owned-checkpoint-repair-link", f"PYTHON={sys.executable}"))
    return subprocess.run(
        arguments, cwd=fixture.repository, env=environment,
        check=False, capture_output=True, text=True, timeout=30,
    )


def audit(fixture: LinkFixture) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-P", str(OWNER_SCRIPT), "count-leaks"],
        cwd=fixture.repository, env=child_environment(),
        check=False, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("environment_override", (False, True))
def test_missing_link_has_real_native_red_then_only_selected_link_is_repaired(
    link_fixture: LinkFixture, environment_override: bool,
) -> None:
    fixture = link_fixture
    failed_git = native(fixture.worktree, "rev-parse", "--show-toplevel", check=False)
    assert failed_git.returncode == 128
    assert "not a git repository" in failed_git.stderr
    failed_audit = audit(fixture)
    assert failed_audit.returncode == 0
    assert failed_audit.stdout.strip() == "1"
    assert f"OWNED-CHECKPOINT-UNOWNED checkpoint_sha={fixture.checkpoint}" in failed_audit.stderr
    assert "native git rev-parse --show-toplevel" in failed_audit.stderr
    main_before = tree_bytes(fixture.repository)
    selected_before = tree_bytes(fixture.worktree)
    sibling_before = tree_bytes(fixture.sibling)
    result = repair(fixture, environment_override=environment_override)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNED-CHECKPOINT-LINK-REPAIRED" in result.stdout
    assert (fixture.worktree / ".git").read_bytes() == fixture.pointer
    assert tree_bytes(fixture.repository) == main_before
    assert tree_bytes(fixture.worktree) == {**selected_before, ".git": fixture.pointer}
    assert tree_bytes(fixture.sibling) == sibling_before
    assert not (fixture.sibling / ".git").exists()
    assert native(fixture.sibling, "rev-parse", "--show-toplevel", check=False).returncode == 128
    assert native(fixture.worktree, "rev-parse", "--show-toplevel").stdout.strip() == str(fixture.worktree)
    assert native(fixture.worktree, "rev-parse", "HEAD").stdout.strip() == fixture.head
    assert native(fixture.worktree, "branch", "--show-current").stdout.strip() == BRANCH
    assert native(fixture.worktree, "show", ":outside.txt").stdout == "current selected staged\n"
    passed = audit(fixture)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert passed.stdout.strip() == "0"
    assert "OWNED-CHECKPOINT-COUNT retained=1 unowned=0" in passed.stderr
    assert tree_bytes(fixture.repository) == main_before
    assert tree_bytes(fixture.worktree) == {**selected_before, ".git": fixture.pointer}


@pytest.mark.parametrize("environment_override", (False, True))
def test_selected_link_plan_verifies_native_identity_without_any_write(
    link_fixture: LinkFixture, environment_override: bool,
) -> None:
    fixture = link_fixture
    before = tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling))
    result = repair(fixture, validate="1", environment_override=environment_override)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNED-CHECKPOINT-LINK-PLAN" in result.stdout
    assert "native_ownership_verified=1" in result.stdout
    assert "LINK-REPAIRED" not in result.stdout
    assert tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling)) == before


@pytest.mark.parametrize(("field", "value"), (
    ("GIT_CHECKPOINT_SHA", "short"),
    ("GIT_CHECKPOINT_SHA", "0" * 40),
    ("GIT_CHECKPOINT_EXPECTED_HEAD", "short"),
    ("GIT_CHECKPOINT_EXPECTED_HEAD", "0" * 40),
    ("GIT_CHECKPOINT_EXPECTED_BRANCH", "foreign-sibling"),
    ("GIT_CHECKPOINT_EXPECTED_BRANCH", "-invalid"),
    ("GIT_CHECKPOINT_WORKTREE", "relative-root"),
    ("GIT_CHECKPOINT_WORKTREE", "sibling"),
    ("GIT_CHECKPOINT_WORKTREE", "symlink-root"),
    ("GIT_CHECKPOINT_ADMIN_DIR", "relative-admin"),
    ("GIT_CHECKPOINT_ADMIN_DIR", "main-admin"),
    ("GIT_CHECKPOINT_ADMIN_DIR", "sibling-admin"),
    ("GIT_CHECKPOINT_VALIDATE_ONLY", "2"),
))
def test_wrong_binding_refuses_without_touching_any_owned_or_foreign_state(
    link_fixture: LinkFixture, field: str, value: str,
) -> None:
    fixture = link_fixture
    if value == "sibling":
        value = str(fixture.sibling)
    elif value == "symlink-root":
        link = fixture.worktree.parent / "foreign symlink"
        link.symlink_to(fixture.worktree, target_is_directory=True)
        value = str(link)
    elif value == "main-admin":
        value = str(fixture.repository / ".git")
    elif value == "sibling-admin":
        value = str(fixture.administration.parent / "foreign-broken-sibling")
    before = tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling))
    result = repair(fixture, replacements={field: value})
    assert result.returncode == 2, result.stdout + result.stderr
    assert "OWNED-CHECKPOINT-REFUSED" in result.stderr
    assert "AGENT_NEXT target=git-owned-checkpoint-repair-link outcome=refused" in result.stderr
    assert "LINK-REPAIRED" not in result.stdout
    assert tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling)) == before


@pytest.mark.parametrize("kind", ("regular", "directory", "symlink", "dangling-symlink"))
def test_existing_link_is_never_replaced(link_fixture: LinkFixture, kind: str) -> None:
    fixture = link_fixture
    pointer = fixture.worktree / ".git"
    foreign = fixture.worktree.parent / "foreign pointer bytes"
    foreign.write_bytes(b"foreign native metadata\n")
    if kind == "regular":
        pointer.write_bytes(b"existing owned bytes\n")
    elif kind == "directory":
        pointer.mkdir()
        (pointer / "retained.txt").write_bytes(b"existing directory bytes\n")
    else:
        pointer.symlink_to(foreign if kind == "symlink" else foreign.with_name("absent foreign"))
    before = tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling))
    target = os.readlink(pointer) if pointer.is_symlink() else None
    result = repair(fixture)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "refuses an existing file, directory or symlink" in result.stderr
    assert "LINK-REPAIRED" not in result.stdout
    assert tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling)) == before
    assert foreign.read_bytes() == b"foreign native metadata\n"
    if target is not None:
        assert os.readlink(pointer) == target


@pytest.mark.parametrize("name", ("gitdir", "commondir", "HEAD", "locked"))
def test_tampered_administration_is_not_reconstructed(link_fixture: LinkFixture, name: str) -> None:
    fixture = link_fixture
    path = fixture.administration / name
    if name == "gitdir":
        path.write_text(str(fixture.sibling / ".git") + "\n")
    elif name == "commondir":
        path.write_text("../foreign-common\n")
    elif name == "HEAD":
        path.write_text("ref: refs/heads/foreign-sibling\n")
    else:
        path.write_text("foreign retained lock\n")
    before = tuple(tree_bytes(root) for root in (fixture.repository, fixture.worktree, fixture.sibling))
    result = repair(fixture)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "LINK-REPAIRED" not in result.stdout
    assert tuple(tree_bytes(root) for root in (fixture.repository, fixture.worktree, fixture.sibling)) == before


def test_real_fifo_metadata_refuses_without_waiting_for_a_writer(link_fixture: LinkFixture) -> None:
    fixture = link_fixture
    index = fixture.administration / "index"
    index.unlink()
    os.mkfifo(index)
    ordinary_before = {
        name: (fixture.administration / name).read_bytes()
        for name in ("gitdir", "commondir", "HEAD", "locked")
    }
    selected_before = tree_bytes(fixture.worktree)
    sibling_before = tree_bytes(fixture.sibling)
    result = repair(fixture)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "metadata is not a regular file" in result.stderr
    assert not (fixture.worktree / ".git").exists()
    assert stat.S_ISFIFO(index.stat().st_mode)
    assert {
        name: (fixture.administration / name).read_bytes() for name in ordinary_before
    } == ordinary_before
    assert tree_bytes(fixture.worktree) == selected_before
    assert tree_bytes(fixture.sibling) == sibling_before


def invoke_native_owner(fixture: LinkFixture) -> None:
    assert Path(checkpoint_owner.__file__).resolve() == OWNER_SCRIPT
    checkpoint_owner.repair_link(
        fixture.repository, str(fixture.worktree), str(fixture.administration),
        fixture.checkpoint, fixture.head, BRANCH, validate=False,
    )


def test_changed_index_fence_retains_new_input_without_creating_link(
    link_fixture: LinkFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = link_fixture
    original = checkpoint_owner._repair_link_state
    calls = 0
    observed: list[dict[str, bytes]] = []

    def changed_state(
        root: Path, worktree: Path, administration: Path, sha: str, head: str, branch: str,
    ) -> checkpoint_owner.RepairState:
        nonlocal calls
        calls += 1
        if calls == 2:
            index = administration / "index"
            index.write_bytes(index.read_bytes() + b"owned concurrent fixture edit")
            observed.append(tree_bytes(fixture.repository))
        return original(root, worktree, administration, sha, head, branch)

    monkeypatch.setattr(checkpoint_owner, "_repair_link_state", changed_state)
    with pytest.raises(checkpoint_owner.Refused, match="changed before pointer creation"):
        invoke_native_owner(fixture)
    assert calls == 2
    assert not (fixture.worktree / ".git").exists()
    assert tree_bytes(fixture.repository) == observed[0]


@pytest.mark.parametrize("kind", ("regular", "symlink"))
def test_native_exclusive_create_preserves_a_concurrently_created_foreign_link(
    link_fixture: LinkFixture, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    fixture = link_fixture
    assert vars(checkpoint_owner)["os"] is os
    actual_open = os.open
    foreign = fixture.worktree.parent / "foreign race bytes"
    foreign.write_bytes(b"preserved external bytes\n")

    def racing_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None,
    ) -> int:
        if path == ".git" and flags & os.O_CREAT:
            pointer = fixture.worktree / ".git"
            if kind == "regular":
                pointer.write_bytes(b"concurrent native pointer bytes\n")
            else:
                pointer.symlink_to(foreign)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    before = tree_bytes(fixture.repository)
    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(FileExistsError):
        invoke_native_owner(fixture)
    assert tree_bytes(fixture.repository) == before
    assert foreign.read_bytes() == b"preserved external bytes\n"
    pointer = fixture.worktree / ".git"
    if kind == "regular":
        assert pointer.read_bytes() == b"concurrent native pointer bytes\n"
    else:
        assert pointer.is_symlink()
        assert os.readlink(pointer) == str(foreign)


def test_fsync_failure_retains_exact_created_pointer_without_success_claim(
    link_fixture: LinkFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = link_fixture
    assert vars(checkpoint_owner)["os"] is os
    before = tree_bytes(fixture.repository)

    def fail_fsync(descriptor: int) -> None:
        assert descriptor >= 0
        raise OSError("owned synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(checkpoint_owner.Refused, match="selected pointer may have been created"):
        invoke_native_owner(fixture)
    assert (fixture.worktree / ".git").read_bytes() == fixture.pointer
    assert tree_bytes(fixture.repository) == before
    assert "LINK-REPAIRED" not in capsys.readouterr().out


def test_default_mode_is_a_native_verified_nonmutating_plan(link_fixture: LinkFixture) -> None:
    fixture = link_fixture
    before = tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling))
    result = repair(fixture, validate=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNED-CHECKPOINT-LINK-PLAN" in result.stdout
    assert "outcome=planned" in result.stderr
    assert tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling)) == before




def root_entrypoint(fixture: LinkFixture) -> Path:
    """Copy only the actual thirteen-file Make include graph into owned CWD."""
    entrypoint = PROJECT_ROOT / "Makefile"
    names = (
        "00-foundation.mk", "10-observability-and-tests.mk", "20-recovery-and-git.mk",
        "30-ci-and-release.mk", "40-cross-version-and-worktrees.mk",
        "50-development-and-guardrails.mk", "60-quality-packaging-and-sandbox.mk",
        "70-orchestration-and-model-runtime.mk", "80-agent-enforcement.mk",
        "90-infrastructure-and-services.mk", "95-collections-and-pipelines.mk",
        "99-azure-and-local-models.mk",
    )
    source = entrypoint.read_text()
    assert [line for line in source.splitlines() if line.startswith("include ")] == [
        f"include make/{name}" for name in names
    ]
    (fixture.repository / "Makefile").write_text(source)
    (fixture.repository / "make").mkdir()
    for name in names:
        (fixture.repository / "make" / name).write_bytes((PROJECT_ROOT / "make" / name).read_bytes())
    (fixture.repository / "scripts").mkdir()
    (fixture.repository / "scripts" / "owned_git_checkpoint.py").symlink_to(OWNER_SCRIPT)
    return fixture.repository / "Makefile"

LITERAL_FIELDS = (
    "GIT_CHECKPOINT_SHA", "GIT_CHECKPOINT_WORKTREE", "GIT_CHECKPOINT_ADMIN_DIR",
    "GIT_CHECKPOINT_EXPECTED_HEAD", "GIT_CHECKPOINT_EXPECTED_BRANCH", "GIT_CHECKPOINT_VALIDATE_ONLY",
)
EVALUATION_CASES = (
    *[(field, "make-shell") for field in LITERAL_FIELDS],
    ("GIT_CHECKPOINT_WORKTREE", "shell"),
    ("GIT_CHECKPOINT_WORKTREE", "backticks"),
)


@pytest.mark.parametrize("entrypoint_kind", ("fragment", "root"))
@pytest.mark.parametrize("origin", ("command", "environment", "override"))
@pytest.mark.parametrize(("field", "kind"), EVALUATION_CASES)
def test_literal_bindings_refuse_the_same_actively_witnessed_payload(
    link_fixture: LinkFixture, field: str, kind: str, origin: str, entrypoint_kind: str,
) -> None:
    fixture = link_fixture
    marker = fixture.worktree.parent / "owned evaluation witness"
    quoted = shlex.quote(str(marker))
    if kind == "make-shell":
        payload = f"$(shell touch {quoted})"
    elif kind == "shell":
        payload = f"$$(touch {quoted})"
    else:
        payload = f"`touch {quoted}`"
    entrypoint = fixture.worktree.parent / "unguarded-literal-witness.mk"
    entrypoint.write_text(f".PHONY: witness\nwitness:\n\t@printf '%s\\n' \"$({field})\"\n")
    environment = child_environment()
    arguments = ["make", "--no-print-directory", "-f", str(entrypoint), "witness"]
    if origin == "command":
        arguments.append(f"{field}={payload}")
    else:
        environment[field] = payload
        if origin == "override":
            arguments.append("-e")
    unguarded = subprocess.run(
        arguments, cwd=fixture.repository, env=environment,
        check=False, capture_output=True, text=True, timeout=30,
    )
    assert unguarded.returncode == 0, unguarded.stdout + unguarded.stderr
    assert marker.is_file(), "The exact variable/origin/payload must execute before guard proof"
    marker.unlink()  # Remove only the owned witness before retaining the guarded assertion.
    actual_entrypoint = root_entrypoint(fixture) if entrypoint_kind == "root" else None
    before = tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling))
    guarded = repair(fixture, replacements={field: payload}, origin=origin, entrypoint=actual_entrypoint)
    assert guarded.returncode == 2, guarded.stdout + guarded.stderr
    assert "LINK-REPAIRED" not in guarded.stdout
    assert not marker.exists()
    assert tuple(tree_bytes(path) for path in (fixture.repository, fixture.worktree, fixture.sibling)) == before
