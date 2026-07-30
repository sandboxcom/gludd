"""Fail-closed GitHub Actions verdict for a release commit."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

Run = dict[str, Any]


def _detect_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else "development"
    except Exception:
        return "development"


def _matching_runs(runs: Sequence[Run], sha: str) -> list[Run]:
    matches = [
        run
        for run in runs
        if isinstance(run.get("headSha"), str)
        and str(run["headSha"]).startswith(sha)
    ]
    preferred = [
        run for run in matches if run.get("workflowName") == "Build and Release"
    ]
    return preferred or matches


def _evaluate_runs(runs: Sequence[Run], sha: str) -> tuple[int, str]:
    matches = _matching_runs(runs, sha)
    if not matches:
        return 1, f"CI RED: no run found for SHA {sha}"

    latest = max(matches, key=lambda run: int(run.get("databaseId") or 0))
    run_id = latest.get("databaseId", "?")
    status = latest.get("status")
    conclusion = latest.get("conclusion")

    if status == "completed" and conclusion == "success":
        return 0, f"CI GREEN: {sha} run {run_id}"
    if status in {"in_progress", "queued", "waiting", "pending", "requested"}:
        return 2, f"CI PENDING: {sha} run {run_id} status={status}"
    if status == "completed" and conclusion in {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
    }:
        return 1, f"CI RED: {sha} run {run_id} conclusion={conclusion}"
    return (
        1,
        "CI RED: "
        f"{sha} run {run_id} status={status} conclusion={conclusion} "
        "(fail-closed)",
    )


def _fetch_runs(sha: str, branch: str) -> list[Run]:
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--commit",
            sha,
            "--branch",
            branch,
            "-R",
            "sandboxcom/gludd",
            "--json",
            "conclusion,databaseId,status,headSha,workflowName,displayTitle",
            "--limit",
            "20",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"gh exited {result.returncode}"
        raise RuntimeError(detail)
    decoded = json.loads(result.stdout or "[]")
    if not isinstance(decoded, list):
        raise ValueError("GitHub Actions response is not a list")
    return decoded


def verdict_for(
    runs_or_sha: Sequence[Run] | str,
    sha_or_branch: str | None = None,
    *,
    branch: str | None = None,
) -> tuple[int, str] | int:
    """Evaluate supplied runs, or fetch and print the verdict for a commit SHA."""
    if not isinstance(runs_or_sha, str):
        if not sha_or_branch:
            raise ValueError("sha is required when evaluating supplied runs")
        return _evaluate_runs(runs_or_sha, sha_or_branch)

    sha = runs_or_sha
    selected_branch = branch or sha_or_branch or _detect_branch()
    try:
        runs = _fetch_runs(sha, selected_branch)
        code, message = _evaluate_runs(runs, sha)
    except Exception as error:
        code = 1
        message = f"CI RED: {sha} query failed ({error}) (fail-closed)"
    print(message)
    return code


def main() -> int:
    sha = sys.argv[1] if len(sys.argv) > 1 else None
    branch = sys.argv[2] if len(sys.argv) > 2 else None
    if not sha:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = result.stdout.strip()
    if not sha:
        print("CI RED: unable to determine HEAD SHA (fail-closed)")
        return 1
    verdict = verdict_for(sha, branch)
    return int(verdict)


if __name__ == "__main__":
    sys.exit(main())
