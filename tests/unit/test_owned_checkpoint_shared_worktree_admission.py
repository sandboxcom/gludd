"""Real-Git shared checkpoint admission without transferring restore authority."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.test_owned_git_checkpoint import (
    LABEL,
    OWNER_SCRIPT,
    assert_retained_count,
    git,
    owner,
    pushed_sha,
    retained_inventory,
    retained_objects,
    snapshot,
)

pytest_plugins = ["tests.unit.test_owned_git_checkpoint"]


def linked(root: Path, path: Path, branch: str = "peer-owner") -> Path:
    """Let native Git register a live peer, including whitespace in its root."""
    git(root, "worktree", "add", "-b", branch, str(path))
    return path.resolve()


@pytest.fixture
def peer(repo: Path, tmp_path: Path) -> Path:
    return linked(repo, tmp_path / "peer worktree\nwith newline")


def saved(root: Path, *, label: str = LABEL) -> str:
    (root / "owned.txt").write_text("peer staged version\n")
    git(root, "add", "--", "owned.txt")
    (root / "owned.txt").write_text("peer staged version\npeer unstaged version\n")
    (root / "new.txt").write_text("peer untracked version\n")
    return pushed_sha(owner(root, files="owned.txt new.txt", label=label))


def loaded_owner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("shared_checkpoint_owner", OWNER_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_checkpoint_passes_peer_native_guard_with_all_saved_bytes_intact(repo: Path, peer: Path) -> None:
    sha = saved(repo)
    original = retained_objects(repo, sha)
    before = snapshot(repo), snapshot(peer)
    result = retained_inventory(peer, guard=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "_stash-leak-guard: PASS" in result.stdout
    assert "STASH-LEAK (FORCED)" not in result.stdout
    assert "OWNED-CHECKPOINT-COUNT retained=1 unowned=0" in result.stderr
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    assert (snapshot(repo), snapshot(peer)) == before
    assert retained_objects(repo, sha) == original
    assert git(peer, "rev-parse", "refs/stash").stdout.strip() == sha


def test_peer_checkpoint_is_admitted_from_main_and_another_registered_peer(
    repo: Path,
    peer: Path,
    tmp_path: Path,
) -> None:
    observer = linked(repo, tmp_path / "observer", "peer-observer")
    sha = saved(peer)
    original = retained_objects(repo, sha)
    for root in (repo, peer, observer):
        result = assert_retained_count(root, retained=1, unowned=0)
        assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    assert retained_objects(repo, sha) == original


def test_peer_admission_never_grants_restore_authority(repo: Path, peer: Path) -> None:
    sha = saved(peer)
    assert_retained_count(repo, retained=1, unowned=0)
    before = snapshot(repo), snapshot(peer)
    objects = retained_objects(repo, sha)
    result = owner(repo, "restore", files="owned.txt new.txt", sha=sha)
    assert result.returncode != 0
    assert "foreign checkpoint label, worktree identity or explicit scope" in result.stderr
    assert (snapshot(repo), snapshot(peer)) == before
    restored = owner(peer, "restore", files="owned.txt new.txt", sha=sha)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert git(peer, "show", ":owned.txt").stdout == "peer staged version\n"
    assert (peer / "new.txt").read_text() == "peer untracked version\n"
    assert retained_objects(repo, sha) == objects
    assert_retained_count(repo, retained=1, unowned=0)


def test_shared_inventory_keeps_other_unknown_stashes_blocking(repo: Path, peer: Path) -> None:
    sha = saved(peer)
    (repo / "outside.txt").write_text("unknown independent version\n")
    git(repo, "stash", "push", "-m", "unknown owner", "--", "outside.txt")
    unknown = git(repo, "rev-parse", "refs/stash").stdout.strip()
    objects = {item: retained_objects(repo, item) for item in (sha, unknown)}
    result = assert_retained_count(peer, retained=1, unowned=1)
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" in result.stderr
    guard = retained_inventory(peer, guard=True)
    assert guard.returncode != 0
    assert "STASH-LEAK BLOCKED" in guard.stdout
    for item, content in objects.items():
        assert retained_objects(repo, item) == content


@pytest.mark.parametrize(
    "state",
    (
        "unregistered",
        "prunable",
        "symlink-gitfile",
        "wrong-backlink",
        "symlink-backlink",
        "symlink-commondir",
        "symlink-root",
    ),
)
def test_stale_or_redirected_peer_registration_never_admits_its_checkpoint(
    repo: Path,
    peer: Path,
    tmp_path: Path,
    state: str,
) -> None:
    sha = saved(peer)
    if state == "unregistered":
        git(repo, "worktree", "remove", str(peer))
    elif state == "prunable":
        peer.rename(tmp_path / "moved-without-native-repair")
    elif state == "symlink-gitfile":
        gitfile = peer / ".git"
        original = peer / ".git-original"
        gitfile.rename(original)
        gitfile.symlink_to(original)
    elif state == "wrong-backlink":
        (peer / ".git").write_text(f"gitdir: {repo / '.git'}\n")
    elif state == "symlink-root":
        moved = tmp_path / "moved-root"
        peer.rename(moved)
        peer.symlink_to(moved, target_is_directory=True)
    else:
        directory = Path(git(peer, "rev-parse", "--absolute-git-dir").stdout.strip())
        name = "gitdir" if state == "symlink-backlink" else "commondir"
        original = directory / f"{name}-original"
        (directory / name).rename(original)
        (directory / name).symlink_to(original)
    objects = retained_objects(repo, sha)
    result = assert_retained_count(repo, retained=0, unowned=1)
    assert f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}" not in result.stderr
    assert retained_objects(repo, sha) == objects


def test_registered_path_replaced_with_another_repository_is_unowned(
    repo: Path,
    peer: Path,
    tmp_path: Path,
) -> None:
    sha = saved(peer)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "-b", "development")
    (peer / ".git").write_text(f"gitdir: {foreign / '.git'}\n")
    before = (peer / ".git").read_bytes()
    assert_retained_count(repo, retained=0, unowned=1)
    assert (peer / ".git").read_bytes() == before
    assert git(repo, "rev-parse", "refs/stash").stdout.strip() == sha


def test_duplicate_peer_labels_still_count_as_unknown(repo: Path, peer: Path) -> None:
    first = saved(peer)
    (peer / "owned.txt").write_text("competing checkpoint\n")
    (peer / "new.txt").write_text("competing untracked checkpoint\n")
    subject = git(repo, "show", "-s", "--format=%s", first).stdout.strip()
    message = subject.split(": ", 1)[-1]
    git(peer, "stash", "push", "-u", "-m", message, "--", "owned.txt", "new.txt")
    second = git(repo, "rev-parse", "refs/stash").stdout.strip()
    objects = {item: retained_objects(repo, item) for item in (first, second)}
    assert_retained_count(repo, retained=1, unowned=1)
    for item, content in objects.items():
        assert retained_objects(repo, item) == content


@pytest.mark.parametrize("scope", ("../owned.txt", "owned.txt,owned.txt", "outside.txt", "owned.txt,new.txt"))
def test_forged_peer_scope_is_unowned_and_retains_all_objects(
    repo: Path,
    peer: Path,
    scope: str,
) -> None:
    original = saved(peer)
    identity = hashlib.sha256(os.fsencode(peer)).hexdigest()
    message = f"gludd-owned-checkpoint label={LABEL}-forged owner={identity} files={scope}"
    arguments = ["commit-tree", f"{original}^{{tree}}", "-m", message]
    parents = git(repo, "rev-list", "--parents", "-n", "1", original).stdout.split()[1:]
    for parent in parents:
        arguments.extend(("-p", parent))
    forged = git(repo, *arguments).stdout.strip()
    git(repo, "update-ref", "--create-reflog", "-m", message, "refs/stash", forged, original)
    objects = {item: retained_objects(repo, item) for item in (original, forged)}
    assert_retained_count(repo, retained=1, unowned=1)
    for item, content in objects.items():
        assert retained_objects(repo, item) == content


def test_reflog_only_peer_ownership_claim_does_not_verify_an_ordinary_stash(repo: Path, peer: Path) -> None:
    (repo / "owned.txt").write_text("ordinary preserved version\n")
    git(repo, "stash", "push", "-m", "ordinary unknown", "--", "owned.txt")
    ordinary = git(repo, "rev-parse", "refs/stash").stdout.strip()
    saved(peer)
    identity = hashlib.sha256(os.fsencode(peer)).hexdigest()
    message = f"gludd-owned-checkpoint label={LABEL}-forged owner={identity} files=owned.txt"
    git(repo, "stash", "store", "-m", message, ordinary)
    before = retained_objects(repo, ordinary)
    assert_retained_count(repo, retained=1, unowned=2)
    assert retained_objects(repo, ordinary) == before


def test_peer_metadata_cannot_admit_captured_symlinks(repo: Path, peer: Path) -> None:
    identity = hashlib.sha256(os.fsencode(peer)).hexdigest()
    message = f"gludd-owned-checkpoint label={LABEL} owner={identity} files=link.txt"
    (peer / "link.txt").symlink_to("owned.txt")
    git(peer, "stash", "push", "-u", "-m", message, "--", "link.txt")
    sha = git(repo, "rev-parse", "refs/stash").stdout.strip()
    objects = retained_objects(repo, sha)
    result = assert_retained_count(repo, retained=0, unowned=1)
    assert "captures a symlink or submodule" in result.stderr
    assert retained_objects(repo, sha) == objects


@pytest.mark.parametrize("failure", ("lookup", "unterminated", "relative", "duplicate"))
def test_failed_or_malformed_native_registration_refuses_without_mutation(
    repo: Path,
    peer: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    sha = saved(peer)
    objects = retained_objects(repo, sha)
    before = snapshot(repo), snapshot(peer)
    module = loaded_owner()
    native_git = module.git

    def failed_inventory(root: Path, *args: str, optional: bool = False) -> str:
        output = str(native_git(root, *args, optional=optional))
        if args != ("worktree", "list", "--porcelain", "-z"):
            return output
        if failure == "lookup":
            raise module.Refused("native worktree inventory lookup failed")
        if failure == "unterminated":
            return output[:-1]
        if failure == "relative":
            return "worktree relative-root\0\0"
        return output + output

    monkeypatch.setattr(module, "git", failed_inventory)
    with pytest.raises(module.Refused):
        module.count_leaks(repo.resolve())
    assert "OWNED-CHECKPOINT-COUNT" not in capsys.readouterr().err
    assert (snapshot(repo), snapshot(peer)) == before
    assert retained_objects(repo, sha) == objects


@pytest.mark.parametrize("change", ("stash", "worktree"))
def test_competing_native_inventory_change_refuses_a_count_receipt(
    repo: Path,
    peer: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: str,
) -> None:
    original = saved(peer)
    objects = retained_objects(repo, original)
    module = loaded_owner()
    native_git = module.git
    injected = False
    expected: tuple[str, str, str, dict[str, bytes]] | None = None

    def competing(root: Path, *args: str, optional: bool = False) -> str:
        nonlocal injected, expected
        output = str(native_git(root, *args, optional=optional))
        if args == ("stash", "list", "--format=%H%x00%s%x00") and not injected:
            injected = True
            if change == "stash":
                (repo / "outside.txt").write_text("competing retained version\n")
                git(repo, "stash", "push", "-m", "competing owner", "--", "outside.txt")
            else:
                linked(repo, tmp_path / "competing worktree", "competing-worktree")
            expected = snapshot(repo)
        return output

    monkeypatch.setattr(module, "git", competing)
    with pytest.raises(module.Refused, match="inventory changed"):
        module.count_leaks(repo.resolve())
    assert injected
    assert "OWNED-CHECKPOINT-COUNT" not in capsys.readouterr().err
    assert snapshot(repo) == expected
    assert retained_objects(repo, original) == objects
