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
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, cast

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

    def _mutate(self, branch: str, worktree: Path | None) -> None:
        branch = _validate_branch(branch)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = self._read()
            workstreams = payload["workstreams"]
            if worktree is None:
                workstreams.pop(branch, None)
            else:
                workstreams[branch] = {
                    "branch": branch,
                    "status": "active",
                    "updated_epoch": int(time.time()),
                    "worktree": str(worktree.expanduser().resolve()),
                }
            self._write(payload)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def register(self, branch: str, worktree: Path) -> None:
        """Register *branch* as an active logical workstream."""
        self._mutate(branch, worktree)

    def unregister(self, branch: str) -> None:
        """Remove *branch* only after explicit lifecycle completion."""
        self._mutate(branch, None)

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
        registry.unregister(args.branch)
        print(f"unregistered active workstream: {args.branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
