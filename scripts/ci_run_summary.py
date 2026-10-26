#!/usr/bin/env python3
"""Print a concise, fail-closed summary for one immutable GitHub Actions run."""

from __future__ import annotations

import argparse
import json
import re
import runpy
import subprocess
import sys
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import cast

Job = dict[str, object]
Payload = dict[str, object]

RUN_FIELDS = "databaseId,headSha,status,conclusion,url,workflowName,jobs"
_RUN_ID = re.compile(r"[1-9][0-9]*\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_SHARD_JOB = re.compile(r"test-shard \(3\.11, ([^,()]+)(?:, [^)]*)?\)\Z")
_GATE_JOB = re.compile(r"gate \((?:3\.11|3\.12)\)\Z")


def _value(job: Job, key: str, default: str = "?") -> str:
    value = job.get(key)
    if value is None or value == "":
        return default
    return str(value)


def _print_jobs(marker: str, jobs: Iterable[Job]) -> None:
    for job in jobs:
        name = _value(job, "name")
        status = _value(job, "status")
        conclusion = _value(job, "conclusion", "—")
        print(f"{marker} {name:<29} {status:<12} {conclusion:<12}")


def _parse_payload(raw: str, source: str) -> Payload | None:
    try:
        decoded = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        print(f"ci-run-summary: invalid JSON from {source}: {exc}", file=sys.stderr)
        return None
    if not isinstance(decoded, dict):
        print(f"ci-run-summary: expected a JSON object from {source}", file=sys.stderr)
        return None
    return cast(Payload, decoded)


def _fetch_run(run_id: str, repository: str) -> tuple[Payload | None, int]:
    command = [
        "gh",
        "run",
        "view",
        run_id,
        "--repo",
        repository,
        "--json",
        RUN_FIELDS,
    ]
    print(f"CI_RUN_SUMMARY_FETCH_START run={run_id} repo={repository}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"ci-run-summary: gh run view failed: {exc}", file=sys.stderr)
        return None, 1
    if result.returncode != 0:
        detail = result.stderr.rstrip()
        if detail:
            print(detail, file=sys.stderr)
        else:
            print(
                f"ci-run-summary: gh run view failed with exit {result.returncode}",
                file=sys.stderr,
            )
        return None, result.returncode
    return _parse_payload(result.stdout, "gh run view"), 0


def _validate_identity(payload: Payload, run_id: str) -> bool:
    actual_id = payload.get("databaseId")
    if str(actual_id) != run_id:
        print(
            f"ci-run-summary: requested run {run_id} but received {actual_id}",
            file=sys.stderr,
        )
        return False
    head_sha = payload.get("headSha")
    if not isinstance(head_sha, str) or _FULL_SHA.fullmatch(head_sha) is None:
        print("ci-run-summary: invalid immutable head SHA", file=sys.stderr)
        return False
    return True


def _timestamp(value: object) -> datetime | None:
    """Parse source timestamps, rejecting unfinished and timezone-free values."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year < 1970 or parsed.utcoffset() is None:
        return None
    return parsed


def _elapsed(start: object, end: object) -> float | None:
    begin, finish = _timestamp(start), _timestamp(end)
    if begin is None or finish is None or finish < begin:
        return None
    return (finish - begin).total_seconds()


def _source_timestamp(job: Job, key: str) -> str | None:
    value = job.get(key)
    return value if isinstance(value, str) else None


def _timing_state(job: Job) -> str:
    status, conclusion = job.get("status"), job.get("conclusion")
    if conclusion == "failure":
        return "failed"
    if status in ("queued", "waiting", "pending", "requested"):
        return "pending"
    if status == "in_progress" or conclusion in ("cancelled", "timed_out", "skipped", "stale"):
        return "censored"
    if status == "completed" and conclusion == "success":
        return "success"
    return "unknown"


def _shard_timings(payload: Payload, run_id: str) -> Payload:
    """Describe source measurements without granting gate or release authority."""
    inventory = runpy.run_path(str(Path(__file__).with_name("ci_named_shard_files.py")))
    shards = tuple(cast(dict[str, object], inventory["SHARDS"]))
    raw_jobs = payload.get("jobs")
    jobs = [cast(Job, item) for item in raw_jobs if isinstance(item, dict)] if isinstance(raw_jobs, list) else []
    gate_legs = [job for job in jobs if _GATE_JOB.fullmatch(_value(job, "name"))]
    gates = [job for job in gate_legs if job.get("status") == "completed"]
    valid_gates = [(timestamp, job) for job in gates if (timestamp := _timestamp(job.get("completedAt"))) is not None]
    latest_gate = max(valid_gates, key=lambda pair: pair[0])[1] if valid_gates else None
    gate_completed = None if latest_gate is None else _source_timestamp(latest_gate, "completedAt")
    gate_states = [_timing_state(job) for job in gate_legs]
    gate_complete = (
        sorted(_value(job, "name") for job in gate_legs) == ["gate (3.11)", "gate (3.12)"]
        and all(state == "success" for state in gate_states)
        and len(valid_gates) == 2
    )
    gate_state = "success" if gate_complete else "unknown"
    for incomplete in ("failed", "censored", "pending"):
        if incomplete in gate_states:
            gate_state = incomplete
            break
    rows: list[Job] = []
    for shard in shards:
        matches = [
            job for job in jobs
            if (match := _SHARD_JOB.fullmatch(_value(job, "name"))) and match.group(1) == shard
        ]
        row: Job = {
            "shard": shard, "python_version": "3.11", "state": "unknown",
            "source_job_id": None, "source_job_name": None,
            "source_job_status": None, "source_job_conclusion": None,
            "source_job_started_at": None, "source_job_completed_at": None,
            "source_test_name": None, "source_test_status": None, "source_test_conclusion": None,
            "source_test_started_at": None, "source_test_completed_at": None,
            "test_step_seconds": None, "delay_from_gate_seconds": None,
            "test_step_duration_censored": True,
        }
        rows.append(row)
        if not matches:
            if payload.get("status") in ("queued", "waiting", "pending", "in_progress"):
                row["state"] = "pending"
            continue
        if len(matches) != 1:
            row["source_job_ids"] = [job.get("databaseId") for job in matches]
            continue
        job = matches[0]
        row.update({
            "state": _timing_state(job), "source_job_id": job.get("databaseId"),
            "source_job_name": job.get("name"), "source_job_status": job.get("status"),
            "source_job_conclusion": job.get("conclusion"),
            "source_job_started_at": _source_timestamp(job, "startedAt"),
            "source_job_completed_at": _source_timestamp(job, "completedAt"),
            "delay_from_gate_seconds": _elapsed(gate_completed, job.get("startedAt")) if gate_complete else None,
        })
        raw_steps = job.get("steps")
        steps = [
            cast(Job, step) for step in raw_steps
            if isinstance(step, dict) and step.get("name") == f"Test (shard {shard}, Python 3.11)"
        ] if isinstance(raw_steps, list) else []
        if len(steps) != 1:
            if row["state"] == "success":
                row["state"] = "unknown"
            continue
        step = steps[0]
        row.update({
            "source_test_name": step.get("name"), "source_test_status": step.get("status"),
            "source_test_conclusion": step.get("conclusion"),
            "source_test_started_at": _source_timestamp(step, "startedAt"),
            "source_test_completed_at": _source_timestamp(step, "completedAt"),
        })
        step_state = _timing_state(step)
        if step.get("status") == "completed" and step.get("conclusion") != "skipped":
            job_start, job_end = _timestamp(job.get("startedAt")), _timestamp(job.get("completedAt"))
            step_start, step_end = _timestamp(step.get("startedAt")), _timestamp(step.get("completedAt"))
            if (
                job_start is not None and job_end is not None
                and step_start is not None and step_end is not None
                and job_start <= step_start <= step_end <= job_end
                and _RUN_ID.fullmatch(str(job.get("databaseId"))) is not None
            ):
                row["test_step_seconds"] = _elapsed(step.get("startedAt"), step.get("completedAt"))
        if step_state == "failed":
            row["state"] = "failed"
        elif row["state"] == "success":
            row["state"] = step_state
            if step_state == "success" and (
                row["test_step_seconds"] is None or row["delay_from_gate_seconds"] is None
                or _elapsed(job.get("startedAt"), job.get("completedAt")) is None
            ):
                row["state"] = "unknown"
        row["test_step_duration_censored"] = row["state"] != "success"
    return {
        "run_id": run_id, "head_sha": payload["headSha"], "authority": "diagnostic-only",
        "release_ready": False, "gate_evidence": False,
        "gate_completed_at": gate_completed,
        "gate_barrier_complete": gate_complete, "gate_barrier_state": gate_state,
        "gate_job_id": None if latest_gate is None else latest_gate.get("databaseId"),
        "gate_job_name": None if latest_gate is None else latest_gate.get("name"),
        "gate_conclusion": None if latest_gate is None else latest_gate.get("conclusion"),
        "shards": rows,
    }


def _print_shard_timings(payload: Payload, run_id: str) -> None:
    print("SHARD_TIMINGS " + json.dumps(_shard_timings(payload, run_id), sort_keys=True))


def _summarize(payload: Payload, run_id: str | None = None) -> int:
    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list):
        print("ci-run-summary: jobs must be a JSON array", file=sys.stderr)
        return 1
    jobs = [cast(Job, job) for job in raw_jobs if isinstance(job, dict)]
    if len(jobs) != len(raw_jobs):
        print("ci-run-summary: every job must be a JSON object", file=sys.stderr)
        return 1
    if not jobs:
        print("No jobs found in CI run.")
        return 1

    if run_id is not None:
        print(
            f"RUN {run_id} WORKFLOW {_value(payload, 'workflowName')} "
            f"SHA {_value(payload, 'headSha')} STATUS {_value(payload, 'status')} "
            f"CONCLUSION {_value(payload, 'conclusion', '—')} "
            f"URL {_value(payload, 'url')}"
        )

    failed: list[Job] = []
    running: list[Job] = []
    passed: list[Job] = []
    other: list[Job] = []
    for job in jobs:
        status = _value(job, "status")
        conclusion = _value(job, "conclusion", "—")
        if conclusion == "failure":
            failed.append(job)
        elif status != "completed":
            running.append(job)
        elif conclusion == "success":
            passed.append(job)
        else:
            other.append(job)

    print(f"{'JOB':<34} {'STATUS':<12} {'CONCLUSION':<12}")
    print("-" * 60)
    _print_jobs("FAIL", failed)
    _print_jobs("··· ", running)
    _print_jobs("PASS", passed)
    _print_jobs("?   ", other)
    print()
    print(
        f"{len(failed)} failed, {len(running)} running, "
        f"{len(passed)} passed, {len(other)} other  ({len(jobs)} total)"
    )

    run_is_terminal = run_id is None or (
        payload.get("status") == "completed" and payload.get("conclusion") == "success"
    )
    return 0 if run_is_terminal and not failed and not running and not other else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", help="immutable numeric GitHub Actions run ID")
    parser.add_argument("--repo", default="sandboxcom/gludd", help="owner/repository")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--shard-timings", action="store_true", help="append diagnostic canonical 3.11 timing JSON")
    return parser


def _summary_outcome(payload: Payload, status: int) -> str:
    """Describe the existing result without changing its native status."""
    if status == 0:
        return "passed"
    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list) or any(not isinstance(job, dict) for job in raw_jobs):
        return "lookup-error"
    jobs = [cast(Job, job) for job in raw_jobs]
    if payload.get("conclusion") in ("failure", "cancelled", "timed_out", "skipped", "stale") or any(
        job.get("conclusion") == "failure"
        or (job.get("status") == "completed" and job.get("conclusion") != "success")
        for job in jobs
    ):
        return "failed"
    if payload.get("status") in ("queued", "waiting", "pending", "requested", "in_progress") or any(
        job.get("status") != "completed" for job in jobs
    ):
        return "pending"
    return "failed" if jobs and payload.get("status") == "completed" else "lookup-error"


def _report_result(
    status: int,
    outcome: str,
    *,
    run_id: str | None = None,
    repository: str | None = None,
    head_sha: str | None = None,
    identity: str = "unverified",
    scope: str = "legacy-listed-jobs",
) -> int:
    """Emit advisory stderr context while preserving stdout and native status."""
    safe_run = run_id if run_id is not None and _RUN_ID.fullmatch(run_id) else "unknown"
    safe_repo = repository if repository is not None and _REPOSITORY.fullmatch(repository) else "unknown"
    safe_sha = head_sha if head_sha is not None and _FULL_SHA.fullmatch(head_sha) else "unknown"
    next_owner, action = {
        "planned": ("ci-run-summary", "run-live-only-when-authorized;offline-plan-performs-no-lookup"),
        "pending": ("ci-await", "select-actual-branch-workflow-and-kind-before-bounded-await"),
        "lookup-error": ("ci-view", "repair-reported-lookup-or-identity-input-before-a-new-query"),
        "failed": ("ci-view", "inspect-exact-run-and-failed-or-censored-job-before-repair"),
        "passed": ("pipeline-status", "verify-caller-branch-sha-and-required-workflows"),
        "refused": ("ci-run-summary", "repair-literal-input-before-retry"),
    }[outcome]
    # Advisory output cannot replace the already determined native result.
    with suppress(OSError, ValueError):
        print(
            f"AGENT_NEXT target=ci-run-summary outcome={outcome} native_status={status} "
            f"run_id={safe_run} repository={safe_repo} head_sha={safe_sha} "
            f"identity={identity} scope={scope} authority=diagnostic-only "
            f"gate_evidence=false release_ready=false next_owner={next_owner} "
            f"no_unchanged_rerun=true action={action}",
            file=sys.stderr,
            flush=True,
        )
    return status


def main(argv: Sequence[str] = ()) -> int:
    """Validate inputs and summarize one immutable run or legacy stdin payload."""
    args = _parser().parse_args(argv)
    run_id = cast("str | None", args.run)
    repository = cast(str, args.repo)
    validate_only = cast(bool, args.validate_only)
    shard_timings = cast(bool, args.shard_timings)

    if run_id is None:
        if validate_only:
            print("ci-run-summary: --validate-only requires --run", file=sys.stderr)
            return _report_result(2, "refused", scope="offline-inputs")
        try:
            raw_payload = sys.stdin.read()
        except OSError as exc:
            print(f"ci-run-summary: failed to read JSON from stdin: {exc}", file=sys.stderr)
            return _report_result(1, "lookup-error")
        payload = _parse_payload(raw_payload, "stdin")
        if payload is None:
            return _report_result(1, "lookup-error")
        if shard_timings:
            source_id = str(payload.get("databaseId", ""))
            if _RUN_ID.fullmatch(source_id) is None or not _validate_identity(payload, source_id):
                print("ci-run-summary: immutable run identity is required for shard timings", file=sys.stderr)
                return _report_result(1, "lookup-error")
            result = _summarize(payload)
            _print_shard_timings(payload, source_id)
            return _report_result(
                result, _summary_outcome(payload, result), run_id=source_id,
                head_sha=cast(str, payload["headSha"]), identity="validated-stdin-id-sha",
                scope="stdin-listed-jobs-and-shard-timings",
            )
        result = _summarize(payload)
        return _report_result(result, _summary_outcome(payload, result))

    if _RUN_ID.fullmatch(run_id) is None:
        print("ci-run-summary: --run must be a positive numeric ID", file=sys.stderr)
        return _report_result(2, "refused", repository=repository, scope="offline-inputs")
    if _REPOSITORY.fullmatch(repository) is None:
        print("ci-run-summary: --repo must be owner/repository", file=sys.stderr)
        return _report_result(2, "refused", run_id=run_id, scope="offline-inputs")
    if validate_only:
        print(f"CI-RUN-SUMMARY VALIDATED run={run_id} repo={repository}")
        return _report_result(
            0, "planned", run_id=run_id, repository=repository,
            identity="requested-only", scope="offline-inputs",
        )

    payload, fetch_rc = _fetch_run(run_id, repository)
    if payload is None:
        return _report_result(
            fetch_rc or 1, "lookup-error", run_id=run_id, repository=repository,
            identity="requested-only", scope="run-lookup",
        )
    if not _validate_identity(payload, run_id):
        return _report_result(
            1, "lookup-error", run_id=run_id, repository=repository,
            identity="requested-only", scope="run-lookup",
        )
    result = _summarize(payload, run_id)
    if shard_timings:
        _print_shard_timings(payload, run_id)
    return _report_result(
        result, _summary_outcome(payload, result), run_id=run_id, repository=repository,
        head_sha=cast(str, payload["headSha"]), identity="validated-run-id-sha",
        scope="reported-run-and-listed-jobs",
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
