#!/usr/bin/env python3
"""Fail closed unless a remote branch points at the exact local HEAD."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Optional

RunFn = Callable[[Sequence[str], Optional[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RemoteHeadState:
    branch: str
    local_head: str
    remote: str
    remote_ref: str
    remote_head: str
    dirty_count: int
    staged_count: int
    untracked_count: int
    status: list[str]

    @property
    def remote_matches_local(self) -> bool:
        return bool(self.remote_head) and self.remote_head == self.local_head

    @property
    def is_clean(self) -> bool:
        return self.dirty_count == 0


class GuardError(RuntimeError):
    pass

def _run(argv: Sequence[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _stdout(argv: Sequence[str], run: RunFn, cwd: Optional[str] = None) -> str:
    result = run(argv, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise GuardError(f"{list(argv)}: {detail}")
    return result.stdout.strip()

def _status_lines(run: RunFn, cwd: Optional[str] = None) -> list[str]:
    result = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        raise GuardError(f"git status --porcelain=v1 --untracked-files=all: {detail}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _staged_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line[:2] != "??" and line[:1] not in {"", " "})


def _untracked_count(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line.startswith("??"))


def _remote_head(output: str) -> str:
    first = output.splitlines()[0].split() if output.splitlines() else []
    return first[0] if first else ""


def collect_state(
    ref: str = "",
    remote: str = "sandboxcom",
    run: RunFn = _run,
    cwd: Optional[str] = None,
) -> RemoteHeadState:
    branch = _stdout(["git", "branch", "--show-current"], run, cwd) or "DETACHED"
    local_head = _stdout(["git", "rev-parse", "--verify", "HEAD"], run, cwd)
    status = _status_lines(run, cwd)
    remote_name = remote or "sandboxcom"
    remote_branch = ref or branch
    remote_ref = f"refs/heads/{remote_branch}"
    remote_output = _stdout(["git", "ls-remote", remote_name, remote_ref], run, cwd)

    return RemoteHeadState(
        branch=branch,
        local_head=local_head,
        remote=remote_name,
        remote_ref=remote_ref,
        remote_head=_remote_head(remote_output),
        dirty_count=len(status),
        staged_count=_staged_count(status),
        untracked_count=_untracked_count(status),
        status=status,
    )


def guard_state(state: RemoteHeadState, *, allow_dirty_unstaged: bool = False) -> list[str]:
    errors: list[str] = []
    if not state.remote_head:
        errors.append(f"remote branch {state.remote}/{state.remote_ref} does not exist")
    elif state.remote_head != state.local_head:
        errors.append(
            f"remote {state.remote}/{state.remote_ref} is {state.remote_head}, not local HEAD {state.local_head}"
        )
    if state.staged_count:
        errors.append(f"{state.staged_count} staged change(s) would not be present in the remote CI run")
    if state.dirty_count and not allow_dirty_unstaged:
        errors.append(f"{state.dirty_count} local dirty path(s) would make local tests differ from remote CI")
    if allow_dirty_unstaged and state.staged_count == 0 and state.dirty_count:
        errors.append(
            "dirty unstaged work exists; use committed-head CI only for remote harness checks, not release evidence"
        )
    return errors


def print_state(state: RemoteHeadState, errors: Sequence[str], *, as_json: bool) -> None:
    if as_json:
        payload = asdict(state)
        payload["remote_matches_local"] = state.remote_matches_local
        payload["is_clean"] = state.is_clean
        payload["errors"] = list(errors)
        json.dump(payload, sys.stdout, sort_keys=True)
        print()
        return

    prefix = "REMOTE-HEAD-READY" if not errors else "REMOTE-HEAD-BLOCKED"
    remote_head = state.remote_head or "<missing>"
    print(
        f"{prefix} branch={state.branch} local_head={state.local_head} "
        f"remote={state.remote}/{state.remote_ref} remote_head={remote_head} "
        f"dirty={state.dirty_count} staged={state.staged_count} untracked={state.untracked_count}"
    )
    for error in errors:
        print(f"BLOCKED: {error}")
    for line in state.status[:25]:
        print(f"  {line}")
    if len(state.status) > 25:
        print(f"  ... {len(state.status) - 25} more changed path(s)")



def main(argv: Optional[Sequence[str]] = None, run: RunFn = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="", help="branch ref to verify on the remote; defaults to current branch")
    parser.add_argument("--remote", default="sandboxcom", help="git remote name")
    parser.add_argument("--json", action="store_true", help="emit machine-readable state")
    parser.add_argument(
        "--allow-dirty-unstaged",
        action="store_true",
        help="allow remote-head checks while local unstaged work exists, but still fail staged work",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        state = collect_state(args.ref, args.remote, run=run)
        errors = guard_state(state, allow_dirty_unstaged=args.allow_dirty_unstaged)
    except GuardError as exc:
        print(f"REMOTE-HEAD-UNKNOWN error={exc}")
        return 1

    print_state(state, errors, as_json=args.json)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
