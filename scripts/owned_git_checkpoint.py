#!/usr/bin/env python3
"""Guard explicit owned files around native Git stash checkpoint operations."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


class Refused(ValueError):
    """An ownership or native Git prerequisite did not hold."""


def git(root: Path, *args: str, optional: bool = False) -> str:
    """Use native Git without interpreting supplied file names as pathspecs."""
    result = subprocess.run(
        ["git", "--literal-pathspecs", *args], cwd=root,
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode and not optional:
        raise Refused(f"native git {' '.join(args[:3])}: {(result.stderr or result.stdout).strip()}")
    return result.stdout if result.returncode == 0 else ""


def names(output: str) -> set[str]:
    """Decode the native NUL-delimited path list without shell expansion."""
    return set(output.rstrip("\0").split("\0")) if output else set()


def repository_root() -> Path:
    """Require the invoking repository's native, unredirected named-branch state."""
    redirected = {
        "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    }
    if any(os.environ.get(key) for key in redirected):
        raise Refused("redirected Git state is not an owned checkpoint")
    cwd = Path.cwd().resolve()
    root = Path(git(cwd, "rev-parse", "--show-toplevel").strip()).resolve()
    if root != cwd:
        raise Refused("invoke the checkpoint owner at the repository root")
    git(root, "rev-parse", "--verify", "HEAD")
    if not git(root, "branch", "--show-current").strip():
        raise Refused("a named branch is required")
    return root


def root_and_files(raw: str, *, restore: bool) -> tuple[Path, list[str]]:
    """Refuse broad, missing, redirected, symlinked and nonliteral file scope."""
    root = repository_root()
    files = raw.split()
    if not files or len(files) != len(set(files)):
        raise Refused("FILES must name nonempty distinct explicit files")
    for name in files:
        parts = name.split("/")
        if (not re.fullmatch(r"[A-Za-z0-9_./+@-]+", name)
                or any(part in {"", ".", "..", ".git"} for part in parts)):
            raise Refused(f"unsafe nonliteral file: {name}")
        path = root / name
        if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
            raise Refused(f"symlink file or parent: {name}")
        if path.is_dir():
            raise Refused(f"directory scope is not permitted: {name}")
        staged = git(root, "ls-files", "--stage", "-z", "--", name)
        if staged.startswith("160000 "):
            raise Refused(f"submodule scope is not permitted: {name}")
        if (not restore and not path.is_file()
                and name not in names(git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", name))):
            raise Refused(f"missing untracked file: {name}")
    return root, sorted(files)


def dirty(root: Path) -> set[str]:
    """Ask Git for both tracked and untracked changed paths."""
    return names(git(root, "diff", "--no-renames", "--name-only", "-z", "HEAD")) | names(
        git(root, "ls-files", "--others", "--exclude-standard", "-z")
    )


def metadata(root: Path, files: list[str], label: str) -> str:
    """Keep ownership and explicit scope in the native human-readable stash message."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{7,79}", label):
        raise Refused("label must be a unique 8..80-character lowercase project label")
    identity = hashlib.sha256(os.fsencode(root)).hexdigest()
    return f"gludd-owned-checkpoint label={label} owner={identity} files={','.join(files)}"


def top(root: Path) -> str:
    """Return the exact native top stash object or the empty first-checkpoint state."""
    return git(root, "rev-parse", "--verify", "refs/stash", optional=True).strip()


def checkpoint_paths(root: Path, sha: str) -> set[str]:
    """Validate captured scope using the real stash parent trees, not a separate manifest."""
    result = names(git(root, "diff", "--no-renames", "--name-only", "-z", f"{sha}^1", sha))
    result |= names(git(root, "diff", "--no-renames", "--name-only", "-z", f"{sha}^1", f"{sha}^2"))
    parents = git(root, "rev-list", "--parents", "-n", "1", sha).split()
    if len(parents) == 4:
        result |= names(git(root, "ls-tree", "-r", "--name-only", "-z", f"{sha}^3"))
    return result


def registered_roots(inventory: str) -> dict[str, tuple[Path, bool]]:
    """Resolve identities only from Git's NUL-delimited live worktree registry."""
    if not inventory.endswith("\0\0"):
        raise Refused("malformed native worktree inventory; no checkpoints changed")
    roots: dict[str, tuple[Path, bool]] = {}
    seen: set[Path] = set()
    for position, record in enumerate(inventory[:-2].split("\0\0")):
        fields = record.split("\0")
        if not fields[0].startswith("worktree ") or not fields[0][9:]:
            raise Refused("malformed native worktree inventory; no checkpoints changed")
        path = Path(fields[0][9:])
        if not path.is_absolute() or path in seen:
            raise Refused("nonliteral or duplicate native worktree registration")
        seen.add(path)
        if any(field == "prunable" or field.startswith("prunable ") for field in fields[1:]):
            continue
        try:
            if (path.resolve(strict=True) != path or not path.is_dir()
                    or (path / ".git").is_symlink()):
                continue
        except (OSError, RuntimeError):
            continue
        identity = hashlib.sha256(os.fsencode(path)).hexdigest()
        roots[identity] = path, position == 0
    return roots


def verify_registered_root(owner: Path, *, main: bool, common: Path) -> None:
    """Require the registered root's actual common directory and native backlink."""
    if (Path(git(owner, "rev-parse", "--show-toplevel").strip()) != owner
            or Path(git(owner, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) != common):
        raise Refused("registered checkpoint owner belongs to another repository")
    directory = Path(git(owner, "rev-parse", "--absolute-git-dir").strip())
    if directory.resolve(strict=True) != directory:
        raise Refused("symlinked native owner administration directory")
    if main:
        if directory != common:
            raise Refused("registered main checkpoint owner has a foreign native directory")
    elif (directory.parent != common / "worktrees"
            or any((directory / name).is_symlink() for name in ("gitdir", "commondir"))
            or Path((directory / "gitdir").read_text().removesuffix("\n")) != owner / ".git"):
        raise Refused("registered checkpoint owner has a stale or foreign native backlink")


def verify_checkpoint_claim(owner: Path, files: list[str], label: str, message: str) -> None:
    """Retain the existing literal owned scope and exact root-bound message."""
    if (files != sorted(set(files))
            or any(not re.fullmatch(r"[A-Za-z0-9_./+@-]+", name)
                   or any(part in {"", ".", "..", ".git"} for part in name.split("/"))
                   for name in files)
            or metadata(owner, files, label) != message):
        raise Refused("foreign or nonliteral owned checkpoint metadata")


def verify_checkpoint_content(root: Path, sha: str, message: str, files: list[str]) -> None:
    """Validate the existing native stash topology, scope and captured file modes."""
    if git(root, "show", "-s", "--format=%s", sha).strip().split(": ", 1)[-1] != message:
        raise Refused("native stash commit differs from its inventory ownership claim")
    parents = git(root, "rev-list", "--parents", "-n", "1", sha).split()
    if len(parents) not in {3, 4}:
        raise Refused("owned checkpoint has no native stash parent topology")
    index_parents = git(root, "rev-list", "--parents", "-n", "1", parents[2]).split()
    if index_parents != [parents[2], parents[1]] or checkpoint_paths(root, sha) != set(files):
        raise Refused("owned checkpoint native index or captured scope differs")
    if (len(parents) == 4
            and git(root, "rev-list", "--parents", "-n", "1", parents[3]).split() != [parents[3]]):
        raise Refused("owned checkpoint untracked parent is not a native root commit")
    for tree in (parents[1], sha, *parents[2:]):
        entries = git(root, "ls-tree", "-r", "-z", tree)
        for entry in entries.rstrip("\0").split("\0") if entries else []:
            mode, name = entry.split(" ", 1)[0], entry.split("\t", 1)[-1]
            if name in files and mode not in {"100644", "100755"}:
                raise Refused("owned checkpoint captures a symlink or submodule")


RepairFileState = tuple[int, int, int, int, bytes]
RepairState = tuple[str, str, str, tuple[RepairFileState, ...]]


def _repair_file_state(path: Path) -> RepairFileState:
    """Read only one regular metadata file without following its final link."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise Refused("checkpoint repair metadata is not a regular file")
        content = stream.read()
        after = os.fstat(stream.fileno())
    before_identity = before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns
    after_identity = after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns
    if before_identity != after_identity or before.st_size != len(content):
        raise Refused("checkpoint repair metadata changed during inspection")
    return *before_identity, content


def _repair_link_state(
    root: Path, worktree: Path, administration: Path, sha: str, head: str, branch: str,
) -> RepairState:
    """Bind the selected missing link to fresh native ownership and immutable state."""
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    if (common.resolve(strict=True) != common
            or administration.resolve(strict=True) != administration
            or administration.parent != common / "worktrees"
            or not administration.is_dir()):
        raise Refused("checkpoint repair requires one canonical native worktree administration directory")
    states = tuple(_repair_file_state(administration / name) for name in (
        "gitdir", "commondir", "HEAD", "index", "locked",
    ))
    if (states[0][-1] != os.fsencode(worktree / ".git") + b"\n"
            or states[1][-1] != b"../..\n"
            or states[2][-1] != f"ref: refs/heads/{branch}\n".encode()
            or states[4][-1] != f"checkpoint-{sha}\n".encode()):
        raise Refused("checkpoint repair native backlink, common link, HEAD or lock differs")
    inventory = git(root, "worktree", "list", "--porcelain", "-z")
    identity = hashlib.sha256(os.fsencode(worktree)).hexdigest()
    if registered_roots(inventory).get(identity) != (worktree, False):
        raise Refused("checkpoint repair owner is not the exact live registered linked worktree")
    record = next(
        fields for entry in inventory[:-2].split("\0\0")
        if (fields := entry.split("\0"))[0] == f"worktree {worktree}"
    )
    if (f"HEAD {head}" not in record or f"branch refs/heads/{branch}" not in record
            or f"locked checkpoint-{sha}" not in record):
        raise Refused("checkpoint repair HEAD, branch or retained checkpoint lock differs")
    prefix = f"--git-dir={administration}"
    if (git(root, prefix, "rev-parse", "--verify", "HEAD").strip() != head
            or git(root, prefix, "symbolic-ref", "HEAD").strip() != f"refs/heads/{branch}"
            or Path(git(root, prefix, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) != common):
        raise Refused("checkpoint repair native administration identity differs")
    if any(os.path.lexists(path) for path in (
        administration / "index.lock", common / "refs" / "stash.lock",
        common / "refs" / "heads" / f"{branch}.lock", common / "packed-refs.lock",
    )):
        raise Refused("checkpoint repair refuses an active native index or ref lock")
    records = git(root, "stash", "list", "--format=%H%x00%s%x00")
    selected = [record for record in records.splitlines() if record.split("\0", 1)[0] == sha]
    if len(selected) != 1:
        raise Refused("checkpoint repair requires the exact unique retained stash SHA")
    fields = selected[0].split("\0")
    if len(fields) != 3 or fields[-1]:
        raise Refused("checkpoint repair retained stash inventory is malformed")
    message = fields[1].split(": ", 1)[-1]
    match = re.fullmatch(r"gludd-owned-checkpoint label=(\S+) owner=([0-9a-f]{64}) files=(\S+)", message)
    if match is None or match.group(2) != identity:
        raise Refused("checkpoint repair stash belongs to a different owner")
    label, _, scope = match.groups()
    files = scope.split(",")
    verify_checkpoint_claim(worktree, files, label, message)
    verify_checkpoint_content(root, sha, message, files)
    return inventory, records, git(root, "show-ref", "--head"), states


def repair_link(
    root: Path, worktree_raw: str, administration_raw: str,
    sha: str, head: str, branch: str, *, validate: bool,
) -> None:
    """Recreate only a proven absent checkpoint-owner pointer without replacement."""
    if (not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head)):
        raise Refused("checkpoint repair requires full exact checkpoint and expected HEAD SHAs")
    git(root, "check-ref-format", "--branch", branch)
    worktree, administration = Path(worktree_raw), Path(administration_raw)
    if any(
        not raw or not path.is_absolute() or ".." in path.parts
        or any(ord(character) < 32 for character in raw)
        for raw, path in ((worktree_raw, worktree), (administration_raw, administration))
    ):
        raise Refused("checkpoint repair requires literal canonical absolute paths")
    if worktree.resolve(strict=True) != worktree or not worktree.is_dir():
        raise Refused("checkpoint repair requires the existing canonical owner directory")
    if os.path.lexists(worktree / ".git"):
        raise Refused("checkpoint repair refuses an existing file, directory or symlink at .git")
    descriptor = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        directory = os.fstat(descriptor)
        state = _repair_link_state(root, worktree, administration, sha, head, branch)
        if validate:
            print(
                f"OWNED-CHECKPOINT-LINK-PLAN checkpoint_sha={sha} "
                "native_ownership_verified=1 writes=0 gate_evidence=false release_ready=false",
                flush=True,
            )
            print(
                "AGENT_NEXT target=git-owned-checkpoint-repair-link outcome=planned "
                "writes=0 gate_evidence=false release_ready=false; only selected native metadata "
                "was verified; Root may request literal GIT_CHECKPOINT_VALIDATE_ONLY=0 after "
                "source acceptance; no repair, commit or release proof",
                file=sys.stderr, flush=True,
            )
            return
        if _repair_link_state(root, worktree, administration, sha, head, branch) != state:
            raise Refused("checkpoint repair native identity changed before pointer creation")
        current = worktree.stat()
        if (current.st_dev, current.st_ino) != (directory.st_dev, directory.st_ino):
            raise Refused("checkpoint repair owner directory changed before pointer creation")
        # O_EXCL and the held canonical-directory descriptor prevent replacement.
        pointer = os.open(
            ".git", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644, dir_fd=descriptor,
        )
        created = True
        with os.fdopen(pointer, "wb") as stream:
            stream.write(os.fsencode(f"gitdir: {administration}\n"))
            stream.flush()
            os.fsync(stream.fileno())
        common = administration.parent.parent
        verify_registered_root(worktree, main=False, common=common)
        if (Path(git(worktree, "rev-parse", "--absolute-git-dir").strip()) != administration
                or _repair_link_state(root, worktree, administration, sha, head, branch) != state):
            raise Refused("checkpoint repair state changed; preserve the pointer and reconcile native evidence")
        print(
            f"OWNED-CHECKPOINT-LINK-REPAIRED checkpoint_sha={sha} "
            "checkpoint_retained=1 scope=selected-missing-link gate_evidence=false release_ready=false",
            flush=True,
        )
        print(
            "AGENT_NEXT target=git-owned-checkpoint-repair-link outcome=passed "
            "next_owner=owned-checkpoint-count-leaks; inspect fresh retained/unowned counts "
            "before the normal guarded commit; gate and exact-SHA proofs remain pending",
            file=sys.stderr, flush=True,
        )
    except (Refused, OSError, subprocess.TimeoutExpired) as exc:
        if created:
            raise Refused(
                f"{exc}; selected pointer may have been created at {worktree / '.git'}; "
                "preserve it and reconcile exact native identity before retry"
            ) from exc
        raise
    finally:
        os.close(descriptor)


def _checkpoint_inventory(root: Path) -> tuple[int, frozenset[Path]]:
    """Validate one fresh native inventory and preserve its existing diagnostics."""
    retained = unowned = 0
    retained_owners: set[Path] = set()
    labels: set[str] = set()
    worktrees = git(root, "worktree", "list", "--porcelain", "-z")
    owners = registered_roots(worktrees)
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    records = git(root, "stash", "list", "--format=%H%x00%s%x00")
    for record in records.splitlines():
        fields = record.split("\0")
        if len(fields) != 3 or fields[-1] or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]):
            raise Refused("malformed native stash inventory; no checkpoints changed")
        sha, subject, _ = fields
        message = subject.split(": ", 1)[-1]
        match = re.fullmatch(r"gludd-owned-checkpoint label=(\S+) owner=([0-9a-f]{64}) files=(\S+)", message)
        if not match:
            unowned += 1
            continue
        label, identity, scope = match.groups()
        files = scope.split(",")
        try:
            if identity not in owners:
                raise Refused("checkpoint owner is not a live registered worktree")
            owner, is_main = owners[identity]
            if label in labels:
                raise Refused("foreign or nonliteral owned checkpoint metadata")
            verify_checkpoint_claim(owner, files, label, message)
            verify_registered_root(owner, main=is_main, common=common)
            verify_checkpoint_content(root, sha, message, files)
        except Refused as exc:
            unowned += 1
            print(f"OWNED-CHECKPOINT-UNOWNED checkpoint_sha={sha} reason={exc}", file=sys.stderr, flush=True)
            continue
        labels.add(label)
        retained_owners.add(owner)
        retained += 1
        print(f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}", file=sys.stderr, flush=True)
    if (git(root, "stash", "list", "--format=%H%x00%s%x00") != records
            or git(root, "worktree", "list", "--porcelain", "-z") != worktrees):
        raise Refused("native checkpoint inventory changed during inspection; retry after writers finish")
    print(f"OWNED-CHECKPOINT-COUNT retained={retained} unowned={unowned}", file=sys.stderr, flush=True)
    return unowned, frozenset(retained_owners)


def count_leaks(root: Path) -> int:
    """Count unknown stashes while verifying retained checkpoints without mutation."""
    return _checkpoint_inventory(root)[0]


def retained_checkpoint_owners(root: Path) -> frozenset[Path]:
    """Return verified retained owners, refusing any unknown checkpoint authority."""
    unowned, owners = _checkpoint_inventory(root)
    if unowned:
        raise Refused("unowned checkpoints prevent worktree retirement")
    return owners


def push(root: Path, files: list[str], label: str, *, validate: bool) -> None:
    """Checkpoint only an explicit owned scope; native Git preserves its index."""
    message = metadata(root, files, label)
    if validate:
        print(
            f"OWNED-CHECKPOINT-PLAN action=push files={len(files)} label={label} native_ownership_verified=0",
            flush=True,
        )
        return
    records = git(root, "stash", "list", "--format=%H%x00%s%x00")
    if f"gludd-owned-checkpoint label={label} " in records:
        raise Refused("checkpoint label already exists; select a new unique label")
    staged = names(git(root, "diff", "--cached", "--no-renames", "--name-only", "-z"))
    if staged - set(files):
        raise Refused("staged changes outside explicit FILES would be captured by native stash")
    deleted = names(git(root, "diff", "--cached", "--no-renames", "--diff-filter=D", "--name-only", "-z"))
    if deleted & set(files):
        raise Refused("unsupported staged deletion: native scoped stash cannot preserve this index safely")
    if set(files) - dirty(root):
        raise Refused("each explicit file must have an actual tracked or untracked change")
    if git(root, "ls-files", "--unmerged", "-z"):
        raise Refused("unmerged index cannot be checkpointed")
    prior = top(root)
    print(f"OWNED-CHECKPOINT-START action=push files={len(files)}", flush=True)
    git(root, "stash", "push", "--include-untracked", "--message", message, "--", *files)
    sha = top(root)
    if not sha or sha == prior or git(root, "show", "-s", "--format=%s", sha).strip().split(": ", 1)[-1] != message:
        raise Refused("native stash did not produce the expected owned checkpoint")
    if checkpoint_paths(root, sha) != set(files):
        raise Refused(f"captured scope mismatch; checkpoint retained at {sha}")
    if set(files) & dirty(root):
        raise Refused(f"owned changes remain; checkpoint retained at {sha}")
    print(f"OWNED-CHECKPOINT-SAVED checkpoint_sha={sha} label={label} files={len(files)}", flush=True)


def restore(root: Path, files: list[str], label: str, sha: str, *, validate: bool) -> None:
    """Apply the exact owned top stash with its index and retain recoverable evidence."""
    message = metadata(root, files, label)
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise Refused("restore requires a full exact Git object SHA, not a mutable ref")
    if validate:
        print(
            f"OWNED-CHECKPOINT-PLAN action=restore files={len(files)} label={label} native_ownership_verified=0",
            flush=True,
        )
        return
    if top(root) != sha:
        raise Refused("expected checkpoint SHA is not the current top stash")
    if git(root, "show", "-s", "--format=%s", sha).strip().split(": ", 1)[-1] != message:
        raise Refused("foreign checkpoint label, worktree identity or explicit scope")
    if checkpoint_paths(root, sha) != set(files):
        raise Refused("native checkpoint contains paths outside the explicit scope")
    if set(files) & dirty(root):
        raise Refused("current changes overlap owned checkpoint; both versions are preserved")
    if git(root, "diff", "--cached", "--name-only", "-z"):
        raise Refused("current staged work must be committed or independently checkpointed first")
    if git(root, "ls-files", "--unmerged", "-z"):
        raise Refused("unmerged index cannot be restored")
    print(f"OWNED-CHECKPOINT-START action=restore expected_sha={sha}", flush=True)
    # apply, never pop/drop/reset/checkout: a native conflict leaves the object recoverable.
    git(root, "stash", "apply", "--index", sha)
    if top(root) != sha:
        raise Refused("stash stack changed during restore; no checkpoint was deleted")
    print(f"OWNED-CHECKPOINT-RESTORED checkpoint_sha={sha} checkpoint_retained=1", flush=True)


def main() -> int:
    """Read Make-exported values without injecting untrusted values into a shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("push", "restore", "count-leaks", "repair-link"))
    args = parser.parse_args()
    try:
        if args.action == "count-leaks":
            print(count_leaks(repository_root()), flush=True)
            return 0
        validate_key = (
            "GLUDD_CHECKPOINT_REPAIR_VALIDATE_ONLY"
            if args.action == "repair-link" else "GIT_CHECKPOINT_VALIDATE_ONLY"
        )
        validate = os.environ.get(validate_key, "1")
        if validate not in {"0", "1"}:
            if args.action == "repair-link":
                raise Refused(f"{validate_key} (public GIT_CHECKPOINT_VALIDATE_ONLY) must be 0 or 1")
            raise Refused("GIT_CHECKPOINT_VALIDATE_ONLY must be 0 or 1")
        if args.action == "repair-link":
            repair_link(
                repository_root(), os.environ.get("GLUDD_CHECKPOINT_REPAIR_WORKTREE", ""),
                os.environ.get("GLUDD_CHECKPOINT_REPAIR_ADMIN_DIR", ""),
                os.environ.get("GLUDD_CHECKPOINT_REPAIR_SHA", ""),
                os.environ.get("GLUDD_CHECKPOINT_REPAIR_EXPECTED_HEAD", ""),
                os.environ.get("GLUDD_CHECKPOINT_REPAIR_EXPECTED_BRANCH", ""),
                validate=validate == "1",
            )
            return 0
        root, files = root_and_files(os.environ.get("GIT_CHECKPOINT_FILES", ""), restore=args.action == "restore")
        label = os.environ.get("GIT_CHECKPOINT_LABEL", "")
        if args.action == "push":
            push(root, files, label, validate=validate == "1")
        else:
            restore(root, files, label, os.environ.get("GIT_CHECKPOINT_SHA", ""), validate=validate == "1")
    except (Refused, OSError, subprocess.TimeoutExpired) as exc:
        print(f"OWNED-CHECKPOINT-REFUSED {exc}", file=sys.stderr, flush=True)
        if args.action == "repair-link":
            pointer = Path(os.environ.get("GLUDD_CHECKPOINT_REPAIR_WORKTREE", "")) / ".git"
            print(
                "AGENT_NEXT target=git-owned-checkpoint-repair-link outcome=refused "
                "native_status=2 gate_evidence=false release_ready=false "
                f"possible_pointer={str(pointer)!r}; retain the native reason and inspect "
                "exact selected link/admin/index/ref/stash state before a changed-input retry; "
                "never overwrite, remove, pop, drop or force unrelated state",
                file=sys.stderr, flush=True,
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
