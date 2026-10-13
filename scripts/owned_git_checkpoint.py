#!/usr/bin/env python3
"""Guard explicit owned files around native Git stash checkpoint operations."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
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


def count_leaks(root: Path) -> int:
    """Count unknown stashes while verifying retained checkpoints without mutation."""
    retained = unowned = 0
    labels: set[str] = set()
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
        label, _identity, scope = match.groups()
        files = scope.split(",")
        try:
            if (files != sorted(set(files)) or label in labels
                    or any(not re.fullmatch(r"[A-Za-z0-9_./+@-]+", name)
                           or any(part in {"", ".", "..", ".git"} for part in name.split("/"))
                           for name in files)
                    or metadata(root, files, label) != message):
                raise Refused("foreign or nonliteral owned checkpoint metadata")
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
        except Refused as exc:
            unowned += 1
            print(f"OWNED-CHECKPOINT-UNOWNED checkpoint_sha={sha} reason={exc}", file=sys.stderr, flush=True)
            continue
        labels.add(label)
        retained += 1
        print(f"OWNED-CHECKPOINT-VERIFIED checkpoint_sha={sha}", file=sys.stderr, flush=True)
    print(f"OWNED-CHECKPOINT-COUNT retained={retained} unowned={unowned}", file=sys.stderr, flush=True)
    return unowned


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
    parser.add_argument("action", choices=("push", "restore", "count-leaks"))
    args = parser.parse_args()
    try:
        if args.action == "count-leaks":
            print(count_leaks(repository_root()), flush=True)
            return 0
        validate = os.environ.get("GIT_CHECKPOINT_VALIDATE_ONLY", "1")
        if validate not in {"0", "1"}:
            raise Refused("GIT_CHECKPOINT_VALIDATE_ONLY must be 0 or 1")
        root, files = root_and_files(os.environ.get("GIT_CHECKPOINT_FILES", ""), restore=args.action == "restore")
        label = os.environ.get("GIT_CHECKPOINT_LABEL", "")
        if args.action == "push":
            push(root, files, label, validate=validate == "1")
        else:
            restore(root, files, label, os.environ.get("GIT_CHECKPOINT_SHA", ""), validate=validate == "1")
    except (Refused, OSError, subprocess.TimeoutExpired) as exc:
        print(f"OWNED-CHECKPOINT-REFUSED {exc}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
