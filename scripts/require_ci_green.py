#!/usr/bin/env python3
"""
require_ci_green.py — CI-green precondition check for release-cut.

Usage:
    python scripts/require_ci_green.py [SHA]

Exit codes:
    0  — CI GREEN: completed + success
    1  — CI RED or no matching run found (fail-closed)
    2  — CI PENDING: run still in_progress or queued

If SHA is omitted, uses current HEAD (via `git rev-parse HEAD`).
Repo is resolved from `git remote get-url sandboxcom` or falls back to
sandboxcom/gludd.
"""

from __future__ import annotations

import json
import subprocess
import sys


WORKFLOW_NAME = "Build and Release"
FALLBACK_REPO = "sandboxcom/gludd"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError as exc:
        return 1, "", str(exc)
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out running {cmd[0]}"


def _resolve_repo() -> str:
    """Return 'owner/repo' from the sandboxcom remote, or the fallback."""
    rc, out, _ = _run(["git", "remote", "get-url", "sandboxcom"])
    if rc == 0 and out:
        # git@github.com:owner/repo.git  or  https://github.com/owner/repo.git
        url = out.rstrip(".git")
        if "github.com:" in url:
            return url.split("github.com:")[-1]
        if "github.com/" in url:
            parts = url.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return "/".join(parts[:2])
    return FALLBACK_REPO


def _resolve_sha(sha: str | None) -> str | None:
    """Return SHA string; None on error."""
    if sha:
        return sha.strip()
    rc, out, err = _run(["git", "rev-parse", "HEAD"])
    if rc != 0:
        print(f"ERROR: could not resolve HEAD SHA: {err}", file=sys.stderr)
        return None
    return out.strip()


def _fetch_runs(repo: str, limit: int = 40) -> list[dict] | None:
    """
    Call `gh run list` and return parsed JSON list, or None on failure.
    Uses --repo flag so it works from any cwd.
    """
    rc, out, err = _run([
        "gh", "run", "list",
        "--repo", repo,
        "--json", "databaseId,headSha,status,conclusion,workflowName,displayTitle",
        "--limit", str(limit),
    ])
    if rc != 0:
        msg = err or "gh run list failed with no stderr"
        print(f"ERROR: gh run list failed: {msg}", file=sys.stderr)
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse gh output as JSON: {exc}", file=sys.stderr)
        return None


def verdict_for(runs: list[dict], sha: str) -> tuple[int, str]:
    """
    Pure decision function — given a list of gh run dicts and a target SHA,
    return (exit_code, message).

    Rules (applied in order across runs filtered to headSha==sha):
      - Among matching runs, prefer "Build and Release" workflow; fall back to any.
      - Among matching workflow runs, pick the one with the highest databaseId
        (most recent trigger) as the authoritative run.
      - completed + success  -> (0, "CI GREEN: <sha> run <id>")
      - in_progress / queued / waiting / pending -> (2, "CI PENDING: ...")
      - anything else (failure, cancelled, timed_out, ...) -> (1, "CI RED: ...")
      - no matching run at all -> (1, "CI RED: no run found for SHA <sha>")
    """
    # Filter to matching SHA
    sha_lower = sha.lower()
    matching = [r for r in runs if r.get("headSha", "").lower().startswith(sha_lower)]

    if not matching:
        return 1, f"CI RED: no run found for SHA {sha}"

    # Prefer "Build and Release"; if none, take all matching
    preferred = [r for r in matching if r.get("workflowName") == WORKFLOW_NAME]
    candidates = preferred if preferred else matching

    # Pick most recent by databaseId
    run = max(candidates, key=lambda r: r.get("databaseId", 0))
    run_id = run.get("databaseId", "?")
    status = run.get("status", "")
    conclusion = run.get("conclusion") or ""

    # Decision
    if status == "completed":
        if conclusion == "success":
            return 0, f"CI GREEN: {sha} run {run_id}"
        return 1, f"CI RED: {sha} run {run_id} conclusion={conclusion!r}"

    # Still running
    if status in ("in_progress", "queued", "waiting", "pending", "requested"):
        return 2, f"CI PENDING: {sha} run {run_id} status={status!r}"

    # Unknown status — fail-closed
    return 1, f"CI RED: {sha} run {run_id} status={status!r} conclusion={conclusion!r} (unknown — fail-closed)"


def main(argv: list[str]) -> int:
    sha_arg = argv[1] if len(argv) > 1 else None
    sha = _resolve_sha(sha_arg)
    if not sha:
        print("CI RED: could not determine SHA (fail-closed)")
        return 1

    repo = _resolve_repo()

    runs = _fetch_runs(repo)
    if runs is None:
        # gh not installed / not authed / no network
        print(f"CI RED: could not fetch runs from {repo} — gh unavailable or not authenticated (fail-closed)")
        return 1

    code, msg = verdict_for(runs, sha)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
