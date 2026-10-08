#!/usr/bin/env python3
"""Poll one GitHub Actions workflow identity until it is terminal.

Release callers bind the wait to ref, full commit SHA, workflow, and event. The
GitHub query uses the commit selector and then repeats every identity check
locally so an API filter regression cannot authorize the wrong run. Generic
branch-only polling remains available for backwards compatibility.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_REPO = "sandboxcom/gludd"
DEFAULT_TIMEOUT = int(os.environ.get("CI_AWAIT_TIMEOUT", "3600"))
DEFAULT_POLL_INTERVAL = 60.0
SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")

TERMINAL_SUCCESS = {"success"}
TERMINAL_FAILURE = {
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
    "timed_out",
}
NON_TERMINAL = {"in_progress", "pending", "queued", "requested", "waiting"}

RunCommand = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
ProgressFn = Callable[[str], None]
ClockFn = Callable[[], float]
SleepFn = Callable[[float], None]


class AwaitError(RuntimeError):
    """A retryable GitHub run lookup failure."""


@dataclass(frozen=True)
class RunSelector:
    """Identity fields that a workflow run must match."""

    ref: str
    sha: str = ""
    workflow: str = ""
    event: str = ""
    repo: str = DEFAULT_REPO
    after_run_id: int = 0


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
    )


def _matches(run: dict[str, Any], selector: RunSelector) -> bool:
    try:
        run_id = int(run.get("databaseId") or 0)
    except (TypeError, ValueError):
        return False
    if run_id <= selector.after_run_id or not run.get("headSha"):
        return False
    if selector.sha and str(run.get("headSha") or "") != selector.sha:
        return False
    if selector.ref and str(run.get("headBranch") or "") != selector.ref:
        return False
    if (
        selector.workflow
        and str(run.get("workflowName") or "") != selector.workflow
    ):
        return False
    return not selector.event or str(run.get("event") or "") == selector.event


def select_latest_run(
    runs: object,
    selector: RunSelector,
) -> dict[str, Any] | None:
    """Select the newest locally verified run for the selector."""

    if not isinstance(runs, list):
        return None
    matches = [
        run
        for run in runs
        if isinstance(run, dict) and _matches(run, selector)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda run: (
            str(run.get("createdAt") or ""),
            int(run.get("databaseId") or 0),
        ),
    )


def get_latest_run(
    selector: RunSelector,
    *,
    runner: RunCommand = _run,
) -> dict[str, Any] | None:
    """Query GitHub and return the newest run matching every identity field."""

    command = [
        "gh",
        "run",
        "list",
        "-R",
        selector.repo,
        "--limit",
        "50",
        "--json",
        (
            "conclusion,status,databaseId,headSha,headBranch,createdAt,"
            "workflowName,event"
        ),
    ]
    if selector.sha:
        # Avoid the long-lived server-side branch-filter failure for exact-SHA
        # waits. Ref identity is still required by local selection.
        command.extend(["--commit", selector.sha])
    else:
        command.extend(["--branch", selector.ref])
    if selector.workflow:
        command.extend(["--workflow", selector.workflow])
    if selector.event:
        command.extend(["--event", selector.event])

    try:
        result = runner(command)
    except OSError as exc:
        raise AwaitError(f"GitHub run lookup failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise AwaitError(f"GitHub run lookup failed: {detail}")
    try:
        runs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise AwaitError(f"GitHub run lookup returned invalid JSON: {exc}") from exc
    if not isinstance(runs, list):
        raise AwaitError("GitHub run lookup returned a non-list JSON value")
    return select_latest_run(runs, selector)


def _describe(selector: RunSelector) -> str:
    return (
        f"ref={selector.ref} sha={selector.sha or '<any>'} "
        f"workflow={selector.workflow or '<any>'!r} "
        f"event={selector.event or '<any>'} "
        f"after_run_id={selector.after_run_id}"
    )


def ci_await(
    selector: RunSelector | str,
    timeout: int,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    fetch: Callable[[RunSelector], dict[str, Any] | None] | None = None,
    clock: ClockFn = time.monotonic,
    sleep: SleepFn = time.sleep,
    progress: ProgressFn = print,
) -> int:
    """Wait for a terminal run; return 0 success, 1 failure, or 2 timeout."""

    if isinstance(selector, str):
        selector = RunSelector(ref=selector)
    fetch_run = fetch or get_latest_run
    started = clock()
    progress(
        "=== CI-AWAIT: "
        f"{_describe(selector)} every {poll_interval:g}s (timeout={timeout}s) ==="
    )

    while True:
        elapsed = max(0.0, clock() - started)
        try:
            run = fetch_run(selector)
        except AwaitError as exc:
            progress(f"[{elapsed:g}s] CI-AWAIT: lookup-error={exc}")
            run = None

        if run is None:
            progress(
                f"[{elapsed:g}s] CI-AWAIT: no exact run found {_describe(selector)}"
            )
        else:
            run_id = run.get("databaseId", "?")
            head_sha = str(run.get("headSha") or "")[:12]
            status = str(run.get("status") or "unknown")
            conclusion = str(run.get("conclusion") or "")

            if status == "completed" and conclusion in TERMINAL_SUCCESS:
                progress(
                    f"[{elapsed:g}s] CI-AWAIT: TERMINAL SUCCESS run={run_id} "
                    f"sha={head_sha} conclusion={conclusion}"
                )
                return 0
            if conclusion in TERMINAL_FAILURE or status == "completed":
                progress(
                    f"[{elapsed:g}s] CI-AWAIT: TERMINAL FAILURE run={run_id} "
                    f"sha={head_sha} conclusion={conclusion or 'missing'}"
                )
                return 1
            state = status if status in NON_TERMINAL else f"unknown:{status}"
            progress(
                f"[{elapsed:g}s] CI-AWAIT: status={state} run={run_id} "
                f"sha={head_sha} conclusion={conclusion or 'none'}"
            )

        if elapsed >= timeout:
            progress(
                f"=== CI-AWAIT: TIMEOUT after {elapsed:g}s (still pending) ==="
            )
            return 2

        wait_seconds = min(poll_interval, timeout - elapsed)
        progress(
            f"[{elapsed:g}s] CI-AWAIT: heartbeat; next check in "
            f"{wait_seconds:g}s"
        )
        sleep(wait_seconds)


def _validate(
    parser: argparse.ArgumentParser,
    selector: RunSelector,
    *,
    timeout: int,
    poll_interval: float,
) -> None:
    if not selector.ref.strip():
        parser.error("ref cannot be empty")
    if selector.sha and SHA_RE.fullmatch(selector.sha) is None:
        parser.error("sha must be a full 40-character hexadecimal commit")
    if timeout <= 0:
        parser.error("timeout must be greater than zero")
    if poll_interval <= 0:
        parser.error("poll interval must be greater than zero")
    if not selector.repo.strip():
        parser.error("repo cannot be empty")
    if selector.after_run_id < 0:
        parser.error("after-run-id cannot be negative")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_branch", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument(
        "legacy_timeout",
        nargs="?",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--ref", default="", help="branch or tag ref name")
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="bounded wait seconds",
    )
    parser.add_argument("--sha", default="", help="full exact commit SHA")
    parser.add_argument(
        "--workflow",
        default="",
        help="exact workflow name or file",
    )
    parser.add_argument("--event", default="", help="exact triggering event")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub OWNER/REPO")
    parser.add_argument(
        "--after-run-id",
        type=int,
        default=0,
        help="accept only runs created after this pre-push baseline ID",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="seconds between observable checks",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate configuration without querying GitHub",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="print the newest matching run ID, or zero, without waiting",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    ref = args.ref or args.legacy_branch or os.environ.get("BRANCH", "master")
    timeout = (
        args.timeout
        if args.timeout is not None
        else args.legacy_timeout
        if args.legacy_timeout is not None
        else DEFAULT_TIMEOUT
    )
    selector = RunSelector(
        ref=ref,
        sha=args.sha,
        workflow=args.workflow,
        event=args.event,
        repo=args.repo,
        after_run_id=args.after_run_id,
    )
    _validate(
        parser,
        selector,
        timeout=timeout,
        poll_interval=args.poll_interval,
    )

    if args.validate_only:
        print(
            f"CI-AWAIT-VALIDATED {_describe(selector)} "
            f"timeout={timeout}s interval={args.poll_interval:g}s"
        )
        return 0
    if args.snapshot_only:
        try:
            run = get_latest_run(selector)
        except AwaitError as exc:
            print(f"CI-AWAIT-SNAPSHOT-BLOCKED error={exc}", file=sys.stderr)
            return 1
        print(int(run.get("databaseId") or 0) if run is not None else 0)
        return 0
    return ci_await(
        selector,
        timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
