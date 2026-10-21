"""Exact-SHA local-gate and hosted-workflow status.

The hosted side deliberately queries every workflow for one pushed commit and
then selects the newest push run per workflow. Missing evidence, GitHub lookup
errors, and non-terminal runs are not green states.
"""

from __future__ import annotations

import argparse
import json
import math
import os as os
import re
import subprocess
import sys
import time as time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import active_work_status
    import automatic_disk_cleanup
    import check_disk_usage
    from proof_observations import write_snapshot

    from scripts import ci_check_cooldown, ci_failure_ledger
else:
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import (
        active_work_status,
        automatic_disk_cleanup,
        check_disk_usage,
        ci_check_cooldown,
        ci_failure_ledger,
    )
    from scripts.proof_observations import write_snapshot

DEFAULT_REPO = "sandboxcom/gludd"
DEFAULT_BRANCH = "development"
DEFAULT_REMOTE = "sandboxcom"
DEFAULT_REQUIRED_WORKFLOWS = ("Build and Release", "Molecule Tests")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
NON_TERMINAL = {"in_progress", "pending", "queued", "requested", "waiting"}
_JOB_FAILURE_STATES = ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale")
_JOB_TERMINAL_STATES = ("success", *_JOB_FAILURE_STATES, "neutral", "skipped")
_JOB_PROGRESS_STATES = ("queued", "pending", "requested", "waiting", "in_progress", *_JOB_TERMINAL_STATES, "unknown")
_JOB_PROGRESS_LIMIT = 8
_JOB_PROGRESS_NAME_LIMIT = 64

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
        """Return every newest run with a terminal non-success result."""
        return tuple(run for run in self.runs if run.state == "failure")

    @property
    def pending(self) -> tuple[WorkflowRun, ...]:
        """Return newest runs that have not reached a terminal result."""
        return tuple(run for run in self.runs if run.state == "pending")

    @property
    def successes(self) -> tuple[WorkflowRun, ...]:
        """Return only newest runs with explicit terminal success."""
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


def _compatibility_verdict_line(summary: PipelineSummary, exit_code: int) -> str:
    """Render the operational outcome for existing consumers, never synthetic proof."""
    marker = {0: "CI GREEN", 1: "CI RED", 2: "CI PENDING"}[exit_code]
    run_ids = ",".join(str(run.run_id) for run in summary.runs) or "none"
    status = (
        summary.pending[0].status if summary.pending
        else "incomplete" if summary.missing_workflows or not summary.runs
        else "completed"
    )
    conclusion = (
        "success" if exit_code == 0
        else ",".join(dict.fromkeys(run.conclusion for run in summary.failures)) if exit_code == 1
        else "none"
    )
    return (
        f"{marker}: sha={summary.sha} run={run_ids} "
        f"status='{status}' conclusion={conclusion} branch={summary.branch}"
    )


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
    event: str = "push",
    required_workflows: Sequence[str] = DEFAULT_REQUIRED_WORKFLOWS,
) -> PipelineSummary:
    """Evaluate newest exact-SHA run for every workflow, not merely one run."""
    matching = [
        run
        for run in runs
        if str(run.get("headSha") or "") == sha
        and str(run.get("headBranch") or "") == branch
        and str(run.get("event") or "") == event
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
    """Print lifecycle-marker age and PID liveness without inferring progress."""
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
            print(
                f"  GATE-MARKER: marker_age_seconds={age} pid={pid} pid_probe=exists "
                "gate_identity=unverified progress=unknown (lifecycle marker, not a progress heartbeat)"
            )
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
    publication = parser.add_mutually_exclusive_group()
    publication.add_argument("--snapshot-output", type=Path)
    publication.add_argument(
        "--record-verdict", action="store_true",
        help="record a complete terminal all-workflow verdict through the existing history owner",
    )
    parser.add_argument("--failure-ledger", type=Path, default=Path(".gludd/ci-failure-ledger.json"))
    observation = parser.add_mutually_exclusive_group()
    observation.add_argument(
        "--observe-ledger", action="store_true",
        help="run the existing failure-ledger observation before the independent status query",
    )
    observation.add_argument("--ledger-observation-exit-code", type=int)
    parser.add_argument(
        "--required-workflow",
        action="append",
        dest="required_workflows",
        default=None,
    )
    return parser


def _terminal_verdict(
    args: argparse.Namespace, summary: PipelineSummary | None, sha: str | None,
) -> str | None:
    """Authorize history only for a canonical, complete, exact terminal observation."""
    if (summary is None or args.repo != DEFAULT_REPO or not args.branch.strip()
            or summary.sha != sha or summary.branch != args.branch
            or SHA_RE.fullmatch(summary.sha) is None
            or args.ledger_observation_exit_code
            or not summary.runs or summary.pending or summary.missing_workflows
            or not set(DEFAULT_REQUIRED_WORKFLOWS).issubset(run.workflow for run in summary.runs)
            or any(run.status != "completed" or not run.conclusion for run in summary.runs)):
        return None
    return "failure" if summary.failures else "success"


def _job_progress_state(record: dict[str, object]) -> str:
    """Classify diagnostic metadata without changing observation acceptance."""
    status, conclusion = record.get("status"), record.get("conclusion")
    if isinstance(status, str) and status in NON_TERMINAL and conclusion in ("", None):
        return status
    if status == "completed" and isinstance(conclusion, str) and conclusion in _JOB_TERMINAL_STATES:
        return conclusion
    return "unknown"


def _job_progress_name(value: object) -> str:
    """Reuse bounded native control sanitization and inert standard JSON quoting."""
    if not isinstance(value, str) or not value:
        return "unknown"
    safe = active_work_status._safe_bounded_text(
        value[:_JOB_PROGRESS_NAME_LIMIT + 1], limit=_JOB_PROGRESS_NAME_LIMIT,
    )
    quoted = json.dumps(safe, ensure_ascii=True)
    return quoted + ("[truncated]" if len(value) > _JOB_PROGRESS_NAME_LIMIT else "")


def _latest_job_step(steps: Sequence[dict[str, object]]) -> str:
    """Select only an unambiguous last step, never an arbitrary array position."""
    if not steps:
        return "none"
    if len(steps) == 1:
        return _job_progress_name(steps[0].get("name"))
    numbered: list[tuple[int, dict[str, object]]] = []
    for step in steps:
        number = step.get("number")
        if type(number) is not int or number <= 0:
            return "unknown"
        numbered.append((number, step))
    if len({number for number, _step in numbered}) != len(numbered):
        return "unknown"
    return _job_progress_name(max(numbered, key=lambda item: item[0])[1].get("name"))


def _job_step_progress(job: dict[str, object], state: str) -> tuple[str, str, int]:
    """Describe known current/problem and successful steps with explicit unknowns."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return "unknown", "unknown", 0
    active = [step for step in steps if _job_progress_state(step) == "in_progress"]
    successful = [step for step in steps if _job_progress_state(step) == "success"]
    if active:
        current = _job_progress_name(active[0].get("name")) if len(active) == 1 else "unknown"
    elif state in _JOB_FAILURE_STATES:
        failed = [step for step in steps if _job_progress_state(step) in _JOB_FAILURE_STATES]
        current = _latest_job_step(failed) if failed else "unknown"
    else:
        current = "unknown"
    return current, _latest_job_step(successful), len(active)


def _job_progress_lines(run: WorkflowRun, jobs: Sequence[dict[str, object]]) -> tuple[str, ...]:
    """Render complete counts and bounded active/failed/unknown rows from one payload."""
    groups: dict[str, list[dict[str, object]]] = {state: [] for state in _JOB_PROGRESS_STATES}
    for job in jobs:
        groups[_job_progress_state(job)].append(job)
    candidates = [
        (state, job)
        for state in ("in_progress", *_JOB_FAILURE_STATES, "unknown")
        for job in groups[state]
    ]
    shown = min(len(candidates), _JOB_PROGRESS_LIMIT)
    omitted = len(candidates) - shown
    counts = " ".join(f"{state}={len(groups[state])}" for state in _JOB_PROGRESS_STATES)
    lines = [
        f"PIPELINE-JOBS run={run.run_id} workflow={_job_progress_name(run.workflow)} "
        f"total={len(jobs)} {counts} progress_shown={shown} progress_omitted={omitted}"
    ]
    for state, job in candidates[:shown]:
        current, latest_success, active_steps = _job_step_progress(job, state)
        lines.append(
            f"PIPELINE-JOB-PROGRESS run={run.run_id} state={state} job={_job_progress_name(job.get('name'))} "
            f"current={current} latest_success={latest_success} active_steps={active_steps}"
        )
    if omitted:
        lines.append(f"PIPELINE-JOBS-TRUNCATED run={run.run_id} omitted={omitted} limit={_JOB_PROGRESS_LIMIT}")
    return tuple(lines)


def _publish_snapshot(
    args: argparse.Namespace, summary: PipelineSummary | None,
    *, sha: str | None, error: str | None = None,
) -> bool:
    """Generate the current handoff from authorities, never from agent notes."""
    if args.snapshot_output is None:
        return True
    errors = [error] if error is not None else []
    if args.ledger_observation_exit_code:
        errors.append(f"failure-ledger observation failed rc={args.ledger_observation_exit_code}")
    local: dict[str, object] | None = None
    capacity: dict[str, object] | None = None
    ledger_status: dict[str, object] | None = None
    ledger: dict[str, Any] | None = None
    hosted: dict[str, Any] | None = asdict(summary) if summary is not None else None
    hosted_failures: list[dict[str, object]] = []
    terminal_observed = False
    print("PIPELINE-SNAPSHOT-START", flush=True)
    try:
        local = active_work_status.collect_status()
        print("PIPELINE-SNAPSHOT-CAPACITY-START", flush=True)
        measured = automatic_disk_cleanup.inspect_usage()
        if (isinstance(measured.scratch_mb, bool) or isinstance(measured.disk_pct, bool)
                or not math.isfinite(measured.scratch_mb) or measured.scratch_mb < 0
                or not math.isfinite(measured.disk_pct) or not 0 <= measured.disk_pct <= 100):
            raise ValueError("capacity inspection produced invalid usage")
        capacity = {
            **asdict(measured), "hard_blocked": measured.has_hard_pressure,
            "disk_limit_pct": check_disk_usage.DISK_USAGE_PCT_LIMIT,
            "disk_limit_kind": "hard",
            "scratch_limit_mb": check_disk_usage.GLUDD_TMP_LIMIT_MB,
            "scratch_limit_kind": "soft",
        }
        print(f"PIPELINE-SNAPSHOT-CAPACITY-END hard_blocked={measured.has_hard_pressure}", flush=True)
        ledger = ci_failure_ledger.read_ledger(args.failure_ledger)
        ledger_status = {
            "path": str(args.failure_ledger), "observed": args.failure_ledger.exists(),
            "counts": {state: sum(family["status"] == state for family in ledger["families"].values())
                       for state in ("open", "repaired", "resolved")},
            "runs": len(ledger["runs"]),
        }
        if summary is not None and hosted is not None:
            terminal_observed = bool(summary.runs) and not summary.pending and not summary.missing_workflows
            for record, run in zip(hosted["runs"], summary.runs, strict=True):
                print(f"PIPELINE-SNAPSHOT-JOBS workflow={run.workflow!r} run={run.run_id}", flush=True)
                detail = ci_failure_ledger.fetch_run(run.run_id, args.repo)
                if (detail.get("databaseId") != run.run_id
                        or detail.get("headSha") != summary.sha
                        or detail.get("headBranch") != summary.branch
                        or detail.get("workflowName") != run.workflow
                        or detail.get("event", "push") != "push"):
                    raise ValueError("job observation identity differs from the exact workflow")
                if run.state == "success" and (
                    detail.get("status") != "completed" or detail.get("conclusion") != "success"
                ):
                    raise ValueError("job observation no longer confirms workflow success")
                jobs = detail.get("jobs")
                if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
                    raise ValueError("job observation has malformed jobs")
                if any(not isinstance(job.get("steps", []), list)
                       or any(not isinstance(step, dict) for step in job.get("steps", [])) for job in jobs):
                    raise ValueError("job observation has malformed steps")
                if run.state == "success" and any(job.get("conclusion") == "failure" for job in jobs):
                    raise ValueError("job observation contradicts workflow success")
                terminal_observed = terminal_observed and (
                    detail.get("status") == "completed"
                    and detail.get("conclusion") == run.conclusion
                )
                record["jobs"] = jobs
                record["source_created_at"] = detail.get("createdAt")
                record["source_updated_at"] = detail.get("updatedAt")
                record["source_attempt"] = detail.get("attempt")
                hosted_failures.extend({
                    "workflow": run.workflow, "run_id": run.run_id,
                    "job": job.get("name"),
                    "steps": [step.get("name") for step in job.get("steps", [])
                              if isinstance(step, dict) and step.get("conclusion") == "failure"],
                } for job in jobs if job.get("conclusion") == "failure")
                for line in _job_progress_lines(run, jobs):
                    print(line, flush=True)
    except (OSError, ValueError, subprocess.SubprocessError, ci_failure_ledger.LedgerError,
            automatic_disk_cleanup.DiskInspectionError) as exc:
        errors.append(str(exc))
    state = ("lookup_error" if errors else
             "success" if summary is not None and summary.exit_code == 0 else
             "failure" if hosted_failures or (summary is not None and summary.exit_code == 1) else "pending")
    local_git = local.get("git") if local is not None else None
    implementation_sha = local_git.get("head") if isinstance(local_git, dict) else None
    repair_guidance: list[ci_failure_ledger.RepairGuidance] = []
    try:
        if state == "failure" and ledger is not None:
            clean_candidate = False
            candidate = implementation_sha if isinstance(implementation_sha, str) else ""
            if any(
                family.get("branch") == args.branch and family.get("status") == "open"
                for family in ledger["families"].values()
            ):
                try:
                    ci_failure_ledger._require_repair_checkout(candidate)
                except ci_failure_ledger.LedgerError:
                    pass
                else:
                    clean_candidate = True
            repair_guidance = ci_failure_ledger.build_repair_guidance(
                ledger, ledger_path=args.failure_ledger, branch=args.branch,
                repair_sha=candidate, checkout_clean=clean_candidate,
            )
    except (OSError, ValueError, KeyError, ci_failure_ledger.LedgerError) as exc:
        errors.append(str(exc))
        state = "lookup_error"
    open_tasks = local.get("open_task_ids", []) if local is not None else []
    next_action = ("resolve_observation_error" if errors else
                   "repair_hosted_failure" if state == "failure" else
                   "clear_hard_capacity_before_local_proofs" if capacity is not None and capacity["hard_blocked"] else
                   "collect_exact_final_proofs" if state == "success" else
                   "resolve_or_await_hosted_evidence")
    missing_proofs = {
        task_id: {
            "implementation_sha": implementation_sha, "hosted_sha": sha,
            "hosted_status": state, "canonical_gate": "not_collected",
            "canonical_coverage": "not_collected", "source_review": "not_collected",
            "next_action": next_action,
        }
        for task_id in open_tasks if isinstance(task_id, str)
    } if isinstance(open_tasks, list) else {}
    def publish() -> bool:
        try:
            write_snapshot(
                args.snapshot_output, producer="pipeline-status",
                identity={"repo": args.repo, "branch": args.branch, "sha": sha}, state=state,
                evidence={"local": local, "hosted": hosted, "failure_ledger": ledger_status,
                           "missing_proofs": missing_proofs, "hosted_failures": hosted_failures,
                            "capacity": capacity, "errors": errors, "repair_guidance": repair_guidance},
            )
        except (OSError, ValueError) as exc:
            print(f"PIPELINE-SNAPSHOT-BLOCKED error={exc}", flush=True)
            return False
        return True

    if not publish():
        return False
    verdict = _terminal_verdict(args, summary, sha)
    if terminal_observed and not errors and verdict is not None:
        try:
            history = ci_check_cooldown.load_history()
            if history.get("last_push_sha") == sha and (
                history.get("last_checked_sha") != sha or history.get("last_verdict") != verdict
            ):
                ci_check_cooldown.cmd_record_verdict(verdict, sha)
                print(f"PIPELINE-SNAPSHOT-VERDICT-RECORDED sha={sha} verdict={verdict}", flush=True)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            state = "lookup_error"
            repair_guidance = []
            for missing in missing_proofs.values():
                missing["hosted_status"] = state
                missing["next_action"] = "resolve_observation_error"
            if not publish():
                return False
    print(f"PIPELINE-SNAPSHOT-WRITTEN path={args.snapshot_output} state={state} release_ready=false", flush=True)
    if terminal_observed and not errors and verdict is not None:
        print(
            f"PIPELINE-TERMINAL-OBSERVED repo={args.repo} branch={args.branch} "
            f"sha={sha} verdict={verdict} release_ready=false", flush=True,
        )
    return not errors


def _observe_ledger(args: argparse.Namespace) -> int:
    """Forward explicit identity to the public ledger CLI without sharing its queries."""
    argv = [
        "observe-sha", "--repo", args.repo, "--branch", args.branch,
        "--remote", args.remote, "--ledger", str(args.failure_ledger),
    ]
    if args.sha:
        argv.extend(["--sha", args.sha])
    if args.validate_only:
        argv.append("--validate-only")
    return ci_failure_ledger.main(argv)


def _command_exit_code(command: Callable[[], int]) -> int:
    """Normalize explicit exits and report unexpected command exceptions."""
    try:
        return command()
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code & 255
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1


def _cli_exit_code(command: Callable[[], int]) -> int:
    """Complete both stream flushes and retain CPython's cleanup failure code 120."""
    result = 1
    try:
        result = _command_exit_code(command)
    except Exception:
        result = 1
    finally:
        for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
            try:
                if stream is not None and not getattr(stream, "closed", False):
                    stream.flush()
            except Exception as exc:
                result = 120
                try:
                    traceback.print_exception(exc, file=sys.stderr)
                except Exception:
                    result = 120
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Print both observations while retaining the ledger's failure precedence."""
    args = _parser().parse_args(argv)
    args.ledger_observation_exit_code = args.ledger_observation_exit_code or 0
    if args.observe_ledger:
        args.ledger_observation_exit_code = _cli_exit_code(lambda: _observe_ledger(args))
    status_rc = _cli_exit_code(lambda: _status(args)) if args.observe_ledger else _status(args)
    if args.observe_ledger and args.ledger_observation_exit_code:
        ledger_rc = int(args.ledger_observation_exit_code)
        try:
            print(f"pipeline-status: failure-ledger observation failed rc={ledger_rc}")
        except Exception:
            try:
                traceback.print_exc()
            except Exception:
                return ledger_rc
        return ledger_rc
    return status_rc


def _status(args: argparse.Namespace) -> int:
    """Print local and complete remote state, returning a fail-closed verdict."""
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
    sha = None
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
        _publish_snapshot(args, None, sha=sha, error=str(exc))
        return 2
    print("\n".join(summary.lines()))
    if not _publish_snapshot(args, summary, sha=sha):
        return 2
    if args.record_verdict:
        try:
            verdict = _terminal_verdict(args, summary, sha)
            history = ci_check_cooldown.load_history()
            if verdict is None or (
                history.get("last_push_sha") and history["last_push_sha"] != sha
            ):
                ci_check_cooldown.cmd_record_verdict("pending")
                print(_compatibility_verdict_line(summary, 2))
                return 2
            if history.get("last_checked_sha") != sha or history.get("last_verdict") != verdict:
                ci_check_cooldown.cmd_record_verdict(verdict, sha)
        except (OSError, ValueError) as exc:
            print(f"CI VERDICT RECORD ERROR: {exc}")
            return 2
    print(_compatibility_verdict_line(summary, summary.exit_code))
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
