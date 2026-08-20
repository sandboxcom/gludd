"""Fail-closed GitHub Actions verdict for one exact commit.

The release workflow must never treat a successful run for a different commit,
or a cancelled/skipped run, as evidence that the requested commit passed CI.
``verdict_from_runs`` is pure so the matching rules stay unit-testable without
network access; ``verdict_for`` is the small ``gh`` adapter used by Make.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Any


def _detect_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else "development"
    except Exception:
        return "development"


def _run_id(run: dict[str, Any]) -> int:
    value = run.get("databaseId", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def verdict_from_runs(runs: Sequence[dict[str, Any]], sha: str) -> tuple[int, str]:
    """Return ``(exit_code, message)`` for runs against an exact full SHA.

    A full-string SHA comparison is intentional. Prefix matching can accept a
    stale-success run when callers mix abbreviated local refs with API output.
    Cancelled and skipped conclusions are red because neither is evidence of a
    successful test execution.
    """
    matching = [run for run in runs if str(run.get("headSha", "")) == sha]
    if not matching:
        return 1, f"CI RED: no run found for SHA {sha}"

    preferred = [
        run for run in matching if run.get("workflowName") == "Build and Release"
    ]
    candidates = preferred or matching
    latest = max(candidates, key=_run_id)
    run_id = latest.get("databaseId", "?")
    status = str(latest.get("status", "")).lower()
    conclusion = str(latest.get("conclusion") or "").lower()

    if status in {"queued", "in_progress", "waiting", "requested", "pending"}:
        return 2, f"CI PENDING: {sha} run {run_id} status={status}"
    if status not in {"completed", ""}:
        return 1, f"CI RED: {sha} run {run_id} status={status} (fail-closed)"
    if conclusion == "success":
        return 0, f"CI GREEN: {sha} run {run_id}"
    if conclusion in {"failure", "cancelled", "skipped", "timed_out"}:
        return 1, f"CI RED: {sha} run {run_id} conclusion={conclusion}"
    return 1, (
        f"CI RED: {sha} run {run_id} conclusion={conclusion or '?'} "
        "(fail-closed)"
    )


def _fetch_runs(sha: str, branch: str) -> list[dict[str, Any]]:
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
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run list failed").strip()
        raise RuntimeError(detail)
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise ValueError("gh run list returned a non-list payload")
    return [item for item in payload if isinstance(item, dict)]


def verdict_for(
    source: Sequence[dict[str, Any]] | str,
    sha_or_branch: str | None = None,
    *,
    branch: str | None = None,
) -> Any:
    """Evaluate supplied runs (tests) or query ``gh`` (CLI).

    The sequence form returns ``(code, message)``. The string form prints the
    message and returns the process exit code for compatibility with Make.
    """
    if branch is not None and sha_or_branch is not None:
        raise TypeError("pass the branch either positionally or by keyword, not both")
    if not isinstance(source, str):
        if branch is not None:
            raise TypeError("branch is only valid when querying CI for a commit")
        return verdict_from_runs(source, sha_or_branch or "")

    sha = source
    selected_branch = branch or sha_or_branch or _detect_branch()
    try:
        code, message = verdict_from_runs(_fetch_runs(sha, selected_branch), sha)
    except Exception as exc:
        print(f"CI ERROR: {exc}")
        return 2
    print(message)
    return code


if __name__ == "__main__":
    commit = sys.argv[1] if len(sys.argv) > 1 else ""
    if not commit:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
        commit = result.stdout.strip()
    sys.exit(verdict_for(commit, sys.argv[2] if len(sys.argv) > 2 else None))
