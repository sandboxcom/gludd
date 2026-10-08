#!/usr/bin/env python3
"""Idempotently ensure an exact-SHA GitHub Actions run exists.

The release-candidate path must never rely on a person noticing that a push did
not produce a visible run.  This command:

1. fails closed unless the worktree is clean and the remote ref equals HEAD;
2. reuses an existing exact-SHA run when one is already visible;
3. gives the push-triggered run a short discovery window;
4. dispatches the workflow at most once for this SHA on the local release host;
5. waits until GitHub returns an exact-SHA run URL.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from ci_remote_head_guard import GuardError, collect_state, guard_state

# Quoted because macOS /usr/bin/python3 can be 3.9: postponed annotations do
# not defer evaluation of a module-level type alias.
RunFn = Callable[[Sequence[str], "str | None"], subprocess.CompletedProcess[str]]
SleepFn = Callable[[float], None]
ProgressFn = Callable[[str], None]

DEFAULT_REPO = "sandboxcom/gludd"
DEFAULT_REMOTE = "sandboxcom"
DEFAULT_WORKFLOW = "Build and Release"
DEFAULT_STATE_DIR = Path("/tmp")
RUN_URL_RE = re.compile(r"https://github\.com/[^\s]+/actions/runs/[^\s]+")


class SignalError(RuntimeError):
    """A fail-closed exact-SHA signaling error."""


@dataclass(frozen=True)
class WorkflowRun:
    database_id: str
    head_sha: str
    url: str
    status: str
    conclusion: str
    event: str

    @classmethod
    def from_payload(cls, payload: object, repo: str) -> WorkflowRun | None:
        if not isinstance(payload, dict):
            return None
        database_id = str(payload.get("databaseId") or "")
        head_sha = str(payload.get("headSha") or "")
        if not database_id or not head_sha:
            return None
        url = str(payload.get("url") or "")
        if not url:
            url = f"https://github.com/{repo}/actions/runs/{database_id}"
        return cls(
            database_id=database_id,
            head_sha=head_sha,
            url=url,
            status=str(payload.get("status") or ""),
            conclusion=str(payload.get("conclusion") or ""),
            event=str(payload.get("event") or ""),
        )


@dataclass(frozen=True)
class SignalResult:
    sha: str
    ref: str
    run: WorkflowRun
    dispatched: bool

    @property
    def url(self) -> str:
        return self.run.url


def _run(
    argv: Sequence[str],
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _checked(
    argv: Sequence[str],
    *,
    run: RunFn,
    cwd: str | None,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(argv, cwd)
    except OSError as exc:
        raise SignalError(f"{operation} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise SignalError(f"{operation} failed: {detail}")
    return result


def _list_exact_runs(
    *,
    repo: str,
    workflow: str,
    sha: str,
    run: RunFn,
    cwd: str | None,
) -> list[WorkflowRun]:
    result = _checked(
        [
            "gh",
            "run",
            "list",
            "-R",
            repo,
            "--workflow",
            workflow,
            "--commit",
            sha,
            "--json",
            "databaseId,headSha,status,conclusion,url,event",
            "--limit",
            "20",
        ],
        run=run,
        cwd=cwd,
        operation="exact-SHA run lookup",
    )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SignalError(f"exact-SHA run lookup returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise SignalError("exact-SHA run lookup returned a non-list JSON value")
    exact_runs: list[WorkflowRun] = []
    for item in payload:
        candidate = WorkflowRun.from_payload(item, repo)
        if candidate is not None and candidate.head_sha == sha:
            exact_runs.append(candidate)
    return exact_runs


def _run_is_reusable(candidate: WorkflowRun) -> bool:
    return candidate.status != "completed" or candidate.conclusion == "success"


def _list_exact_run(
    *,
    repo: str,
    workflow: str,
    sha: str,
    run: RunFn,
    cwd: str | None,
) -> WorkflowRun | None:
    return next(
        (
            candidate
            for candidate in _list_exact_runs(
                repo=repo,
                workflow=workflow,
                sha=sha,
                run=run,
                cwd=cwd,
            )
            if _run_is_reusable(candidate)
        ),
        None,
    )


def _list_terminal_unsuccessful_run(
    *,
    repo: str,
    workflow: str,
    sha: str,
    run: RunFn,
    cwd: str | None,
) -> WorkflowRun | None:
    return next(
        (
            candidate
            for candidate in _list_exact_runs(
                repo=repo,
                workflow=workflow,
                sha=sha,
                run=run,
                cwd=cwd,
            )
            if not _run_is_reusable(candidate)
        ),
        None,
    )


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned[:80] or "default"


def _state_paths(
    state_dir: Path,
    *,
    repo: str,
    workflow: str,
    sha: str,
) -> tuple[Path, Path]:
    stem = (
        f"gludd-gha-signal-{_safe_component(repo)}-"
        f"{_safe_component(workflow)}-{_safe_component(sha)}"
    )
    return state_dir / f"{stem}.json", state_dir / f"{stem}.lock"


@contextmanager
def _signal_lock(
    path: Path,
    *,
    sleep: SleepFn,
    progress: ProgressFn,
    timeout: float = 60.0,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SignalError(
                        f"timed out waiting for exact-SHA signal lock {path}"
                    ) from None
                progress(f"GHA-SIGNAL-LOCK waiting path={path}")
                sleep(1.0)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_marker(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise SignalError(f"cannot read dispatch marker {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SignalError(f"dispatch marker {path} is not a JSON object")
    return payload


def _write_marker(
    path: Path,
    *,
    sha: str,
    ref: str,
    repo: str,
    workflow: str,
    dispatch_url: str,
) -> None:
    payload = {
        "version": 1,
        "sha": sha,
        "ref": ref,
        "repo": repo,
        "workflow": workflow,
        "dispatch_url": dispatch_url,
        "requested_at_epoch": time.time(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise SignalError(f"cannot persist dispatch marker {path}: {exc}") from exc
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _poll_for_run(
    *,
    phase: str,
    polls: int,
    poll_interval: float,
    repo: str,
    workflow: str,
    sha: str,
    run: RunFn,
    cwd: str | None,
    sleep: SleepFn,
    progress: ProgressFn,
) -> WorkflowRun | None:
    attempts = max(1, polls)
    for attempt in range(1, attempts + 1):
        exact_run = _list_exact_run(
            repo=repo,
            workflow=workflow,
            sha=sha,
            run=run,
            cwd=cwd,
        )
        if exact_run is not None:
            return exact_run
        progress(
            f"GHA-SIGNAL-CHECK phase={phase} attempt={attempt}/{attempts} "
            f"sha={sha} result=absent"
        )
        if attempt < attempts and poll_interval > 0:
            sleep(poll_interval)
    return None


def _dispatch(
    *,
    repo: str,
    workflow: str,
    ref: str,
    run: RunFn,
    cwd: str | None,
) -> str:
    result = _checked(
        ["gh", "workflow", "run", workflow, "-R", repo, "--ref", ref],
        run=run,
        cwd=cwd,
        operation="workflow dispatch",
    )
    match = RUN_URL_RE.search(result.stdout or "")
    return match.group(0) if match else ""


def _print_result(result: SignalResult, progress: ProgressFn) -> None:
    action = "DISPATCHED" if result.dispatched else "EXISTING"
    run = result.run
    progress(
        f"GHA-SIGNAL-{action} sha={result.sha} ref={result.ref} "
        f"run={run.database_id} event={run.event or 'unknown'} "
        f"status={run.status or 'unknown'} conclusion={run.conclusion or 'pending'} "
        f"url={run.url}"
    )
    progress(f"GHA_RUN_URL={run.url}")


def signal_exact_sha(
    *,
    ref: str = "",
    remote: str = DEFAULT_REMOTE,
    repo: str = DEFAULT_REPO,
    workflow: str = DEFAULT_WORKFLOW,
    discovery_polls: int = 6,
    confirm_polls: int = 15,
    poll_interval: float = 2.0,
    state_dir: Path = DEFAULT_STATE_DIR,
    run: RunFn = _run,
    sleep: SleepFn = time.sleep,
    progress: ProgressFn = print,
    cwd: str | None = None,
) -> SignalResult:
    """Return an existing exact-SHA run or dispatch and confirm exactly one."""

    try:
        remote_state = collect_state(
            ref=ref,
            remote=remote,
            run=run,
            cwd=cwd,
        )
        guard_errors = guard_state(remote_state)
    except (GuardError, OSError) as exc:
        raise SignalError(f"remote-head guard failed: {exc}") from exc
    if guard_errors:
        raise SignalError("remote-head guard failed: " + "; ".join(guard_errors))

    signal_ref = ref or remote_state.branch
    sha = remote_state.local_head
    marker_path, lock_path = _state_paths(
        state_dir,
        repo=repo,
        workflow=workflow,
        sha=sha,
    )

    with _signal_lock(lock_path, sleep=sleep, progress=progress):
        exact_run = _poll_for_run(
            phase="push-discovery",
            polls=discovery_polls,
            poll_interval=poll_interval,
            repo=repo,
            workflow=workflow,
            sha=sha,
            run=run,
            cwd=cwd,
            sleep=sleep,
            progress=progress,
        )
        if exact_run is not None:
            result = SignalResult(
                sha=sha,
                ref=signal_ref,
                run=exact_run,
                dispatched=False,
            )
            _print_result(result, progress)
            return result

        terminal_run = _list_terminal_unsuccessful_run(
            repo=repo,
            workflow=workflow,
            sha=sha,
            run=run,
            cwd=cwd,
        )
        marker = _read_marker(marker_path)
        dispatched_now = False
        dispatch_url = ""
        if marker is not None and terminal_run is None:
            dispatch_url = str(marker.get("dispatch_url") or "")
            progress(
                f"GHA-SIGNAL-ALREADY-REQUESTED sha={sha} ref={signal_ref} "
                f"url={dispatch_url or '<pending>'}"
            )
        else:
            if terminal_run is not None:
                progress(
                    f"GHA-SIGNAL-RETRY sha={sha} ref={signal_ref} "
                    f"prior_run={terminal_run.database_id} "
                    f"conclusion={terminal_run.conclusion or '<missing>'}"
                )
            progress(
                f"GHA-SIGNAL-DISPATCH sha={sha} ref={signal_ref} "
                f"workflow={workflow!r}"
            )
            dispatch_url = _dispatch(
                repo=repo,
                workflow=workflow,
                ref=signal_ref,
                run=run,
                cwd=cwd,
            )
            _write_marker(
                marker_path,
                sha=sha,
                ref=signal_ref,
                repo=repo,
                workflow=workflow,
                dispatch_url=dispatch_url,
            )
            dispatched_now = True

        exact_run = _poll_for_run(
            phase="dispatch-confirm",
            polls=confirm_polls,
            poll_interval=poll_interval,
            repo=repo,
            workflow=workflow,
            sha=sha,
            run=run,
            cwd=cwd,
            sleep=sleep,
            progress=progress,
        )
        if exact_run is None:
            url_detail = f"; dispatch URL={dispatch_url}" if dispatch_url else ""
            raise SignalError(
                f"exact-SHA run for {sha} is not visible after dispatch confirmation"
                f"{url_detail}; refusing a duplicate dispatch"
            )

        result = SignalResult(
            sha=sha,
            ref=signal_ref,
            run=exact_run,
            dispatched=dispatched_now,
        )
        _print_result(result, progress)
        return result


def _example_result(ref: str, repo: str) -> SignalResult:
    sha = "0123456789abcdef0123456789abcdef01234567"
    run = WorkflowRun(
        database_id="example",
        head_sha=sha,
        url=f"https://github.com/{repo}/actions/runs/example",
        status="queued",
        conclusion="",
        event="push",
    )
    return SignalResult(
        sha=sha,
        ref=ref or "release/beta3-candidate",
        run=run,
        dispatched=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="", help="remote branch ref; defaults to current branch")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="git remote name")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub OWNER/REPO")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW, help="workflow name or file")
    parser.add_argument("--discovery-polls", type=int, default=6)
    parser.add_argument("--confirm-polls", type=int, default=15)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("GLUDD_GHA_SIGNAL_STATE_DIR", DEFAULT_STATE_DIR)),
    )
    parser.add_argument(
        "--example",
        action="store_true",
        help="run a deterministic, network-free behavioral example",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.discovery_polls < 1 or args.confirm_polls < 1:
        parser.error("poll counts must be at least 1")
    if args.poll_interval < 0:
        parser.error("poll interval cannot be negative")

    if args.example:
        result = _example_result(args.ref, args.repo)
        print(
            f"GHA-SIGNAL-EXAMPLE clean=true remote_matches_local=true "
            f"workflow={args.workflow!r}"
        )
        _print_result(result, print)
        return 0

    try:
        signal_exact_sha(
            ref=args.ref,
            remote=args.remote,
            repo=args.repo,
            workflow=args.workflow,
            discovery_polls=args.discovery_polls,
            confirm_polls=args.confirm_polls,
            poll_interval=args.poll_interval,
            state_dir=args.state_dir,
        )
    except SignalError as exc:
        print(f"GHA-SIGNAL-BLOCKED error={exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
