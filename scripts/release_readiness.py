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

from general_ludd.quality.preflight import check_tasks_ticks
from general_ludd.review import release_forecast

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

RELEASE_STAGE_PLAN = (
    release_forecast.StagePlan("readiness_fix", "shared", 30.0),
    release_forecast.StagePlan("candidate_commit", "shared", 15.0, ("readiness_fix",)),
    release_forecast.StagePlan("local_dual_track", "local", 75.0, ("candidate_commit",)),
    release_forecast.StagePlan("hosted_ci", "gha", 35.0, ("candidate_commit",)),
    release_forecast.StagePlan(
        "full_gate",
        "shared",
        55.0,
        ("local_dual_track", "hosted_ci"),
    ),
    release_forecast.StagePlan("release_dry_run", "shared", 5.0, ("full_gate",)),
    release_forecast.StagePlan(
        "promotion_and_publish",
        "shared",
        20.0,
        ("release_dry_run",),
    ),
)
RELEASE_ARTIFACT_DEPENDENCIES = {
    "checksums": ("binaries", "packages", "wheel", "sdist", "collections"),
    "sbom": ("wheel", "sdist"),
    "smoke-attestations": ("binaries", "packages"),
    "release-manifest": ("checksums", "sbom", "smoke-attestations"),
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
    execution_critical_path: list[str] = field(default_factory=list)
    risk_priorities: list[release_forecast.Priority] = field(default_factory=list)
    hosted_canary: list[release_forecast.CanaryItem] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    replay_gaps: list[str] = field(default_factory=list)
    calibration_sample_count: int = 0
    method: str = "empirical-critical-path-v1"


@dataclass(frozen=True)
class RemediationStep:
    """One operator-owned action that never bypasses a readiness blocker."""

    code: str
    blockers: tuple[str, ...]
    validate_argv: tuple[str, ...]
    owner_release_argv: tuple[tuple[str, ...], ...]
    apply_argv: tuple[str, ...] | None
    requires_owner_confirmation: bool
    resolution: str
    safety: str


@dataclass(frozen=True)
class RemediationPlan:
    """Bounded, machine-readable workflow for remediable blocker classes."""

    schema_version: int
    steps: tuple[RemediationStep, ...]
    recheck_argv: tuple[str, ...]


def build_remediation_plan(
    result: Readiness,
    *,
    tag: str,
) -> RemediationPlan:
    """Return validate-first operator actions without changing release state."""
    steps: list[RemediationStep] = []
    prunable: list[dict[str, object]] = []
    for entry in result.unintegrated_worktrees:
        raw_reasons = entry.get("reasons")
        reasons = (
            {reason for reason in raw_reasons if isinstance(reason, str)}
            if isinstance(raw_reasons, list)
            else set()
        )
        if reasons == {"prunable_registration"}:
            prunable.append(entry)

    if prunable:
        blockers = tuple(
            sorted(
                f"{entry.get('branch', '<detached>')}@{entry.get('path', '<unknown>')}"
                for entry in prunable
            )
        )
        branches = sorted(
            {
                branch
                for entry in prunable
                if isinstance((branch := entry.get("branch")), str) and branch
            }
        )
        steps.append(
            RemediationStep(
                code="prunable_worktree_registration",
                blockers=blockers,
                validate_argv=(
                    "make",
                    "wt-prune-safe",
                    "ACTIVE_WORKSTREAM_REGISTRY=",
                    "WT_PRUNE_VALIDATE_ONLY=1",
                ),
                owner_release_argv=tuple(
                    ("make", "workstream-unregister", f"BRANCH={branch}")
                    for branch in branches
                ),
                apply_argv=(
                    "make",
                    "wt-prune-safe",
                    "ACTIVE_WORKSTREAM_REGISTRY=",
                    "WT_PRUNE_VALIDATE_ONLY=0",
                ),
                requires_owner_confirmation=True,
                resolution=(
                    "Run validate-only first. An owner may unregister only its exact "
                    "completed workstream; validate again and apply only when no "
                    "unrelated candidate would be removed."
                ),
                safety=(
                    "Dirty, locked, active, unmerged, or mixed-reason worktrees remain "
                    "blocking and receive no cleanup instruction."
                ),
            )
        )

    incomplete_tasks = tuple(sorted(set(result.incomplete_release_tasks)))
    if incomplete_tasks:
        steps.append(
            RemediationStep(
                code="incomplete_release_tasks",
                blockers=incomplete_tasks,
                validate_argv=("make", "validate-task-ledger"),
                owner_release_argv=(),
                apply_argv=None,
                requires_owner_confirmation=True,
                resolution=(
                    "Complete every task's declared implementation and evidence; each "
                    "task owner marks it complete only after its required checks. An "
                    "item must not be checked merely to clear readiness."
                ),
                safety=(
                    "The plan never edits TASKS.md or converts incomplete evidence into "
                    "a release authorization."
                ),
            )
        )

    return RemediationPlan(
        schema_version=1,
        steps=tuple(steps),
        recheck_argv=(
            "make",
            "release-readiness",
            f"TAG={tag}",
            "RELEASE_READINESS_VALIDATE_ONLY=0",
            "RELEASE_COMPLETED_STAGES=",
            "RELEASE_OBSERVATIONS=",
        ),
    )


def estimate_release_eta(
    *,
    completed_stages: set[str] | None = None,
    observations: dict[str, list[float]] | None = None,
    historical_observations: Sequence[release_forecast.RunObservation] = (),
    blockers: Sequence[release_forecast.Blocker] = (),
    coverage_gap_modules: Sequence[str] = (),
    canary_limit: int = 5,
) -> ReleaseEta:
    """Estimate release time from empirical history and the dependency graph.

    Local exact-SHA validation and hosted CI are modeled as parallel nodes.
    Historical failures also produce a bounded time-to-first-failure canary.
    """
    completed = completed_stages or set()
    observed = observations or {}
    unknown = (completed | set(observed)) - set(RELEASE_STAGE_BASELINES)
    if unknown:
        raise ValueError(f"unknown release stage(s): {', '.join(sorted(unknown))}")

    structured = list(historical_observations)
    lane_by_stage = {stage.name: stage.lane for stage in RELEASE_STAGE_PLAN}
    for name, samples in observed.items():
        for index, actual_minutes in enumerate(samples):
            if actual_minutes <= 0:
                raise ValueError(f"{name} observations must be positive")
            structured.append(
                release_forecast.RunObservation(
                    run_id=f"legacy:{name}:{index}",
                    phase=name,
                    lane=lane_by_stage[name],
                    duration_minutes=actual_minutes,
                    succeeded=True,
                )
            )

    forecast = release_forecast.build_forecast(
        stages=RELEASE_STAGE_PLAN,
        observations=structured,
        blockers=blockers,
        artifact_dependencies=RELEASE_ARTIFACT_DEPENDENCIES,
        completed_stages=completed,
        coverage_gap_modules=coverage_gap_modules,
        canary_limit=canary_limit,
    )
    phase_by_name = {phase.name: phase for phase in forecast.phases}
    stages = [
        ReleaseStageEstimate(
            name=name,
            baseline_minutes=baseline,
            calibrated_minutes=phase_by_name[name].p50_minutes,
            source=(
                "gludd-calibrated"
                if phase_by_name[name].sample_count
                else "baseline"
            ),
            completed=name in completed,
        )
        for name, baseline in RELEASE_STAGE_BASELINES.items()
    ]

    legacy_critical_path = [
        name
        for name in ("readiness_fix", "candidate_commit")
        if name not in completed
    ]
    if any(
        name not in completed for name in ("local_dual_track", "hosted_ci")
    ):
        legacy_critical_path.append("local_dual_track+hosted_ci")
    legacy_critical_path.extend(
        name
        for name in ("full_gate", "release_dry_run", "promotion_and_publish")
        if name not in completed
    )
    return ReleaseEta(
        p50_minutes=forecast.p50_minutes,
        p90_minutes=forecast.p90_minutes,
        critical_path=legacy_critical_path,
        stages=stages,
        execution_critical_path=list(forecast.critical_path),
        risk_priorities=list(forecast.priorities),
        hosted_canary=list(forecast.hosted_canary),
        coverage_gaps=list(forecast.coverage_gaps),
        replay_gaps=list(forecast.replay_gaps),
        calibration_sample_count=forecast.calibration_sample_count,
        method=forecast.method,
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


def _tasks_tick_check(root: Path) -> tuple[bool, str]:
    """Validate checked task evidence before collecting external release state."""
    tasks_path = root / "TASKS.md"
    if not tasks_path.is_file():
        return False, "TASKS.md is missing"
    try:
        lines = tasks_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return False, f"TASKS.md is unreadable: {exc}"
    outcome = check_tasks_ticks(lines)
    raw_violations = outcome.get("violations", [])
    violations = raw_violations if isinstance(raw_violations, list) else []
    if outcome.get("passed") is True:
        return True, "checked TASKS.md completion evidence is valid"
    detail = "; ".join(str(item) for item in violations)
    return False, detail or "checked TASKS.md completion evidence is invalid"


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
    tick_valid, tick_detail = _tasks_tick_check(root)
    result.ledger_valid = tick_valid
    result.ledger_detail = tick_detail
    if not tick_valid:
        result.errors.append(
            "checked TASKS.md completion evidence is invalid: " + tick_detail
        )
        return result
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


def _coverage_gap_modules(root: Path) -> tuple[str, ...]:
    """Return the tracked structural coverage backlog as forecast risk."""
    baseline = root / "config" / "coverage_gaps_baseline.json"
    if not baseline.is_file():
        return ()
    try:
        decoded = cast(object, json.loads(baseline.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(decoded, dict):
        return ()
    raw_gaps = decoded.get("allowed_gaps")
    if not isinstance(raw_gaps, list):
        return ()
    return tuple(sorted({item for item in raw_gaps if isinstance(item, str) and item}))


def _forecast_blockers(
    result: Readiness,
    *,
    coverage_gap_modules: Sequence[str] = (),
    replay_gaps: Sequence[str] = (),
) -> tuple[release_forecast.Blocker, ...]:
    """Translate current fail-closed readiness state into repair candidates."""
    blockers: list[release_forecast.Blocker] = []
    if not (result.ci_head_matches and result.ci_verdict == "GREEN"):
        blockers.append(
            release_forecast.Blocker(
                code="exact-sha-ci-evidence",
                phase="hosted_ci",
                repair_minutes=15.0,
                failure_class="ci-attestation",
                platform_gaps=tuple(sorted(set(replay_gaps))),
                artifacts=("smoke-attestations",),
            )
        )
    if result.dirty_count:
        blockers.append(
            release_forecast.Blocker(
                code="worktree-cleanliness",
                phase="readiness_fix",
                repair_minutes=5.0,
                failure_class="state-drift",
            )
        )
    if result.detached_worktrees or result.unintegrated_worktrees:
        blockers.append(
            release_forecast.Blocker(
                code="worktree-topology",
                phase="readiness_fix",
                repair_minutes=10.0,
                failure_class="state-drift",
            )
        )
    if not result.version_consistent:
        blockers.append(
            release_forecast.Blocker(
                code="version-consistency",
                phase="candidate_commit",
                repair_minutes=10.0,
                failure_class="version-drift",
                artifacts=("binaries", "packages", "wheel", "sdist"),
            )
        )
    if result.incomplete_release_tasks:
        blockers.append(
            release_forecast.Blocker(
                code="release-task-ledger",
                phase="readiness_fix",
                repair_minutes=max(15.0, len(result.incomplete_release_tasks) * 2.0),
                failure_class="incomplete-evidence",
                artifacts=("release-manifest",),
            )
        )
    if not result.ledger_valid:
        blockers.append(
            release_forecast.Blocker(
                code="ledger-validation",
                phase="readiness_fix",
                repair_minutes=15.0,
                failure_class="invalid-evidence",
                artifacts=("release-manifest",),
            )
        )
    if result.unmanaged_local_inference_processes:
        blockers.append(
            release_forecast.Blocker(
                code="local-inference-lifecycle",
                phase="local_dual_track",
                repair_minutes=15.0,
                failure_class="resource-lifecycle",
                artifacts=("smoke-attestations",),
            )
        )
    if coverage_gap_modules:
        blockers.append(
            release_forecast.Blocker(
                code="coverage-gaps",
                phase="local_dual_track",
                repair_minutes=max(10.0, len(coverage_gap_modules) * 5.0),
                failure_class="coverage",
                coverage_gap_files=len(coverage_gap_modules),
            )
        )
    return tuple(blockers)


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
    parser.add_argument(
        "--completed-stages",
        default="",
        help="comma-separated completed release stages",
    )
    parser.add_argument(
        "--observations",
        default="",
        help="comma-separated stage=minutes calibration samples",
    )
    parser.add_argument(
        "--history",
        default="",
        help=(
            "versioned JSON run history; defaults to "
            "<root>/.gludd/release_forecast_history.json when present"
        ),
    )
    parser.add_argument(
        "--canary-limit",
        default=5,
        type=int,
        help="maximum historically failing hosted nodes to front-load",
    )
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

    root = Path(cast(str, args.root)).absolute()
    raw_history = cast(str, args.history)
    history_path = (
        Path(raw_history).absolute()
        if raw_history
        else root / ".gludd" / "release_forecast_history.json"
    )
    try:
        history = release_forecast.load_observations(history_path) if history_path.is_file() else ()
        estimate = estimate_release_eta(
            completed_stages=completed,
            observations=observations,
            historical_observations=history,
            canary_limit=cast(int, args.canary_limit),
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(
            json.dumps(
                {
                    "estimate": asdict(estimate),
                    "tag": tag,
                    "validate_only": True,
                },
                sort_keys=True,
            )
        )
        return 0

    result = assess(root=root, gha_head_sha=args.gha_head_sha, tag=tag)
    coverage_gaps = _coverage_gap_modules(root)
    seed_estimate = estimate_release_eta(
        completed_stages=completed,
        observations=observations,
        historical_observations=history,
        coverage_gap_modules=coverage_gaps,
        canary_limit=cast(int, args.canary_limit),
    )
    blockers = _forecast_blockers(
        result,
        coverage_gap_modules=coverage_gaps,
        replay_gaps=seed_estimate.replay_gaps,
    )
    estimate = estimate_release_eta(
        completed_stages=completed,
        observations=observations,
        historical_observations=history,
        blockers=blockers,
        coverage_gap_modules=coverage_gaps,
        canary_limit=cast(int, args.canary_limit),
    )
    payload = asdict(result)
    payload["tag"] = tag
    payload["estimate"] = asdict(estimate)
    payload["remediation"] = asdict(build_remediation_plan(result, tag=tag))
    payload["ready"] = result.ready
    exit_code = _exit_code(result)
    payload["exit_code"] = exit_code
    print(json.dumps(payload, sort_keys=True))
    if args.human:
        print("RELEASE-READY" if result.ready else "RELEASE-BLOCKED")
    return exit_code




if __name__ == "__main__":
    raise SystemExit(main())
