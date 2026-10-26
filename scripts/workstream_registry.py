#!/usr/bin/env python3
"""Maintain the project-scoped registry of active logical workstreams.

The registry is shared by every Git worktree for one checkout.  It records
logical model-agent ownership, which intentionally cannot be inferred from OS
PIDs, so cleanup jobs do not reclaim a clean worktree that is still in use.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import owned_git_checkpoint
else:
    from scripts import owned_git_checkpoint

ROOT = Path(__file__).resolve().parent.parent
_VERSION = 1


def default_registry_path(root: Path = ROOT) -> Path:
    """Return one namespaced registry path shared by all project worktrees."""
    configured = os.environ.get("GLUDD_ACTIVE_WORKSTREAM_REGISTRY", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    common_dir = Path(result.stdout.strip()).resolve()
    namespace = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()[:12]
    temp_root = Path(os.environ.get("TMPDIR", "/tmp")).expanduser()
    return temp_root / "gludd-active-workstreams" / f"{namespace}.json"


def _validate_branch(branch: str) -> str:
    value = branch.strip()
    invalid = (
        not value
        or value.startswith("-")
        or value.endswith(("/", "."))
        or ".." in value
        or "//" in value
        or "@{" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    )
    if invalid:
        raise ValueError(f"invalid workstream branch: {branch!r}")
    return value



def _validate_release_context(branch: str, worktree: Path, expected_head: str) -> None:
    """Require the literal identity supplied to the existing release owner."""
    if (_validate_branch(branch) != branch or not worktree.is_absolute()
            or ".." in worktree.parts
            or any(character in branch + str(worktree) for character in "\0\r\n")
            or re.fullmatch(r"[0-9a-f]{40}", expected_head) is None):
        raise ValueError("literal BRANCH, canonical absolute WORKTREE and full immutable HEAD are required")


def _release_identity(branch: str, worktree: Path, expected_head: str) -> tuple[object, ...]:
    """Bind one registered native owner without inspecting peer materializations."""
    if not worktree.is_dir() or worktree.resolve(strict=True) != worktree:
        raise ValueError("WORKTREE must be a canonical registered checkout")
    root = owned_git_checkpoint.repository_root()

    def git(path: Path, *arguments: str) -> str:
        return owned_git_checkpoint.git(path, "--no-optional-locks", *arguments).strip()

    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    inventory = owned_git_checkpoint.git(root, "--no-optional-locks", "worktree", "list", "--porcelain", "-z")
    if not inventory.endswith("\0\0"):
        raise ValueError("malformed native worktree registration")
    matches = [
        (position, fields) for position, block in enumerate(inventory[:-2].split("\0\0"))
        if (fields := block.split("\0"))[0] == f"worktree {worktree}"
    ]
    if len(matches) != 1:
        raise ValueError("WORKTREE has no unique native registration")
    position, fields = matches[0]
    if (f"branch refs/heads/{branch}" not in fields or f"HEAD {expected_head}" not in fields
            or any(field == "prunable" or field.startswith("prunable ") for field in fields)):
        raise ValueError("registered workstream BRANCH or HEAD does not match")
    owned_git_checkpoint.verify_registered_root(worktree, main=position == 0, common=common)
    if (git(worktree, "symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{branch}"
            or git(worktree, "rev-parse", "--verify", "HEAD^{commit}") != expected_head):
        raise ValueError("native workstream BRANCH or HEAD changed")
    owner, backlink = worktree.lstat(), (worktree / ".git").lstat()
    if (not stat.S_ISDIR(owner.st_mode)
            or not (stat.S_ISDIR(backlink.st_mode) if position == 0 else stat.S_ISREG(backlink.st_mode))):
        raise ValueError("native workstream identity is not owned")
    return (
        common, owner.st_dev, owner.st_ino, backlink.st_dev, backlink.st_ino,
        backlink.st_mtime_ns, backlink.st_size, branch, expected_head,
    )


class WorkstreamRegistry:
    """Concurrency-safe active-workstream registry."""

    def __init__(self, path: Path) -> None:
        """Create a registry backed by *path*."""
        self.path = path
        self.lock_path = path.with_suffix(f"{path.suffix}.lock")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": _VERSION, "workstreams": {}}
        try:
            payload = cast(
                dict[str, Any],
                json.loads(self.path.read_text(encoding="utf-8")),
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid active-workstream registry: {self.path}") from exc
        if payload.get("version") != _VERSION or not isinstance(payload.get("workstreams"), dict):
            raise ValueError(f"unsupported active-workstream registry schema: {self.path}")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, self.path)

    def register(self, branch: str, worktree: Path) -> None:
        """Register or renew one physical owner, refusing active path reassignment."""
        branch = _validate_branch(branch)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = self._read()
            workstreams = payload["workstreams"]
            requested_path = worktree.expanduser().resolve()
            existing = workstreams.get(branch)
            if isinstance(existing, dict) and existing.get("status") == "active":
                retained_path = existing.get("worktree")
                if (
                    not isinstance(retained_path, str)
                    or not retained_path
                    or Path(retained_path).expanduser().resolve() != requested_path
                ):
                    raise ValueError(
                        f"active workstream path mismatch for {branch!r}: retained "
                        f"{retained_path!r}, requested {str(requested_path)!r}; reconcile "
                        "the existing logical owner before explicit registration; "
                        "do not force-remove or unregister it"
                    )
            if isinstance(existing, dict) and existing.get("status") == "released":
                current_head = owned_git_checkpoint.git(
                    requested_path, "--no-optional-locks", "rev-parse", "--verify", "HEAD^{commit}",
                ).strip()
                _release_identity(branch, requested_path, current_head)
            workstreams[branch] = {
                "branch": branch, "status": "active", "updated_epoch": int(time.time()),
                "worktree": str(requested_path),
            }
            self._write(payload)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def ownership_status(self, branch: str, worktree: Path, expected_head: str) -> str:
        """Read an exact native-owner tuple; absence never establishes release."""
        _validate_release_context(branch, worktree, expected_head)
        return str(self._matching_entry(self._read(), branch, worktree, expected_head)["status"])

    @staticmethod
    def _matching_entry(
        payload: dict[str, Any], branch: str, worktree: Path, expected_head: str,
    ) -> dict[str, Any]:
        entry = payload["workstreams"].get(branch)
        if (not isinstance(entry, dict) or entry.get("branch") != branch
                or entry.get("worktree") != str(worktree)
                or entry.get("status") not in {"active", "released"}
                or not isinstance(entry.get("updated_epoch"), int)
                or isinstance(entry.get("updated_epoch"), bool)):
            raise ValueError("matching native workstream ownership is required")
        if entry["status"] == "released" and entry.get("head") != expected_head:
            raise ValueError("released workstream HEAD does not match the immutable owner")
        return cast(dict[str, Any], entry)

    def unregister(
        self, branch: str, worktree: Path, expected_head: str,
    ) -> dict[str, str | int]:
        """Atomically release only the exact registered path and immutable HEAD."""
        _validate_release_context(branch, worktree, expected_head)
        self.ownership_status(branch, worktree, expected_head)
        initial = _release_identity(branch, worktree, expected_head)
        # Unknown/missing owners above must not create a registry or a lock file.
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = self._read()
            entry = self._matching_entry(payload, branch, worktree, expected_head)
            if _release_identity(branch, worktree, expected_head) != initial:
                raise ValueError("native workstream identity changed before release")
            if entry["status"] != "released":
                entry.update(status="released", head=expected_head)
                self._write(payload)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return {
            "branch": branch, "worktree": str(worktree), "head": expected_head,
            "status": "released", "native_ownership_verified": 1,
        }

    def active_branches(self) -> frozenset[str]:
        """Return active branch names, failing closed on corrupt state."""
        payload = self._read()
        return frozenset(
            branch
            for branch, entry in payload["workstreams"].items()
            if isinstance(entry, dict) and entry.get("status") == "active"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "unregister", "list"))
    parser.add_argument("--branch")
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--expected-head", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the registry command-line interface."""
    args = _parser().parse_args(argv)
    registry = WorkstreamRegistry(args.registry or default_registry_path())
    if args.action == "list":
        print(json.dumps(sorted(registry.active_branches())))
        return 0
    if not args.branch:
        raise SystemExit("--branch is required")
    if args.action == "register":
        if args.worktree is None:
            raise SystemExit("--worktree is required for register")
        registry.register(args.branch, args.worktree)
        print(f"registered active workstream: {args.branch} ({args.worktree})")
    else:
        if args.worktree is None:
            raise SystemExit("--worktree is required for unregister")
        try:
            receipt = registry.unregister(args.branch, args.worktree, args.expected_head)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"workstream-unregister: release refused: {exc}")
            return 2
        print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
