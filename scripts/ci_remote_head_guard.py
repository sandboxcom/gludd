#!/usr/bin/env python3
"""Refuse to trigger remote CI unless local HEAD is exactly what GHE will test."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    message: str


RunFn = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
    )


def _output(argv: Sequence[str], run: RunFn) -> str:
    result = run(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        joined = " ".join(argv)
        raise RuntimeError(f"command failed: {joined}: {detail}")
    return result.stdout.strip()


def _dirty_tree(run: RunFn) -> list[str]:
    status = _output(["git", "status", "--porcelain=v1", "--untracked-files=all"], run)
    return [line for line in status.splitlines() if line.strip()]


def _current_branch(run: RunFn) -> str:
    return _output(["git", "branch", "--show-current"], run)


def _local_head(run: RunFn) -> str:
    return _output(["git", "rev-parse", "HEAD"], run)


def _remote_head(remote: str, ref: str, run: RunFn) -> str | None:
    result = run(["git", "ls-remote", remote, f"refs/heads/{ref}"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"unable to read remote ref {remote}/{ref}: {detail}")
    output = result.stdout.strip()
    if not output:
        return None
    return output.splitlines()[0].split()[0]


def guard_remote_head(ref: str | None = None, remote: str = "origin", run: RunFn = _run) -> GuardResult:
    """Verify remote workflow dispatch would test this clean local HEAD."""
    try:
        branch = _current_branch(run)
        if not branch:
            return GuardResult(False, "BLOCKED: detached HEAD cannot be used for ci-trigger")

        selected_ref = ref or branch
        if selected_ref != branch:
            return GuardResult(
                False,
                f"BLOCKED: ci-trigger REF={selected_ref} does not match current branch {branch}",
            )

        dirty = _dirty_tree(run)
        if dirty:
            newline = chr(10)
            shown = newline.join(f"  {line}" for line in dirty[:50])
            suffix = "" if len(dirty) <= 50 else f"{newline}  ... {len(dirty) - 50} more changed path(s)"
            return GuardResult(
                False,
                "BLOCKED: local tree has uncommitted changes; commit before triggering remote CI."
                f"{newline}{shown}{suffix}",
            )

        local = _local_head(run)
        remote_sha = _remote_head(remote, selected_ref, run)
        if remote_sha is None:
            return GuardResult(False, f"BLOCKED: remote branch {remote}/{selected_ref} does not exist")
        if remote_sha != local:
            return GuardResult(
                False,
                f"BLOCKED: local HEAD {local} differs from {remote}/{selected_ref} {remote_sha}; push exact HEAD first",
            )

        return GuardResult(True, f"ci-remote-head-guard: {remote}/{selected_ref} matches local HEAD {local}")
    except RuntimeError as exc:
        return GuardResult(False, f"BLOCKED: {exc}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=None, help="Branch/ref to dispatch; defaults to current branch")
    parser.add_argument("--remote", default="origin", help="Git remote to compare")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    result = guard_remote_head(ref=args.ref, remote=args.remote)
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
