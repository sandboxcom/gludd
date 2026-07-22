#!/usr/bin/env python3
"""Path-qualified git worktree state guard for release evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Optional

RunFn = Callable[[Sequence[str], Optional[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class WorktreeState:
    path: str
    branch: str
    head: str
    dirty_count: int
    staged_count: int
    untracked_count: int
    status: list[str]

    @property
    def is_clean(self) -> bool:
        return self.dirty_count == 0


def _run(argv: Sequence[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _git_stdout(argv: Sequence[str], run: RunFn, cwd: Optional[str] = None) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        command = " ".join(argv)
        raise RuntimeError(f"{command}: {detail}")
    return result.stdout.strip()


def _dirty_lines(status_output: str) -> list[str]:
    return [line for line in status_output.splitlines() if line.strip()]


def _staged_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line[:2] != "??" and line[:1] not in {"", " "})


def _untracked_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line.startswith("??"))


def current_state(run: RunFn = _run, cwd: Optional[str] = None) -> WorktreeState:
    path = _git_stdout(["git", "rev-parse", "--show-toplevel"], run, cwd)
    branch = _git_stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    head = _git_stdout(["git", "rev-parse", "--verify", "HEAD"], run, cwd)
    status_result = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd)
    if status_result.returncode != 0:
        detail = (status_result.stderr or status_result.stdout or "git status failed").strip()
        raise RuntimeError(f"git status --porcelain=v1 --untracked-files=all: {detail}")
    lines = _dirty_lines(status_result.stdout)
    return WorktreeState(
        path=path,
        branch=branch,
        head=head,
        dirty_count=len(lines),
        staged_count=_staged_count(lines),
        untracked_count=_untracked_count(lines),
        status=lines,
    )


def format_claim_token(state: WorktreeState, checked_at: Optional[int] = None) -> str:
    if not state.is_clean:
        raise ValueError("cannot create WORKTREE-CLEAN token for dirty state")
    checked = int(time.time()) if checked_at is None else checked_at
    return (
        "WORKTREE-CLEAN "
        f"path={state.path} branch={state.branch} head={state.head} "
        f"dirty=0 checked_at={checked}"
    )


def _print_state(state: WorktreeState, checked_at: int, *, as_json: bool) -> None:
    if as_json:
        payload = asdict(state)
        payload["checked_at"] = checked_at
        payload["is_clean"] = state.is_clean
        json.dump(payload, sys.stdout, sort_keys=True)
        print()
        return

    prefix = "WORKTREE-CLEAN" if state.is_clean else "WORKTREE-DIRTY"
    print(
        f"{prefix} path={state.path} branch={state.branch} head={state.head} "
        f"dirty={state.dirty_count} staged={state.staged_count} "
        f"untracked={state.untracked_count} checked_at={checked_at}"
    )
    for line in state.status[:50]:
        print(f"  {line}")
    if len(state.status) > 50:
        print(f"  ... {len(state.status) - 50} more changed path(s)")


def main(argv: Optional[Sequence[str]] = None, run: RunFn = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable state")
    parser.add_argument("--assert-clean", action="store_true", help="fail if the current worktree is dirty")
    parser.add_argument("--claim-token", action="store_true", help="print a WORKTREE-CLEAN evidence token")
    args = parser.parse_args(list(argv) if argv is not None else None)

    checked_at = int(time.time())
    try:
        state = current_state(run=run)
    except RuntimeError as exc:
        print(f"WORKTREE-UNKNOWN error={exc}")
        return 1

    _print_state(state, checked_at, as_json=args.json)

    if args.assert_clean and not state.is_clean:
        return 1
    if args.claim_token:
        try:
            print(format_claim_token(state, checked_at=checked_at))
        except ValueError:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
