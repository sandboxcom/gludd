#!/usr/bin/env python3
"""Print a concise, fail-closed summary for one immutable GitHub Actions run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from typing import cast

Job = dict[str, object]
Payload = dict[str, object]

RUN_FIELDS = "databaseId,headSha,status,conclusion,url,workflowName,jobs"
_RUN_ID = re.compile(r"[1-9][0-9]*\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


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
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
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
    return parser


def main(argv: Sequence[str] = ()) -> int:
    args = _parser().parse_args(argv)
    run_id = cast("str | None", args.run)
    repository = cast(str, args.repo)
    validate_only = cast(bool, args.validate_only)

    if run_id is None:
        if validate_only:
            print("ci-run-summary: --validate-only requires --run", file=sys.stderr)
            return 2
        try:
            raw_payload = sys.stdin.read()
        except OSError as exc:
            print(f"ci-run-summary: failed to read JSON from stdin: {exc}", file=sys.stderr)
            return 1
        payload = _parse_payload(raw_payload, "stdin")
        return 1 if payload is None else _summarize(payload)

    if _RUN_ID.fullmatch(run_id) is None:
        print("ci-run-summary: --run must be a positive numeric ID", file=sys.stderr)
        return 2
    if _REPOSITORY.fullmatch(repository) is None:
        print("ci-run-summary: --repo must be owner/repository", file=sys.stderr)
        return 2
    if validate_only:
        print(f"CI-RUN-SUMMARY VALIDATED run={run_id} repo={repository}")
        return 0

    payload, fetch_rc = _fetch_run(run_id, repository)
    if payload is None:
        return fetch_rc or 1
    if not _validate_identity(payload, run_id):
        return 1
    return _summarize(payload, run_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
