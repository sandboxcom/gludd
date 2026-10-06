"""Exact-SHA local-gate and hosted-workflow status.

The hosted side deliberately queries every workflow for one exact commit and
then selects the newest push or manual-dispatch run per workflow. Missing
evidence, GitHub lookup errors, and non-terminal runs are not green states.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPO = "sandboxcom/gludd"
DEFAULT_BRANCH = "development"
DEFAULT_REMOTE = "sandboxcom"
DEFAULT_REQUIRED_WORKFLOWS = ("Build and Release", "Molecule Tests")
DEFAULT_RUN_EVENTS = frozenset(("push", "workflow_dispatch"))
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
NON_TERMINAL = {"in_progress", "pending", "queued", "requested", "waiting"}

Runner = Callable[..., subprocess.CompletedProcess[str]]


class PipelineStatusError(RuntimeError):
    """Signal that exact hosted-pipeline evidence could not be collected."""


@dataclass(frozen=True)
class WorkflowRun:
    """One newest exact-SHA workflow run."""

    workflow: str
    run_id: int
    status: str
    conclusion: str
    url: str

    @property
    def state(self) -> str:
        """Return success, pending, or failure using fail-closed semantics."""
        if self.status in NON_TERMINAL or not self.conclusion:
            return "pending"
        if self.status == "completed" and self.conclusion == "success":
            return "success"
        return "failure"


@dataclass(frozen=True)
class PipelineSummary:
    """All newest workflow results bound to one exact SHA."""

    sha: str
    branch: str
    runs: tuple[WorkflowRun, ...]
    missing_workflows: tuple[str, ...]

    @property
    def failures(self) -> tuple[WorkflowRun, ...]:
        """Return terminal runs whose conclusion is not successful."""
        return tuple(run for run in self.runs if run.state == "failure")

    @property
    def pending(self) -> tuple[WorkflowRun, ...]:
        """Return runs that have not reached a terminal conclusion."""
        return tuple(run for run in self.runs if run.state == "pending")

    @property
    def successes(self) -> tuple[WorkflowRun, ...]:
        """Return completed successful runs."""
        return tuple(run for run in self.runs if run.state == "success")

    @property
    def exit_code(self) -> int:
        """Return 0 green, 1 red, or 2 incomplete/error-compatible."""
        if self.failures:
            return 1
        if not self.runs or self.pending or self.missing_workflows:
            return 2
        return 0

    def lines(self) -> tuple[str, ...]:
        """Render a compact result that never hides a sibling workflow."""
        output = [f"REMOTE CI branch={self.branch} sha={self.sha}"]
        if not self.runs:
            output.append("INCOMPLETE no workflow runs found for exact SHA")
        for run in self.runs:
            marker = {"success": "PASS", "pending": "PENDING", "failure": "FAIL"}[
                run.state
            ]
            suffix = f" url={run.url}" if run.url else ""
            output.append(
                f"{marker} {run.workflow} run {run.run_id} "
                f"status={run.status or '?'} conclusion={run.conclusion or 'none'}{suffix}"
            )
        output.extend(
            f"INCOMPLETE missing required workflow: {workflow}"
            for workflow in self.missing_workflows
        )
        output.append(
            "SUMMARY "
            f"{len(self.failures)} failed, {len(self.pending)} pending, "
            f"{len(self.successes)} passed, {len(self.missing_workflows)} missing"
        )
        return tuple(output)


def _run_id(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("databaseId") or 0)
    except (TypeError, ValueError):
        return 0


def _newest_key(payload: dict[str, Any]) -> tuple[str, int]:
    return str(payload.get("createdAt") or ""), _run_id(payload)


def evaluate_runs(
    runs: Sequence[dict[str, Any]],
    sha: str,
    *,
    branch: str,
    events: Collection[str] = DEFAULT_RUN_EVENTS,
    required_workflows: Sequence[str] = DEFAULT_REQUIRED_WORKFLOWS,
) -> PipelineSummary:
    """Evaluate the newest eligible exact-SHA run for every workflow."""
    matching = [
        run
        for run in runs
        if str(run.get("headSha") or "") == sha
        and str(run.get("headBranch") or "") == branch
        and str(run.get("event") or "") in events
        and str(run.get("workflowName") or "").strip()
    ]
    newest: dict[str, dict[str, Any]] = {}
    for run in matching:
        workflow = str(run["workflowName"])
        if workflow not in newest or _newest_key(run) > _newest_key(newest[workflow]):
            newest[workflow] = run

    ordered_names = [name for name in required_workflows if name in newest]
    ordered_names.extend(sorted(set(newest) - set(ordered_names)))
    normalized = tuple(
        WorkflowRun(
            workflow=name,
            run_id=_run_id(newest[name]),
            status=str(newest[name].get("status") or "").lower(),
            conclusion=str(newest[name].get("conclusion") or "").lower(),
            url=str(newest[name].get("url") or ""),
        )
        for name in ordered_names
    )
    missing = tuple(name for name in required_workflows if name not in newest)
    return PipelineSummary(
        sha=sha,
        branch=branch,
        runs=normalized,
        missing_workflows=missing,
    )


def fetch_runs(
    sha: str,
    *,
    repo: str,
    runner: Runner = subprocess.run,
) -> list[dict[str, Any]]:
    """Fetch every workflow run for a full commit SHA through the GitHub CLI."""
    if SHA_RE.fullmatch(sha) is None:
        raise PipelineStatusError("SHA must be a full lowercase 40-character hash")
    fields = (
        "conclusion,databaseId,status,headSha,headBranch,workflowName,event,"
        "createdAt,url"
    )
    command = [
        "gh",
        "run",
        "list",
        "--commit",
        sha,
        "--limit",
        "100",
        "-R",
        repo,
        "--json",
        fields,
    ]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        raise PipelineStatusError(f"GitHub run lookup failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "gh run list failed").strip()
        raise PipelineStatusError(f"GitHub run lookup failed: {detail}")
    try:
        payload: object = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise PipelineStatusError(f"GitHub run lookup returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise PipelineStatusError("GitHub run lookup returned a non-list value")
    if len(payload) >= 100:
        raise PipelineStatusError("GitHub run lookup reached its limit; evidence may be incomplete")
    return [item for item in payload if isinstance(item, dict)]


def remote_head(
    *,
    branch: str,
    remote: str,
    runner: Runner = subprocess.run,
) -> str:
    """Resolve the exact pushed head for the requested branch."""
    command = ["git", "ls-remote", remote, f"refs/heads/{branch}"]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        raise PipelineStatusError(f"remote head lookup failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git ls-remote failed").strip()
        raise PipelineStatusError(f"remote head lookup failed: {detail}")
    first_line = next((line for line in result.stdout.splitlines() if line.strip()), "")
    sha = first_line.split(maxsplit=1)[0] if first_line else ""
    if SHA_RE.fullmatch(sha) is None:
        raise PipelineStatusError(f"no remote head for {remote}/refs/heads/{branch}")
    return sha


def local_gate() -> None:
    """Print the current local gate marker and stale/dead diagnostics."""
    gate = Path(".gate-status")
    pid_file = Path(".gate-background.pid")
    print("=== LOCAL GATE ===")
    if not gate.exists():
        print("( no gate status file )")
        return
    print(gate.read_text(encoding="utf-8"), end="")
    if not pid_file.exists():
        return
    pid = pid_file.read_text(encoding="utf-8").strip()
    if not pid:
        return
    try:
        os.kill(int(pid), 0)
        age = int(time.time()) - int(gate.stat().st_mtime)
        if age > 120:
            print(f"  STALLED: .gate-status not updated for {age} seconds")
    except (OSError, ValueError):
        print(f"  DEAD: pid={pid} no longer running")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("status",), default="status")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--sha", default="")
    parser.add_argument("--remote-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--required-workflow",
        action="append",
        dest="required_workflows",
        default=None,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print local and complete remote state, returning a fail-closed verdict."""
    args = _parser().parse_args(argv)
    if args.validate_only:
        sha = args.sha or "0" * 40
        runs = [
            {
                "workflowName": workflow,
                "databaseId": index,
                "status": "completed",
                "conclusion": "success",
                "headSha": sha,
                "headBranch": args.branch,
                "event": "push",
                "createdAt": f"2026-01-01T00:00:0{index}Z",
                "url": "",
            }
            for index, workflow in enumerate(DEFAULT_REQUIRED_WORKFLOWS, start=1)
        ]
        summary = evaluate_runs(runs, sha, branch=args.branch)
        print("\n".join(summary.lines()))
        print("PIPELINE_STATUS_VALIDATE_ONLY_PASS")
        return summary.exit_code
    if not args.remote_only:
        local_gate()
    print("\n=== REMOTE CI (exact pushed SHA, all workflows) ===")
    try:
        sha = args.sha or remote_head(branch=args.branch, remote=args.remote)
        summary = evaluate_runs(
            fetch_runs(sha, repo=args.repo),
            sha,
            branch=args.branch,
            required_workflows=(
                tuple(args.required_workflows)
                if args.required_workflows
                else DEFAULT_REQUIRED_WORKFLOWS
            ),
        )
    except PipelineStatusError as exc:
        print(f"CI ERROR: {exc}")
        return 2
    print("\n".join(summary.lines()))
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
