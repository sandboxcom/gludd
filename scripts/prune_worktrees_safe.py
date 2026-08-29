#!/usr/bin/env python3
"""Prune clean Git worktrees without reclaiming active logical workstreams."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the fail-closed worktree pruning command."""
    args = _parser().parse_args(argv)
    return prune(
        registry_path=args.registry or default_registry_path(),
        validate_only=args.validate_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
