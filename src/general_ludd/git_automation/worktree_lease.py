"""D-21: Git worktree lease tracking and expiry-safe cleanup.

Each worktree operation writes a JSON lease file with TTL, owner PID,
and branch identity. Expired leases are cleaned up; active leases block
cleanup of foreign worktrees.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_branch_component(branch: str) -> str:
    if ".." in branch or branch.startswith("/") or branch.startswith(".") or "/" in branch:
        raise ValueError(f"branch name would escape lease directory: {branch!r}")
    return branch


def _leases_dir(repo_path: str) -> Path:
    return Path(repo_path) / ".gludd" / "leases"


def write_worktree_lease(
    repo_path: str,
    branch: str,
    ttl_seconds: int,
) -> Path:
    safe = _safe_branch_component(branch)
    leases = _leases_dir(repo_path)
    leases.mkdir(parents=True, exist_ok=True)
    lease_path = leases / f"{safe}.lease.json"
    data = {
        "branch": branch,
        "owner_pid": os.getpid(),
        "created_at": time.time(),
        "ttl_seconds": ttl_seconds,
    }
    tmp = lease_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.chmod(0o600)
    tmp.rename(lease_path)
    return lease_path


def check_worktree_lease(repo_path: str, branch: str) -> bool:
    safe = _safe_branch_component(branch)
    lease_path = _leases_dir(repo_path) / f"{safe}.lease.json"
    try:
        raw = json.loads(lease_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    created = raw.get("created_at", 0)
    ttl = raw.get("ttl_seconds", 0)
    return not (ttl <= 0 or time.time() - created > ttl)


def release_worktree_lease(repo_path: str, branch: str) -> None:
    try:
        safe = _safe_branch_component(branch)
    except ValueError:
        return
    lease_path = _leases_dir(repo_path) / f"{safe}.lease.json"
    lease_path.unlink(missing_ok=True)


def cleanup_expired_leases(repo_path: str) -> int:
    leases = _leases_dir(repo_path)
    if not leases.is_dir():
        return 0
    removed = 0
    now = time.time()
    for entry in leases.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            raw = json.loads(entry.read_text())
            created = raw.get("created_at", 0)
            ttl = raw.get("ttl_seconds", 0)
            if ttl <= 0 or now - created > ttl:
                entry.unlink(missing_ok=True)
                removed += 1
        except (json.JSONDecodeError, KeyError):
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


def worktree_lease_info(repo_path: str) -> list[dict[str, object]]:
    leases = _leases_dir(repo_path)
    if not leases.is_dir():
        return []
    now = time.time()
    result: list[dict[str, object]] = []
    for entry in sorted(leases.iterdir()):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            raw = json.loads(entry.read_text())
            created = raw.get("created_at", 0)
            ttl = raw.get("ttl_seconds", 0)
            result.append(
                {
                    "branch": raw.get("branch", entry.stem.rsplit(".lease", 1)[0]),
                    "owner_pid": raw.get("owner_pid"),
                    "created_at": created,
                    "ttl_seconds": ttl,
                    "expired": (ttl <= 0 or now - created > ttl),
                }
            )
        except (json.JSONDecodeError, KeyError):
            result.append(
                {
                    "branch": entry.stem.rsplit(".lease", 1)[0],
                    "owner_pid": None,
                    "created_at": 0,
                    "ttl_seconds": 0,
                    "expired": True,
                }
            )
    return result
