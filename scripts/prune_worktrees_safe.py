#!/usr/bin/env python3
"""Prune clean Git worktrees without reclaiming active logical workstreams."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import owned_git_checkpoint
    from workstream_registry import WorkstreamRegistry, default_registry_path
else:
    from scripts import owned_git_checkpoint
    from scripts.workstream_registry import WorkstreamRegistry, default_registry_path


@dataclass(frozen=True)
class WorktreeRecord:
    """One parsed entry from ``git worktree list --porcelain``."""

    path: Path
    branch: str | None
    locked: bool
    prunable: bool = False


@dataclass(frozen=True)
class PruningDecision:
    """A deterministic decision for one worktree."""

    action: str
    reason: str


def parse_worktrees(output: str) -> list[WorktreeRecord]:
    """Parse Git's stable porcelain worktree format."""
    records: list[WorktreeRecord] = []
    for block in output.strip().split("\n\n"):
        fields = block.splitlines()
        path_line = next((line for line in fields if line.startswith("worktree ")), "")
        if not path_line:
            continue
        branch_line = next((line for line in fields if line.startswith("branch ")), "")
        branch = branch_line.removeprefix("branch refs/heads/") if branch_line else None
        records.append(
            WorktreeRecord(
                path=Path(path_line.removeprefix("worktree ")).resolve(),
                branch=branch,
                locked=any(line == "locked" or line.startswith("locked ") for line in fields),
                prunable=any(
                    line == "prunable" or line.startswith("prunable ")
                    for line in fields
                ),
            )
        )
    return records


def pruning_decision(
    record: WorktreeRecord,
    *,
    active_branches: frozenset[str],
    protected_paths: frozenset[Path],
) -> PruningDecision:
    """Classify a worktree without consulting mutable process state."""
    if record.path in protected_paths:
        return PruningDecision("protect", "current/main checkout")
    if record.locked:
        return PruningDecision("protect", "git worktree lock")
    if record.branch is not None and record.branch in active_branches:
        return PruningDecision("protect", "active logical workstream")
    return PruningDecision("remove", "unregistered clean candidate")


def _git(
    *args: str, check: bool = True, timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=Path.cwd(),
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def prune(*, registry_path: Path, validate_only: bool) -> int:
    """Protect active workstreams and remove only Git-confirmed clean candidates."""
    records = parse_worktrees(_git("worktree", "list", "--porcelain").stdout)
    if not records:
        print("wt-prune-safe: no worktrees registered")
        return 0
    current = Path(_git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    protected_paths = frozenset({records[0].path, current})
    active_branches = WorkstreamRegistry(registry_path).active_branches()
    decisions = [
        pruning_decision(
            record,
            active_branches=active_branches,
            protected_paths=protected_paths,
        )
        for record in records
    ]
    removable_prunable = [
        record
        for record, decision in zip(records, decisions, strict=True)
        if record.prunable and decision.action == "remove"
    ]
    protected_prunable = [
        record
        for record, decision in zip(records, decisions, strict=True)
        if record.prunable and decision.action == "protect"
    ]
    pruned_paths: frozenset[Path] = frozenset()
    if removable_prunable and not protected_prunable and not validate_only:
        prune_result = _git("worktree", "prune", "--expire", "now", check=False)
        if prune_result.returncode == 0:
            pruned_paths = frozenset(record.path for record in removable_prunable)
    removed = 0
    protected = 0
    retained = 0
    for record, decision in zip(records, decisions, strict=True):
        if decision.action == "protect":
            protected += 1
            print(f"  protected ({decision.reason}): {record.path}")
            continue
        if record.prunable:
            if validate_only:
                retained += 1
                print(f"  would prune (missing registration): {record.path}")
            elif record.path in pruned_paths:
                removed += 1
                print(f"  pruned (missing registration): {record.path}")
            else:
                retained += 1
                reason = (
                    "protected prunable registration exists"
                    if protected_prunable
                    else "git worktree prune failed"
                )
                print(f"  kept ({reason}): {record.path}")
            continue
        if validate_only:
            retained += 1
            print(f"  would remove (clean check deferred): {record.path}")
            continue
        result = _git("worktree", "remove", str(record.path), check=False)
        if result.returncode == 0:
            removed += 1
            print(f"  removed (clean): {record.path}")
        else:
            retained += 1
            print(f"  kept (dirty/unsynced): {record.path}")
    print(
        "wt-prune-safe done: "
        f"removed={removed} protected={protected} retained={retained} "
        f"active_branches={len(active_branches)}"
    )
    return 0



def _retention_identity(
    branch: str, worktree: Path,
) -> tuple[WorktreeRecord, tuple[tuple[int, int, int, int], ...]]:
    """Reuse native checkpoint authority and capture this linked-root identity."""
    owned_git_checkpoint.repository_root()
    inventory = _git("worktree", "list", "--porcelain", "-z", timeout=30).stdout
    owners = owned_git_checkpoint.registered_roots(inventory)
    if (worktree, False) not in owners.values():
        raise ValueError("WORKTREE must be a canonical registered linked checkout")
    common = Path(
        _git("rev-parse", "--path-format=absolute", "--git-common-dir", timeout=30).stdout.strip()
    )
    owned_git_checkpoint.verify_registered_root(worktree, main=False, common=common)
    records = [
        record for record in parse_worktrees(
            _git("worktree", "list", "--porcelain", timeout=30).stdout
        ) if record.path == worktree
    ]
    if (len(records) != 1 or records[0].branch != branch or records[0].prunable
            or _git("-C", str(worktree), "symbolic-ref", "--quiet", "HEAD", timeout=30).stdout.strip()
            != f"refs/heads/{branch}"):
        raise ValueError("WORKTREE registration and BRANCH must match exactly")
    identity = []
    for path, expected in ((worktree, stat.S_ISDIR), (worktree / ".git", stat.S_ISREG)):
        metadata = path.lstat()
        if not expected(metadata.st_mode):
            raise ValueError("WORKTREE or native backlink changed")
        identity.append((metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_size))
    return records[0], tuple(identity)


def retain(*, branch: str, worktree: Path, reason: str, validate_only: bool) -> int:
    """Positively retain one verified linked checkout using Git's native lock."""
    if (not branch or branch != branch.strip() or not reason.strip()
            or not worktree.is_absolute() or ".." in worktree.parts
            or any(character in branch + str(worktree) + reason for character in "\0\r\n")):
        raise ValueError("explicit literal BRANCH, absolute WORKTREE and reason are required")
    receipt: dict[str, str | bool | int] = {
        "branch": branch, "worktree": str(worktree), "reason": reason,
        "validate_only": validate_only, "native_ownership_verified": 0,
    }
    if validate_only:
        print(json.dumps(receipt, sort_keys=True))
        return 0
    initial = _retention_identity(branch, worktree)
    if _retention_identity(branch, worktree) != initial:
        raise ValueError("WORKTREE identity or registration changed before retention")
    already_locked = initial[0].locked
    if not already_locked:
        _git("worktree", "lock", "--reason", reason, "--", str(worktree), timeout=30)
        confirmed = _retention_identity(branch, worktree)
        if not confirmed[0].locked or confirmed[1] != initial[1]:
            raise ValueError("native retention lock was not verified on the unchanged owner")
    receipt.update(native_ownership_verified=1, locked=True, already_locked=already_locked)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--branch", default="")
    parser.add_argument("--worktree", type=Path)
    parser.add_argument("--reason", default="")
    parser.add_argument("--retain-validate-only", choices=("0", "1"), default="1")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the fail-closed worktree pruning command."""
    args = _parser().parse_args(argv)
    if args.retain:
        try:
            if args.worktree is None:
                raise ValueError("WORKTREE is required")
            return retain(
                branch=args.branch, worktree=args.worktree, reason=args.reason,
                validate_only=args.retain_validate_only == "1",
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"agent-worktree-retain: refused: {exc}")
            return 2
    return prune(
        registry_path=args.registry or default_registry_path(),
        validate_only=args.validate_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
