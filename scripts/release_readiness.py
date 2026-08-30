#!/usr/bin/env python3
"""Fail-closed beta release readiness preflight.

The preflight intentionally composes the repository's existing guards instead
of maintaining a second git/CI/task parser.  It emits one stable JSON object
on every path so CI and release tooling can consume the same diagnostics.

Exit codes are part of the interface:

* 0: ready
* 2: current HEAD has no matching successful CI evidence
* 3: worktree is dirty
* 4: detached or unintegrated sibling worktree exists
* 5: project versions are inconsistent
* 6: release-critical TASKS/ledger work is incomplete or invalid
* 7: preflight could not collect required evidence
* 8: a project-owned local-inference server has no Gludd daemon owner
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from general_ludd.review.estimation_tracker import (
    EstimationTracker,
    TaskActual,
    TaskEstimate,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE_TAG = "v0.1.0-beta.4"
RELEASE_TASK_PREFIXES = {DEFAULT_RELEASE_TAG: ("S86.",)}
RELEASE_ACTION_TASKS = {DEFAULT_RELEASE_TAG: frozenset({"S86.10"})}
_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+\Z")

RELEASE_STAGE_BASELINES = {
    "readiness_fix": 30.0,
    "candidate_commit": 15.0,
    "local_dual_track": 75.0,
    "hosted_ci": 35.0,
    "full_gate": 55.0,
    "release_dry_run": 5.0,
    "promotion_and_publish": 20.0,
}

EXIT_OK = 0
EXIT_CI = 2
EXIT_DIRTY = 3
EXIT_WORKTREE = 4
EXIT_VERSION = 5
EXIT_TASKS = 6
EXIT_ERROR = 7
EXIT_RESOURCE = 8

RunFn = Callable[[Sequence[str], str | None], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=cwd, capture_output=True, text=True, check=False, timeout=30
    )


@dataclass
class Readiness:
    """Fail-closed evidence collected for one immutable release candidate."""
    branch: str = ""
    head: str = ""
    ci_head_sha: str = ""
    ci_verdict: str = "UNKNOWN"
    ci_head_matches: bool = False
    dirty_count: int = 0
    detached_worktrees: list[dict[str, str]] = field(default_factory=list)
    unintegrated_worktrees: list[dict[str, object]] = field(default_factory=list)
    version_consistent: bool = False
    version_detail: str = ""
    incomplete_release_tasks: list[str] = field(default_factory=list)
    ledger_valid: bool = False
    ledger_detail: str = ""
    unmanaged_local_inference_processes: list[dict[str, object]] = field(
        default_factory=list
    )
    errors: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Return whether every required release condition is satisfied."""
        return (
            not self.errors
            and bool(self.branch and self.head)
            and self.ci_head_matches
            and self.ci_verdict == "GREEN"
            and self.dirty_count == 0
            and not self.detached_worktrees
            and not self.unintegrated_worktrees
            and self.version_consistent
            and self.ledger_valid
            and not self.incomplete_release_tasks
            and not self.unmanaged_local_inference_processes
        )


@dataclass(frozen=True)
class ReleaseStageEstimate:
    """One calibrated stage in the beta release critical path."""

    name: str
    baseline_minutes: float
    calibrated_minutes: float
    source: str
    completed: bool


@dataclass(frozen=True)
class ReleaseEta:
    """P50/P90 release duration plus its explicit critical path."""

    p50_minutes: float
    p90_minutes: float
    critical_path: list[str]
    stages: list[ReleaseStageEstimate]


def estimate_release_eta(
    *,
    completed_stages: set[str] | None = None,
    observations: dict[str, list[float]] | None = None,
) -> ReleaseEta:
    """Estimate beta release time using Gludd's self-correcting tracker.

    Local exact-SHA validation and hosted CI are a parallel pair, so the
    critical path charges only the slower unfinished lane.
    """
    completed = completed_stages or set()
    observed = observations or {}
    unknown = (completed | set(observed)) - set(RELEASE_STAGE_BASELINES)
    if unknown:
        raise ValueError(f"unknown release stage(s): {', '.join(sorted(unknown))}")

    tracker = EstimationTracker(min_samples=1)
    stages: list[ReleaseStageEstimate] = []
    for name, baseline in RELEASE_STAGE_BASELINES.items():
        samples = observed.get(name, [])
        for index, actual_minutes in enumerate(samples):
            if actual_minutes <= 0:
                raise ValueError(f"{name} observations must be positive")
            todo_id = f"release:{name}:{index}"
            tracker.record_estimate(
                TaskEstimate(todo_id, f"release:{name}", 1.0, baseline, 1)
            )
            tracker.record_completion(
                TaskActual(todo_id, 1.0, actual_minutes, 1, 0)
            )
        calibrated = tracker.get_corrected_estimate(
            f"release:{name}", 1.0, baseline, 1
        )[1]
        stages.append(
            ReleaseStageEstimate(
                name=name,
                baseline_minutes=baseline,
                calibrated_minutes=round(calibrated, 1),
                source="gludd-calibrated" if samples else "baseline",
                completed=name in completed,
            )
        )

    by_name = {stage.name: stage for stage in stages}
    serial_names = [
        "readiness_fix",
        "candidate_commit",
        "full_gate",
        "release_dry_run",
        "promotion_and_publish",
    ]
    p50 = sum(
        by_name[name].calibrated_minutes
        for name in serial_names
        if not by_name[name].completed
    )
    parallel = [
        by_name[name]
        for name in ("local_dual_track", "hosted_ci")
        if not by_name[name].completed
    ]
    if parallel:
        p50 += max(stage.calibrated_minutes for stage in parallel)

    critical_path = [
        name for name in ("readiness_fix", "candidate_commit")
        if not by_name[name].completed
    ]
    if parallel:
        critical_path.append("local_dual_track+hosted_ci")
    critical_path.extend(
        name
        for name in ("full_gate", "release_dry_run", "promotion_and_publish")
        if not by_name[name].completed
    )
    return ReleaseEta(
        p50_minutes=round(p50, 1),
        p90_minutes=round(p50 * 1.3, 1),
        critical_path=critical_path,
        stages=stages,
    )


def _ci_verdict(head: str, branch: str, run: RunFn) -> tuple[str, str]:
    """Use require_ci_green.verdict_for for exact-SHA CI evidence."""
    try:
        from require_ci_green import verdict_for

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = verdict_for(head, branch)
        verdict = {0: "GREEN", 1: "RED", 2: "PENDING"}.get(code, "UNKNOWN")
        return verdict, captured.getvalue().strip()
    except Exception as exc:  # pragma: no cover - defensive import fallback
        helper = ROOT / "scripts" / "require_ci_green.py"
        result = run([sys.executable, str(helper), head, branch], str(ROOT))
        detail = (result.stdout or result.stderr or str(exc)).strip()
        verdict = {0: "GREEN", 1: "RED", 2: "PENDING"}.get(result.returncode, "UNKNOWN")
        return verdict, detail


def _version_check(run: RunFn, root: Path) -> tuple[bool, str]:
    """Invoke the canonical version-consistency helper."""
    helper = root / "scripts" / "check_version_consistency.py"
    result = run([sys.executable, str(helper)], str(root))
    detail = (result.stdout or result.stderr or "version helper returned no output").strip()
    return result.returncode == 0, detail


def _ledger_check(run: RunFn, root: Path) -> tuple[bool, str]:
    """Invoke the canonical task-ledger validator."""
    helper = root / "scripts" / "validate_task_ledger.py"
    result = run([sys.executable, str(helper)], str(root))
    detail = (result.stdout or result.stderr or "ledger helper returned no output").strip()
    return result.returncode == 0, detail


def _detached_worktrees(run: RunFn, root: Path) -> list[dict[str, str]]:
    """Reuse workflow_state_guard's porcelain parser for detached siblings."""
    from workflow_state_guard import _worktree_entries

    result = run(["git", "worktree", "list", "--porcelain"], str(root))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git worktree failed").strip())
    current_result = run(["git", "rev-parse", "--show-toplevel"], str(root))
    current = current_result.stdout.strip() if current_result.returncode == 0 else str(root)
    return [
        {key: str(value) for key, value in entry.items()}
        for entry in _worktree_entries(result.stdout)
        if entry.get("path") != current and not entry.get("branch")
    ]


def _unmanaged_local_inference_processes(
    run: RunFn,
) -> list[dict[str, object]]:
    """Return llama.cpp servers that have no Gludd daemon ancestor."""
    result = run(["ps", "-ax", "-o", "pid=,ppid=,command="], None)
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "process inventory failed").strip()
        )

    processes: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.+)", line)
        if match is None:
            continue
        pid, ppid, command = match.groups()
        processes[int(pid)] = (int(ppid), command)

    unmanaged: list[dict[str, object]] = []
    for pid, (ppid, command) in processes.items():
        is_llama_server = "-m llama_cpp.server" in command or re.search(
            r"(?:^|/)llama-server(?:\s|$)", command
        )
        if not is_llama_server:
            continue
        ancestor_pid = ppid
        seen: set[int] = set()
        daemon_owned = False
        while ancestor_pid > 1 and ancestor_pid not in seen:
            seen.add(ancestor_pid)
            ancestor = processes.get(ancestor_pid)
            if ancestor is None:
                break
            ancestor_pid, ancestor_command = ancestor
            if "general_ludd" in ancestor_command and "daemon" in ancestor_command:
                daemon_owned = True
                break
        if not daemon_owned:
            unmanaged.append({"pid": pid, "ppid": ppid, "command": command})
    return unmanaged


def _incomplete_tasks(root: Path, tag: str = DEFAULT_RELEASE_TAG) -> list[str]:
    extract_tasks = cast(
        "Callable[[Path], tuple[list[dict[str, object]], list[dict[str, object]]]]",
        importlib.import_module("validate_task_ledger").extract_tasks,
    )

    tasks_path = root / "TASKS.md"
    if not tasks_path.exists():
        raise RuntimeError("TASKS.md is missing")
    _, unchecked = extract_tasks(tasks_path)
    ids: list[str] = []
    prefixes = RELEASE_TASK_PREFIXES.get(tag)
    if prefixes is None:
        raise RuntimeError(f"unsupported release task mapping for {tag}")
    release_actions = RELEASE_ACTION_TASKS.get(tag, frozenset())
    for task in unchecked:
        raw_ids = task.get("ids", [])
        if not isinstance(raw_ids, list):
            continue
        for task_id in raw_ids:
            if not isinstance(task_id, str):
                continue
            if task_id not in release_actions and any(
                task_id.startswith(prefix) for prefix in prefixes
            ):
                ids.append(task_id)
    return sorted(set(ids))


def assess(
    *,
    root: Path = ROOT,
    run: RunFn = _run,
    gha_head_sha: str = "",
    tag: str = DEFAULT_RELEASE_TAG,
) -> Readiness:
    """Collect all release evidence without mutating the repository."""
    result = Readiness()
    try:
        from workflow_state_guard import collect_state

        state = collect_state(
            remote="sandboxcom",
            gha_head_sha=gha_head_sha,
            collect_unintegrated_worktrees=True,
            collect_unintegrated_branches=True,
            run=run,
            cwd=str(root),
        )
        result.branch = state.branch
        result.head = state.head
        result.ci_head_sha = gha_head_sha or state.head
        result.ci_head_matches = not gha_head_sha or gha_head_sha == state.head
        result.dirty_count = state.dirty_count
        result.unintegrated_worktrees = state.unintegrated_worktrees

        detached = _detached_worktrees(run, root)
        result.detached_worktrees = detached

        verdict, _ci_detail = _ci_verdict(state.head, state.branch, run)
        result.ci_verdict = verdict
        if not result.ci_head_matches or verdict != "GREEN":
            result.errors.append(
                f"CI evidence is not a successful run for HEAD {state.head} "
                f"(head_match={result.ci_head_matches}, verdict={verdict})"
            )
        if state.dirty_count:
            result.errors.append(f"worktree has {state.dirty_count} dirty path(s)")
        if detached or state.unintegrated_worktrees or state.unintegrated_branches:
            result.errors.append(
                "detached or unintegrated sibling worktree/branch exists"
            )

        result.unmanaged_local_inference_processes = (
            _unmanaged_local_inference_processes(run)
        )
        if result.unmanaged_local_inference_processes:
            result.errors.append(
                "unmanaged local inference process is running outside a Gludd "
                "daemon lifecycle"
            )

        result.version_consistent, result.version_detail = _version_check(run, root)
        if not result.version_consistent:
            result.errors.append("project version files are inconsistent")

        result.incomplete_release_tasks = _incomplete_tasks(root, tag)
        result.ledger_valid, result.ledger_detail = _ledger_check(run, root)
        if result.incomplete_release_tasks:
            result.errors.append(
                "release-critical TASKS.md items are incomplete: "
                + ", ".join(result.incomplete_release_tasks)
            )
        if not result.ledger_valid:
            result.errors.append("TASKS.md ledger validation failed")
    except Exception as exc:
        result.errors.append(
            f"preflight evidence collection failed for root {root}: {exc}"
        )

    return result


def _exit_code(result: Readiness) -> int:
    if result.ready:
        return EXIT_OK
    if any(error.startswith("unmanaged local inference") for error in result.errors):
        return EXIT_RESOURCE
    if any(error.startswith("CI evidence") for error in result.errors) or (
        result.head and not (result.ci_head_matches and result.ci_verdict == "GREEN")
    ):
        return EXIT_CI
    if any(error.startswith("worktree has") for error in result.errors):
        return EXIT_DIRTY
    if any("detached or unintegrated" in error for error in result.errors):
        return EXIT_WORKTREE
    if any("version" in error for error in result.errors):
        return EXIT_VERSION
    if any("TASKS" in error or "ledger" in error for error in result.errors):
        return EXIT_TASKS
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    """Run the beta release preflight and emit one machine-readable result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gha-head-sha", default="", help="expected CI HEAD SHA")
    parser.add_argument("--root", default=str(ROOT), help="explicit repository worktree root")
    parser.add_argument("--tag", default=DEFAULT_RELEASE_TAG, help="target prerelease tag")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the plan without Git, CI, or filesystem evidence",
    )
    parser.add_argument("--completed-stages", default="", help="comma-separated completed release stages")
    parser.add_argument("--observations", default="", help="comma-separated stage=minutes calibration samples")
    parser.add_argument("--human", action="store_true", help="also print a short human summary")
    args = parser.parse_args(list(argv) if argv is not None else None)
    tag = cast(str, args.tag)
    if _TAG.fullmatch(tag) is None or tag not in RELEASE_TASK_PREFIXES:
        parser.error("--tag must be a supported beta release tag")
    completed = {value for value in cast(str, args.completed_stages).split(",") if value}
    observations: dict[str, list[float]] = {}
    for entry in filter(None, cast(str, args.observations).split(",")):
        name, separator, raw_minutes = entry.partition("=")
        if not separator:
            parser.error("--observations entries must be stage=minutes")
        try:
            observations.setdefault(name, []).append(float(raw_minutes))
        except ValueError:
            parser.error("--observations minutes must be numeric")
    try:
        estimate = estimate_release_eta(
            completed_stages=completed,
            observations=observations,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(json.dumps({"estimate": asdict(estimate), "tag": tag, "validate_only": True}, sort_keys=True))
        return 0

    root = Path(cast(str, args.root)).absolute()
    result = assess(root=root, gha_head_sha=args.gha_head_sha, tag=tag)
    payload = asdict(result)
    payload["tag"] = tag
    payload["estimate"] = asdict(estimate)
    payload["ready"] = result.ready
    exit_code = _exit_code(result)
    payload["exit_code"] = exit_code
    print(json.dumps(payload, sort_keys=True))
    if args.human:
        print("RELEASE-READY" if result.ready else "RELEASE-BLOCKED")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
