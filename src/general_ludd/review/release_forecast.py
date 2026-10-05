"""Evidence-calibrated release forecasting and time-to-first-failure planning.

The model intentionally uses only structured evidence and Python's statistics
module. It does not scrape human-formatted logs, guess from wall-clock age, or
hide parallel work behind a serial sum.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_LANES = frozenset({"local", "gha", "shared"})
_METHOD = "empirical-critical-path-v1"
_MAX_HISTORY = 500


@dataclass(frozen=True)
class StagePlan:
    """One stage and the stages that must finish before it can start."""

    name: str
    lane: str
    baseline_minutes: float
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject invalid stage topology before forecasting."""
        if not self.name:
            raise ValueError("stage name is required")
        if self.lane not in _LANES:
            raise ValueError(f"unsupported stage lane: {self.lane}")
        if not math.isfinite(self.baseline_minutes) or self.baseline_minutes <= 0:
            raise ValueError("stage baseline_minutes must be positive and finite")
        if self.name in self.depends_on:
            raise ValueError("stage cannot depend on itself")


@dataclass(frozen=True)
class RunObservation:
    """Structured evidence from one completed local or GitHub Actions phase."""

    run_id: str
    phase: str
    lane: str
    duration_minutes: float
    succeeded: bool
    failure_class: str = ""
    failing_node: str = ""
    node_order: int = 0
    total_nodes: int = 0
    platform: str = ""
    python_version: str = ""

    def __post_init__(self) -> None:
        """Reject incomplete or internally inconsistent run evidence."""
        if not self.run_id or not self.phase:
            raise ValueError("run_id and phase are required")
        if self.lane not in _LANES:
            raise ValueError(f"unsupported observation lane: {self.lane}")
        if not isinstance(self.succeeded, bool):
            raise ValueError("succeeded must be a boolean")
        if not math.isfinite(self.duration_minutes) or self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive and finite")
        if self.node_order < 0 or self.total_nodes < 0:
            raise ValueError("node positions cannot be negative")
        if bool(self.node_order) != bool(self.total_nodes):
            raise ValueError("node_order and total_nodes must be provided together")
        if self.total_nodes and not 1 <= self.node_order <= self.total_nodes:
            raise ValueError("node_order must be within total_nodes")
        if self.succeeded and (self.failure_class or self.failing_node):
            raise ValueError("successful observations cannot contain failure evidence")


@dataclass(frozen=True)
class Blocker:
    """A current repair candidate whose release impact can be ranked."""

    code: str
    phase: str
    repair_minutes: float
    failure_class: str
    coverage_gap_files: int = 0
    platform_gaps: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject blocker signals that cannot be ranked safely."""
        if not self.code or not self.phase or not self.failure_class:
            raise ValueError("blocker code, phase, and failure_class are required")
        if not math.isfinite(self.repair_minutes) or self.repair_minutes <= 0:
            raise ValueError("repair_minutes must be positive and finite")
        if self.coverage_gap_files < 0:
            raise ValueError("coverage_gap_files cannot be negative")


@dataclass(frozen=True)
class PhaseForecast:
    """Calibrated duration and failure evidence for one release stage."""

    name: str
    lane: str
    p50_minutes: float
    p90_minutes: float
    sample_count: int
    failure_count: int
    failure_probability: float
    source: str
    completed: bool


@dataclass(frozen=True)
class Priority:
    """Expected risk removed per minute by repairing one current blocker."""

    rank: int
    code: str
    phase: str
    repair_minutes: float
    expected_risk_reduction_minutes: float
    risk_reduction_per_minute: float
    artifacts_unlocked: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CanaryItem:
    """One historically failing node moved into a bounded hosted canary."""

    canary_order: int
    node: str
    platform: str
    python_version: str
    failure_class: str
    historical_failures: int
    original_node_order: int
    estimated_canary_minutes: float
    expected_minutes_saved: float


@dataclass(frozen=True)
class ReleaseForecast:
    """Complete calibrated forecast and optimization plan."""

    p50_minutes: float
    p90_minutes: float
    critical_path: tuple[str, ...]
    phases: tuple[PhaseForecast, ...]
    priorities: tuple[Priority, ...]
    hosted_canary: tuple[CanaryItem, ...]
    coverage_gaps: tuple[str, ...]
    replay_gaps: tuple[str, ...]
    calibration_sample_count: int
    method: str = _METHOD


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot take a percentile of no values")
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _validate_graph(stages: Sequence[StagePlan]) -> dict[str, StagePlan]:
    by_name: dict[str, StagePlan] = {}
    for stage in stages:
        if stage.name in by_name:
            raise ValueError(f"duplicate stage: {stage.name}")
        by_name[stage.name] = stage
    if not by_name:
        raise ValueError("at least one stage is required")
    for stage in stages:
        missing = set(stage.depends_on) - set(by_name)
        if missing:
            raise ValueError(
                f"{stage.name} has unknown dependencies: {', '.join(sorted(missing))}"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError(f"stage dependency cycle includes {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name].depends_on:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        visit(name)
    return by_name


def _replay_gaps(observations: Sequence[RunObservation]) -> tuple[str, ...]:
    local_replays = {
        (item.platform, item.python_version)
        for item in observations
        if item.lane == "local" and item.platform and item.python_version
    }
    gaps = {
        f"{item.platform}/python-{item.python_version}"
        for item in observations
        if item.lane == "gha"
        and not item.succeeded
        and item.platform
        and item.python_version
        and (item.platform, item.python_version) not in local_replays
    }
    return tuple(sorted(gaps))


def _phase_forecasts(
    stages: Sequence[StagePlan],
    observations: Sequence[RunObservation],
    completed: set[str],
    coverage_gap_count: int,
    replay_gap_count: int,
    blockers: Sequence[Blocker],
) -> tuple[PhaseForecast, ...]:
    observations_by_phase: dict[str, list[RunObservation]] = defaultdict(list)
    for item in observations:
        observations_by_phase[item.phase].append(item)

    blocker_minutes: dict[str, float] = defaultdict(float)
    for blocker in blockers:
        blocker_minutes[blocker.phase] += blocker.repair_minutes

    forecasts: list[PhaseForecast] = []
    for stage in stages:
        samples = observations_by_phase.get(stage.name, [])
        successes = [item.duration_minutes for item in samples if item.succeeded]
        duration_evidence = successes or [item.duration_minutes for item in samples]
        if duration_evidence:
            p50 = statistics.median(duration_evidence)
            empirical_p90 = _nearest_rank(duration_evidence, 0.9)
            p90 = (
                empirical_p90
                if len(duration_evidence) >= 3
                else max(empirical_p90, p50 * 1.3)
            )
            source = "empirical"
        else:
            p50 = stage.baseline_minutes
            p90 = p50 * 1.3
            source = "baseline"

        failures = sum(not item.succeeded for item in samples)
        failure_probability = failures / len(samples) if samples else 0.0
        p90 *= 1.0 + 0.25 * failure_probability
        if stage.lane == "local":
            p90 *= 1.0 + min(0.5, coverage_gap_count * 0.02)
        if stage.lane == "gha":
            p90 *= 1.0 + min(0.5, replay_gap_count * 0.05)

        repairs = blocker_minutes.get(stage.name, 0.0)
        if repairs:
            p50 = max(p50, repairs)
            p90 = max(p90, repairs * 1.3)

        is_completed = stage.name in completed
        forecasts.append(
            PhaseForecast(
                name=stage.name,
                lane=stage.lane,
                p50_minutes=0.0 if is_completed else round(p50, 1),
                p90_minutes=0.0 if is_completed else round(p90, 1),
                sample_count=len(samples),
                failure_count=failures,
                failure_probability=round(failure_probability, 4),
                source=source,
                completed=is_completed,
            )
        )
    return tuple(forecasts)


def _critical_duration(
    stages: Sequence[StagePlan],
    durations: Mapping[str, float],
) -> tuple[float, tuple[str, ...]]:
    by_name = {stage.name: stage for stage in stages}
    cache: dict[str, tuple[float, tuple[str, ...]]] = {}

    def finish(name: str) -> tuple[float, tuple[str, ...]]:
        cached = cache.get(name)
        if cached is not None:
            return cached
        dependencies = by_name[name].depends_on
        if dependencies:
            dependency_minutes, dependency_path = max(
                (finish(dependency) for dependency in dependencies),
                key=lambda item: (item[0], item[1]),
            )
        else:
            dependency_minutes, dependency_path = 0.0, ()
        own_minutes = durations[name]
        path = dependency_path + ((name,) if own_minutes else ())
        value = dependency_minutes + own_minutes, path
        cache[name] = value
        return value

    return max((finish(stage.name) for stage in stages), key=lambda item: (item[0], item[1]))


def _artifact_unlocks(
    artifacts: Sequence[str],
    dependencies: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    unlocked = set(artifacts)
    changed = True
    while changed:
        changed = False
        for artifact, requirements in dependencies.items():
            if artifact in unlocked or not requirements:
                continue
            if all(requirement in unlocked for requirement in requirements):
                unlocked.add(artifact)
                changed = True
    return tuple(sorted(unlocked))


def _descendant_tail(
    stage_name: str,
    stages: Sequence[StagePlan],
    durations: Mapping[str, float],
) -> float:
    children: dict[str, list[str]] = defaultdict(list)
    for stage in stages:
        for dependency in stage.depends_on:
            children[dependency].append(stage.name)
    cache: dict[str, float] = {}

    def tail(name: str) -> float:
        if name not in cache:
            cache[name] = durations[name] + max(
                (tail(child) for child in children.get(name, [])),
                default=0.0,
            )
        return cache[name]

    return tail(stage_name)


def _priorities(
    blockers: Sequence[Blocker],
    observations: Sequence[RunObservation],
    stages: Sequence[StagePlan],
    phases: Sequence[PhaseForecast],
    artifact_dependencies: Mapping[str, Sequence[str]],
) -> tuple[Priority, ...]:
    stage_names = {stage.name for stage in stages}
    phase_p90 = {phase.name: phase.p90_minutes for phase in phases}
    phase_observations: dict[str, list[RunObservation]] = defaultdict(list)
    for item in observations:
        phase_observations[item.phase].append(item)

    unordered: list[Priority] = []
    for blocker in blockers:
        if blocker.phase not in stage_names:
            raise ValueError(f"blocker {blocker.code} references unknown phase {blocker.phase}")
        phase_history = phase_observations.get(blocker.phase, [])
        class_failures = sum(
            not item.succeeded and item.failure_class == blocker.failure_class
            for item in phase_history
        )
        historical_probability = (
            class_failures / len(phase_history) if phase_history else 0.0
        )
        probability = min(
            1.0,
            0.5
            + historical_probability * 0.5
            + blocker.coverage_gap_files * 0.08
            + len(blocker.platform_gaps) * 0.1,
        )
        unlocked = _artifact_unlocks(blocker.artifacts, artifact_dependencies)
        tail = _descendant_tail(blocker.phase, stages, phase_p90)
        expected_reduction = tail * probability + len(unlocked) * 5.0
        reasons = ["current release blocker"]
        if class_failures:
            reasons.append(
                f"{class_failures} historical {blocker.failure_class} failure(s)"
            )
        if blocker.coverage_gap_files:
            reasons.append(f"{blocker.coverage_gap_files} coverage gap file(s)")
        if blocker.platform_gaps:
            reasons.append(
                "missing local replay: " + ", ".join(sorted(blocker.platform_gaps))
            )
        if unlocked:
            reasons.append(f"unlocks {len(unlocked)} artifact node(s)")
        unordered.append(
            Priority(
                rank=0,
                code=blocker.code,
                phase=blocker.phase,
                repair_minutes=round(blocker.repair_minutes, 1),
                expected_risk_reduction_minutes=round(expected_reduction, 1),
                risk_reduction_per_minute=round(
                    expected_reduction / blocker.repair_minutes, 3
                ),
                artifacts_unlocked=unlocked,
                reasons=tuple(reasons),
            )
        )

    ordered = sorted(
        unordered,
        key=lambda item: (
            -item.risk_reduction_per_minute,
            -item.expected_risk_reduction_minutes,
            item.code,
        ),
    )
    return tuple(
        Priority(
            rank=index,
            code=item.code,
            phase=item.phase,
            repair_minutes=item.repair_minutes,
            expected_risk_reduction_minutes=item.expected_risk_reduction_minutes,
            risk_reduction_per_minute=item.risk_reduction_per_minute,
            artifacts_unlocked=item.artifacts_unlocked,
            reasons=item.reasons,
        )
        for index, item in enumerate(ordered, 1)
    )


def _hosted_canary(
    observations: Sequence[RunObservation],
    limit: int,
) -> tuple[CanaryItem, ...]:
    if not 0 <= limit <= 20:
        raise ValueError("canary_limit must be between 0 and 20")
    failures: dict[tuple[str, str, str, str], list[RunObservation]] = defaultdict(list)
    for item in observations:
        if (
            item.lane == "gha"
            and not item.succeeded
            and item.failing_node
            and item.node_order
            and item.total_nodes
        ):
            key = (
                item.failing_node,
                item.platform,
                item.python_version,
                item.failure_class or "unclassified",
            )
            failures[key].append(item)

    candidates: list[CanaryItem] = []
    for (node, platform, python_version, failure_class), items in failures.items():
        first_failure = statistics.median(item.duration_minutes for item in items)
        original_order = round(statistics.median(item.node_order for item in items))
        per_node = statistics.median(
            item.duration_minutes / item.node_order for item in items
        )
        canary_minutes = max(0.5, min(5.0, per_node))
        matching_runs = {
            item.run_id
            for item in observations
            if item.lane == "gha"
            and item.phase == items[0].phase
            and item.platform == platform
            and item.python_version == python_version
        }
        recurrence = min(1.0, len(items) / max(1, len(matching_runs)))
        expected_saved = max(0.0, first_failure - canary_minutes) * recurrence
        candidates.append(
            CanaryItem(
                canary_order=0,
                node=node,
                platform=platform,
                python_version=python_version,
                failure_class=failure_class,
                historical_failures=len(items),
                original_node_order=original_order,
                estimated_canary_minutes=round(canary_minutes, 1),
                expected_minutes_saved=round(expected_saved, 1),
            )
        )

    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.expected_minutes_saved,
            -item.original_node_order,
            item.node,
            item.platform,
            item.python_version,
        ),
    )[:limit]
    return tuple(
        CanaryItem(
            canary_order=index,
            node=item.node,
            platform=item.platform,
            python_version=item.python_version,
            failure_class=item.failure_class,
            historical_failures=item.historical_failures,
            original_node_order=item.original_node_order,
            estimated_canary_minutes=item.estimated_canary_minutes,
            expected_minutes_saved=item.expected_minutes_saved,
        )
        for index, item in enumerate(ordered, 1)
    )


def build_forecast(
    *,
    stages: Sequence[StagePlan],
    observations: Sequence[RunObservation] = (),
    blockers: Sequence[Blocker] = (),
    artifact_dependencies: Mapping[str, Sequence[str]] | None = None,
    completed_stages: set[str] | None = None,
    coverage_gap_modules: Sequence[str] = (),
    canary_limit: int = 5,
) -> ReleaseForecast:
    """Build an empirical critical-path forecast and repair/canary priorities."""
    by_name = _validate_graph(stages)
    completed = completed_stages or set()
    unknown_completed = completed - set(by_name)
    if unknown_completed:
        raise ValueError(
            f"unknown completed stage(s): {', '.join(sorted(unknown_completed))}"
        )
    for item in observations:
        if item.phase not in by_name:
            raise ValueError(f"observation references unknown phase: {item.phase}")
        if item.lane != by_name[item.phase].lane and by_name[item.phase].lane != "shared":
            raise ValueError(
                f"observation lane {item.lane} does not match {item.phase} lane "
                f"{by_name[item.phase].lane}"
            )

    coverage_gaps = tuple(sorted(set(coverage_gap_modules)))
    replay_gaps = _replay_gaps(observations)
    phases = _phase_forecasts(
        stages,
        observations,
        completed,
        len(coverage_gaps),
        len(replay_gaps),
        blockers,
    )
    p50_durations = {phase.name: phase.p50_minutes for phase in phases}
    p90_durations = {phase.name: phase.p90_minutes for phase in phases}
    p50, critical_path = _critical_duration(stages, p50_durations)
    p90, _ = _critical_duration(stages, p90_durations)

    return ReleaseForecast(
        p50_minutes=round(p50, 1),
        p90_minutes=round(max(p50, p90), 1),
        critical_path=critical_path,
        phases=phases,
        priorities=_priorities(
            blockers,
            observations,
            stages,
            phases,
            artifact_dependencies or {},
        ),
        hosted_canary=_hosted_canary(observations, canary_limit),
        coverage_gaps=coverage_gaps,
        replay_gaps=replay_gaps,
        calibration_sample_count=len(observations),
    )


def load_observations(path: Path, *, limit: int = _MAX_HISTORY) -> tuple[RunObservation, ...]:
    """Load a bounded, versioned JSON history without accepting ambiguous data."""
    if not 1 <= limit <= _MAX_HISTORY:
        raise ValueError(f"limit must be between 1 and {_MAX_HISTORY}")
    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load release forecast history: {exc}") from exc
    if not isinstance(decoded, dict) or decoded.get("schema_version") != 1:
        raise ValueError("release forecast history schema_version must be 1")
    raw_observations = decoded.get("observations")
    if not isinstance(raw_observations, list):
        raise ValueError("release forecast history observations must be a list")
    loaded: list[RunObservation] = []
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, dict):
            raise ValueError(f"observation {index} must be an object")
        try:
            loaded.append(RunObservation(**raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid observation {index}: {exc}") from exc
    return tuple(loaded[-limit:])
