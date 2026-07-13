"""AG.9 — Checkpoint branching: forking and restoring from agent checkpoints.

Provides a lightweight checkpoint-branch layer for A/B execution paths.
Branches are stored as JSON blobs keyed by checkpoint id and branch name,
supporting creation, restoration, enumeration, side-by-side comparison,
and cleanup.

Design: metadata-only storage (no heavy LangGraph runtime dependency).
Branch payloads carry checkpoint state + lineage info.  The orchestrator
(re)plays a branch by loading its payload into a new graph invocation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CheckpointBranch:
    """A named branch created from a checkpoint's execution state.

    Each branch records its origin (checkpoint_id, parent_branch), the
    checkpoint state blob it was forked from, human-readable metadata, and
    a timestamp for ordering.
    """

    branch_id: str
    name: str
    checkpoint_id: str
    state: dict[str, Any] = field(default_factory=dict)
    parent_branch: str | None = None
    description: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class BranchResult:
    """Outcome of running a branch — the result of its A/B path."""

    branch_id: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0

    @property
    def is_success(self) -> bool:
        return self.status == "success"


class BranchManager:
    """Create, store, restore, and compare checkpoint branches.

    Branches are persisted as JSON files under *store_dir*, named
    ``<branch_id>.json``.  No external database dependency — the store is a
    plain directory.  Thread-safe at the file level (atomic writes via temp
    + replace).
    """

    def __init__(self, store_dir: str | Path = ".gludd/branches") -> None:
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── create / delete ──────────────────────────────────────────────────

    def create_branch(
        self,
        name: str,
        checkpoint_id: str,
        state: dict[str, Any],
        *,
        parent_branch: str | None = None,
        description: str = "",
    ) -> CheckpointBranch:
        branch = CheckpointBranch(
            branch_id=str(uuid.uuid4()),
            name=name,
            checkpoint_id=checkpoint_id,
            state=state,
            parent_branch=parent_branch,
            description=description,
        )
        self._write(branch)
        return branch

    def delete_branch(self, branch_id: str) -> bool:
        path = self._path(branch_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    # ── restore / list ───────────────────────────────────────────────────

    def restore_branch(self, branch_id: str) -> CheckpointBranch | None:
        return self._read(branch_id)

    def list_branches(self) -> list[CheckpointBranch]:
        branches: list[CheckpointBranch] = []
        for fp in sorted(self._dir.glob("*.json")):
            branch = self._read_from_path(fp)
            if branch is not None:
                branches.append(branch)
        return branches

    def get_branch(self, branch_id: str) -> CheckpointBranch | None:
        return self._read(branch_id)

    # ── comparison ────────────────────────────────────────────────────────

    def compare_branches(
        self,
        branch_ids: list[str],
    ) -> list[BranchResult]:
        results: list[BranchResult] = []
        for bid in branch_ids:
            branch = self._read(bid)
            if branch is None:
                results.append(BranchResult(
                    branch_id=bid, status="missing",
                    error=f"branch {bid} not found",
                ))
            else:
                results.append(BranchResult(
                    branch_id=bid, status="pending",
                    output=branch.state,
                ))
        return results

    # ── internal persistence ──────────────────────────────────────────────

    def _path(self, branch_id: str) -> Path:
        return self._dir / f"{branch_id}.json"

    def _write(self, branch: CheckpointBranch) -> None:
        path = self._path(branch.branch_id)
        tmp = path.with_name(path.name + ".tmp")
        data = {
            "branch_id": branch.branch_id,
            "name": branch.name,
            "checkpoint_id": branch.checkpoint_id,
            "state": branch.state,
            "parent_branch": branch.parent_branch,
            "description": branch.description,
            "created_at": branch.created_at,
        }
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    def _read(self, branch_id: str) -> CheckpointBranch | None:
        path = self._path(branch_id)
        if not path.exists():
            return None
        return self._read_from_path(path)

    @staticmethod
    def _read_from_path(path: Path) -> CheckpointBranch | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return CheckpointBranch(
            branch_id=data["branch_id"],
            name=data["name"],
            checkpoint_id=data["checkpoint_id"],
            state=data.get("state", {}),
            parent_branch=data.get("parent_branch"),
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
        )


# ── module-level convenience wrappers ────────────────────────────────────────

_default = BranchManager()


def create_branch(
    name: str,
    checkpoint_id: str,
    state: dict[str, Any],
    **kwargs: Any,
) -> CheckpointBranch:
    return _default.create_branch(name, checkpoint_id, state, **kwargs)


def restore_branch(branch_id: str) -> CheckpointBranch | None:
    return _default.restore_branch(branch_id)


def list_branches() -> list[CheckpointBranch]:
    return _default.list_branches()


def delete_branch(branch_id: str) -> bool:
    return _default.delete_branch(branch_id)
