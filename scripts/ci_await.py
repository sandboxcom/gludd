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
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proof_observations import write_snapshot

    from scripts.ci_check_cooldown import record_diagnostic_verdict
else:
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.ci_check_cooldown import record_diagnostic_verdict
    from scripts.proof_observations import write_snapshot

DEFAULT_REPO = "sandboxcom/gludd"
ROOT = Path(__file__).resolve().parents[1]
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
_DEFAULT_PROGRESS: ProgressFn = partial(print, flush=True)
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


@dataclass(frozen=True)
class ObservationBudget:
    """One bounded reporting choice; execution and queue timing stay unproved."""

    timeout: int
    source: str
    workflow_plan: dict[str, Any] | None = None
    reason: str = ""


def _budget_integer(value: object, field: str) -> int:
    """Accept literal positive integers without rounding or evaluating expressions."""
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    raise ValueError(f"{field} needs a literal positive integer")


def _matrix_job_bound(strategy: object) -> int:
    """Count literal axes conservatively, including independent include entries."""
    if strategy is None:
        return 1
    if not isinstance(strategy, dict):
        raise ValueError("strategy needs literal mapping input")
    matrix = strategy.get("matrix")
    if matrix is None:
        return 1
    if not isinstance(matrix, dict):
        raise ValueError("matrix needs literal mapping input")
    axes = {key: value for key, value in matrix.items() if key not in {"include", "exclude"}}
    if any(not isinstance(values, list) or not values for values in axes.values()):
        raise ValueError("matrix axes need nonempty literal lists")
    includes, excludes = matrix.get("include", []), matrix.get("exclude", [])
    if not isinstance(includes, list) or not all(isinstance(item, dict) for item in includes):
        raise ValueError("matrix include needs literal mappings")
    if not isinstance(excludes, list) or not all(isinstance(item, dict) for item in excludes):
        raise ValueError("matrix exclude needs literal mappings")
    if not axes:
        if not includes:
            raise ValueError("matrix has no statically countable jobs")
        return len(includes)
    count = 1
    for values in axes.values():
        count *= len(values)
    # Ignoring exclusions deliberately overestimates; include entries can revive
    # excluded combinations, so count every include separately in that case.
    if excludes:
        return count + len(includes)
    return count + sum(
        any(key in axes and value not in axes[key] for key, value in item.items())
        for item in includes
    )


def workflow_observation_plan(payload: object) -> dict[str, Any]:
    """Model declared job-cap waves and dependencies, with queue delay unknown."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), dict) or not payload["jobs"]:
        raise ValueError("workflow needs a nonempty literal jobs mapping")
    jobs = payload["jobs"]
    costs: dict[str, int] = {}
    dependencies: dict[str, list[str]] = {}
    rows: dict[str, dict[str, int]] = {}
    for name, job in jobs.items():
        if not isinstance(name, str) or not isinstance(job, dict):
            raise ValueError("workflow jobs need named literal mappings")
        cap = _budget_integer(job.get("timeout-minutes"), "timeout-minutes") * 60
        strategy = job.get("strategy")
        count = _matrix_job_bound(strategy)
        parallel = _budget_integer(
            strategy.get("max-parallel", count) if isinstance(strategy, dict) else count, "max-parallel"
        )
        waves = (count + parallel - 1) // parallel
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        if not isinstance(needs, list) or not all(isinstance(item, str) for item in needs):
            raise ValueError("needs must name literal workflow jobs")
        dependencies[name] = needs
        costs[name] = cap * waves
        rows[name] = {"job_cap_seconds": cap, "matrix_jobs_upper_bound": count,
                      "max_parallel": parallel, "waves": waves, "execution_allowance_seconds": costs[name]}
    finished: dict[str, tuple[int, tuple[str, ...]]] = {}
    visiting: set[str] = set()

    def finish(name: str) -> tuple[int, tuple[str, ...]]:
        if name not in jobs or name in visiting:
            raise ValueError("workflow dependency is missing or cyclic")
        if name in finished:
            return finished[name]
        visiting.add(name)
        seconds, path = max((finish(parent) for parent in dependencies[name]), default=(0, ()))
        visiting.remove(name)
        finished[name] = (seconds + costs[name], (*path, name))
        return finished[name]

    seconds, path = max(finish(name) for name in jobs)
    return {"execution_allowance_seconds": seconds, "critical_cap_path": list(path), "jobs": rows,
            "queue_seconds": None, "observed_durations": None, "conditions": "conservatively included",
            "authority": "observation planning only", "gate_evidence": False, "release_ready": False}


def _local_workflow_observation_plan(selector: RunSelector) -> dict[str, Any] | None:
    """Read owned local workflow configuration, never remote jobs or observations."""
    if selector.repo != DEFAULT_REPO or not selector.workflow:
        return None
    import hashlib

    import yaml

    matches: list[tuple[Path, bytes, dict[str, Any]]] = []
    directory = ROOT / ".github" / "workflows"
    directory.resolve().relative_to(ROOT.resolve())
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        if path.is_symlink():
            continue
        raw = path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("workflow budget source exceeds the bounded read")
        try:
            payload = yaml.load(raw, Loader=yaml.BaseLoader)
        except yaml.YAMLError:
            if path.name == selector.workflow:
                raise ValueError("selected workflow budget YAML is invalid") from None
            continue
        if not isinstance(payload, dict):
            continue
        if selector.workflow in {path.name, str(path.relative_to(ROOT))} or payload.get("name") == selector.workflow:
            matches.append((path, raw, payload))
    if len(matches) > 1:
        raise ValueError("workflow budget source is ambiguous")
    if not matches:
        return None
    path, raw, payload = matches[0]
    plan = workflow_observation_plan(payload)
    plan.update(workflow_source=str(path.relative_to(ROOT)), workflow_source_sha256=hashlib.sha256(raw).hexdigest())
    return plan


def select_observation_budget(selector: RunSelector, explicit: int | None = None) -> ObservationBudget:
    """Preserve explicit choices without reading unrelated workflow input."""
    if explicit is not None:
        return ObservationBudget(explicit, "explicit")
    if "CI_AWAIT_TIMEOUT" in os.environ:
        return ObservationBudget(DEFAULT_TIMEOUT, "environment")
    plan: dict[str, Any] | None = None
    reason = ""
    try:
        plan = _local_workflow_observation_plan(selector)
    except (ImportError, OSError, RecursionError, ValueError) as exc:
        reason = type(exc).__name__
    if plan is not None:
        seconds = plan["execution_allowance_seconds"]
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError("workflow reporting allowance is not a positive integer")
        return ObservationBudget(seconds, "workflow-job-caps", plan)
    return ObservationBudget(DEFAULT_TIMEOUT, "legacy-default", reason=reason or "workflow-source-unavailable")


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


def _publish_observation(
    path: Path | None, selector: RunSelector, run: dict[str, Any] | None,
    *, state: str, elapsed: float, exit_code: int | None,
    error: str | None, progress: ProgressFn,
) -> bool:
    """Publish exact diagnostic state before sleeping or returning a verdict."""
    if path is None:
        return True
    try:
        if not selector.sha or (run is not None and not _matches(run, selector)):
            raise ValueError("observation identity must match a full exact selector")
        write_snapshot(
            path, producer="ci-await",
            identity={"repo": selector.repo, "branch": selector.ref, "sha": selector.sha},
            state=state,
            evidence={
                "workflow": selector.workflow, "event": selector.event,
                "after_run_id": selector.after_run_id, "run": run,
                "elapsed_seconds": elapsed, "exit_code": exit_code, "error": error,
            },
        )
    except (OSError, ValueError) as exc:
        progress(f"CI-AWAIT: observation-error={exc}")
        return False
    return True



def _record_diagnostic_feedback(
    path: Path | None, selector: RunSelector, run: dict[str, Any],
    *, elapsed: float, progress: ProgressFn,
) -> bool:
    """Use only the just-published terminal producer observation for feedback."""
    if path is None:
        return True
    try:
        recorded = record_diagnostic_verdict(
            repo=selector.repo, branch=selector.ref, sha=selector.sha,
            workflow=selector.workflow, event=selector.event,
            run=run, after_run_id=selector.after_run_id,
        )
    except (OSError, ValueError) as exc:
        progress(f"CI-AWAIT: diagnostic-history-error={exc}")
        _publish_observation(
            path, selector, run, state="lookup_error", elapsed=elapsed,
            exit_code=1, error=str(exc), progress=progress,
        )
        progress(
            "AGENT_NEXT target=ci-await outcome=diagnostic-feedback-failed native_status=1 "
            "history_may_be_applied=true canonical_push=false ci_green=false "
            "gate_evidence=false release_ready=false: Retain the native writer error; "
            "reconcile the existing history/state/counter before retrying."
        )
        return False
    if recorded:
        progress(
            f"AGENT_NEXT target=ci-await outcome=diagnostic-feedback-recorded "
            f"native_status={0 if run.get('conclusion') == 'success' else 1} "
            "history_recorded=true canonical_push=false ci_green=false "
            "gate_evidence=false release_ready=false: Terminal Build dispatch feedback "
            "was recorded for the current pushed SHA; repair its failures before the "
            "next normal guarded source publication. Canonical push and release proof remain required."
        )
    return True


def ci_await(
    selector: RunSelector | str,
    timeout: int,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    fetch: Callable[[RunSelector], dict[str, Any] | None] | None = None,
    clock: ClockFn = time.monotonic,
    sleep: SleepFn = time.sleep,
    progress: ProgressFn = _DEFAULT_PROGRESS,
    observation_path: Path | None = None,
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
        lookup_error: str | None = None
        try:
            run = fetch_run(selector)
        except AwaitError as exc:
            progress(f"[{elapsed:g}s] CI-AWAIT: lookup-error={exc}")
            lookup_error = str(exc)
            run = None

        publish = partial(
            _publish_observation, observation_path, selector, run,
            elapsed=elapsed, error=lookup_error, progress=progress,
        )

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
                if not publish(state="success", exit_code=0):
                    return 1
                if not _record_diagnostic_feedback(
                    observation_path, selector, run, elapsed=elapsed, progress=progress,
                ):
                    return 1
                progress(
                    f"[{elapsed:g}s] CI-AWAIT: TERMINAL SUCCESS run={run_id} "
                    f"sha={head_sha} conclusion={conclusion}"
                )
                return 0
            if conclusion in TERMINAL_FAILURE or status == "completed":
                if not publish(state="failure", exit_code=1):
                    return 1
                if not _record_diagnostic_feedback(
                    observation_path, selector, run, elapsed=elapsed, progress=progress,
                ):
                    return 1
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
            if not publish(state="timeout", exit_code=2):
                return 1
            progress(
                f"=== CI-AWAIT: TIMEOUT after {elapsed:g}s (still pending) ==="
            )
            return 2

        if not publish(state="lookup_error" if lookup_error else "pending", exit_code=None):
            return 1

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
    """Validate the selector and run the existing exact-workflow waiter."""
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
    parser.add_argument(
        "--observation-output", type=Path,
        help="atomically refresh a diagnostic JSON observation on every poll",
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
    if args.observation_output is not None and not selector.sha:
        parser.error("observation output requires a full exact SHA")

    if not args.snapshot_only or args.validate_only:
        explicit = args.timeout if args.timeout is not None else args.legacy_timeout
        budget = select_observation_budget(selector, explicit)
        timeout = budget.timeout
        planned = budget.workflow_plan["execution_allowance_seconds"] if budget.workflow_plan is not None else "unknown"
        print(
            f"CI-AWAIT-BUDGET timeout={timeout}s source={budget.source} "
            f"fresh_workflow_execution_allowance={planned} queue_seconds=unknown "
            f"observed_durations=unknown plan_reason={budget.reason or 'none'} "
            "gate_evidence=false release_ready=false; explicit deadlines remain caller-owned",
            file=sys.stderr,
        )
        if args.validate_only and budget.workflow_plan is not None:
            print("CI-AWAIT-BUDGET-PLAN " + json.dumps(budget.workflow_plan, sort_keys=True), file=sys.stderr)

    if args.validate_only:
        print(
            f"CI-AWAIT-VALIDATED {_describe(selector)} "
            f"timeout={timeout}s interval={args.poll_interval:g}s"
        )
        print(
            "AGENT_NEXT target=ci-await outcome=validated mode=validate-only "
            "native_status=0 configuration_only=true network=false "
            "terminal_verdict=false ci_green=false observation_written=false "
            "gate_evidence=false release_ready=false next_owner=ci-await; "
            "configuration validated only; no GitHub query, wait or observation "
            "write occurred. To observe CI, use CI_AWAIT_VALIDATE_ONLY=0 and "
            "CI_AWAIT_SNAPSHOT_ONLY=0 with the same selector.",
            file=sys.stderr,
        )
        return 0
    if args.snapshot_only:
        try:
            run = get_latest_run(selector)
        except AwaitError as exc:
            print(f"CI-AWAIT-SNAPSHOT-BLOCKED error={exc}", file=sys.stderr)
            print(
                "AGENT_NEXT target=ci-await outcome=lookup-failed mode=snapshot-only "
                "native_status=1 lookup_only=true terminal_verdict=false "
                "ci_green=false observation_written=false gate_evidence=false "
                "release_ready=false next_owner=ci-await; "
                "snapshot lookup failed; observation output is unchanged. "
                "Repair the reported lookup input before retry.",
                file=sys.stderr,
            )
            return 1
        print(int(run.get("databaseId") or 0) if run is not None else 0)
        print(
            "AGENT_NEXT target=ci-await outcome=snapshot mode=snapshot-only "
            "native_status=0 lookup_only=true terminal_verdict=false ci_green=false "
            "observation_written=false gate_evidence=false release_ready=false "
            "next_owner=ci-await; stdout is only the newest matching run ID or 0; "
            "zero status confirms lookup only. Observation output is unchanged. "
            "To observe the current matching run, use CI_AWAIT_SNAPSHOT_ONLY=0 "
            "with the same selector and after-run ID. Use stdout as "
            "CI_AWAIT_AFTER_RUN_ID only when awaiting a future run.",
            file=sys.stderr,
        )
        return 0
    if args.observation_output is not None:
        return ci_await(
            selector, timeout, poll_interval=args.poll_interval,
            observation_path=args.observation_output,
        )
    return ci_await(
        selector,
        timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
