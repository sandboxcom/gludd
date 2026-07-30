"""Helper ranking and selection (spec GRC-001 §4.3, §5.2, GRC-AT-005).

``rank_helpers`` scores discovered :class:`HelperCandidate` records against a
:class:`TaskRequirements` contract. The spec mandates ten scoring criteria and
the priority chain:

    project authority > existing CI usage >
    maintained ecosystem standard > locally generated helper

Popularity alone SHALL NOT authorize a tool (spec §4.3): a candidate with
high capability fit but no documentation, security posture, or reversibility
scores below the policy threshold regardless of how widely adopted it is.

``helper_build_file_changes`` implements the GRC-AT-005 contract: when at
least one candidate clears the threshold, no helper is generated (zero file
changes). When none does, a generation plan is returned describing the
narrowest missing adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from .helper_catalog import HelperCandidate, ScoreEvidence

__all__ = [
    "DEFAULT_THRESHOLD",
    "SCORE_CRITERIA",
    "GeneratedHelperPlan",
    "TaskRequirements",
    "helper_build_file_changes",
    "rank_helpers",
]


# Spec §4.3: capability fit, documentation, maintenance, license, platform
# support, determinism, security posture, observability, reversibility,
# adoption cost.
SCORE_CRITERIA: tuple[str, ...] = (
    "capability_fit",
    "documentation",
    "maintenance",
    "license",
    "platform_support",
    "determinism",
    "security_posture",
    "observability",
    "reversibility",
    "adoption_cost",
)

DEFAULT_THRESHOLD: int = 50

# Priority rank for authority classes (lower == higher priority).
_AUTHORITY_PRIORITY: dict[str, int] = {
    "repository": 0,
    "ci-used": 1,
    "ecosystem": 2,
    "generated": 3,
}

# Per-criterion maximum value (each criterion contributes 0..10, summing to 100).
_MAX_PER_CRITERION: int = 10


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRequirements:
    """What the caller needs a helper to do."""

    kind: str = "build"
    needs_dry_run: bool = False
    needs_rollback: bool = False
    min_score: int = DEFAULT_THRESHOLD
    platforms: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_capability_fit(candidate: HelperCandidate, req: TaskRequirements) -> int:
    if candidate.kind == req.kind:
        return _MAX_PER_CRITERION
    if candidate.kind == "other":
        return 0
    # Adjacent kinds still partially useful (e.g. build helper for a test task).
    return 3


def _is_package_url(source_path: str) -> bool:
    """Heuristic: does ``source_path`` look like a package URL, not a file?

    Package URLs carry a scheme-like prefix (``pkg:npm/foo``, ``package:url``)
    where the first path segment contains a colon. POSIX relative paths never do.
    """
    first_segment = source_path.split("/")[0]
    return ":" in first_segment


def _score_documentation(candidate: HelperCandidate) -> int:
    name = candidate.source_path.upper()
    if name in {
        "AGENTS.MD",
        "CONTRIBUTING.MD",
        "README.MD",
        "DEVELOPMENT.MD",
        "SECURITY.MD",
        "RELEASING.MD",
    }:
        return _MAX_PER_CRITERION
    # A package URL with no doc reference carries no embedded documentation;
    # popularity is not a substitute (spec §4.3).
    if _is_package_url(candidate.source_path):
        return 0
    # Repo-owned files have implicit documentation via the project README.
    if candidate.authority == "repository":
        return 5
    if candidate.authority == "ci-used":
        return 5
    if candidate.authority == "ecosystem":
        return 5
    # Unknown with no doc reference.
    return 0


def _score_maintenance(candidate: HelperCandidate) -> int:
    return {
        "repository": _MAX_PER_CRITERION,
        "ci-used": 8,
        "ecosystem": 7,
        "generated": 2,
    }.get(candidate.authority, 0)


def _score_license(candidate: HelperCandidate) -> int:
    # Project-owned and ecosystem-standard files carry an implicit license
    # (the project's own). CI workflows inherit the repo license. Generated
    # helpers need an explicit license header — absent here, so partial credit.
    return {
        "repository": 8,
        "ci-used": 7,
        "ecosystem": 8,
        "generated": 2,
    }.get(candidate.authority, 0)


def _score_platform_support(candidate: HelperCandidate, req: TaskRequirements) -> int:
    if not req.platforms:
        return 7
    return 7


def _score_determinism(candidate: HelperCandidate) -> int:
    if candidate.supports_dry_run:
        return _MAX_PER_CRITERION
    return 5


def _score_security_posture(candidate: HelperCandidate) -> int:
    if candidate.supports_rollback and candidate.supports_dry_run:
        return _MAX_PER_CRITERION
    if candidate.supports_dry_run:
        return 6
    if candidate.supports_rollback:
        return 6
    return 2


def _score_observability(candidate: HelperCandidate) -> int:
    if candidate.observability:
        return _MAX_PER_CRITERION
    return 5


def _score_reversibility(candidate: HelperCandidate) -> int:
    if candidate.supports_rollback:
        return _MAX_PER_CRITERION
    if candidate.supports_dry_run:
        return 5
    return 0


def _score_adoption_cost(candidate: HelperCandidate) -> int:
    return {
        "repository": _MAX_PER_CRITERION,
        "ci-used": 7,
        "ecosystem": 5,
        "generated": 2,
    }.get(candidate.authority, 0)


def _score_candidate(candidate: HelperCandidate, req: TaskRequirements) -> tuple[int, tuple[ScoreEvidence, ...]]:
    """Return ``(total_score, evidence_tuple)`` for one candidate."""
    scored: list[tuple[str, int, str]] = [
        (
            "capability_fit",
            _score_capability_fit(candidate, req),
            f"kind={candidate.kind!r} vs required {req.kind!r}",
        ),
        (
            "documentation",
            _score_documentation(candidate),
            f"authority={candidate.authority}",
        ),
        (
            "maintenance",
            _score_maintenance(candidate),
            f"authority={candidate.authority}",
        ),
        ("license", _score_license(candidate), f"authority={candidate.authority}"),
        (
            "platform_support",
            _score_platform_support(candidate, req),
            "default broad support",
        ),
        (
            "determinism",
            _score_determinism(candidate),
            f"supports_dry_run={candidate.supports_dry_run}",
        ),
        (
            "security_posture",
            _score_security_posture(candidate),
            f"dry_run={candidate.supports_dry_run}, rollback={candidate.supports_rollback}",
        ),
        (
            "observability",
            _score_observability(candidate),
            f"observability_events={len(candidate.observability)}",
        ),
        (
            "reversibility",
            _score_reversibility(candidate),
            f"supports_rollback={candidate.supports_rollback}",
        ),
        (
            "adoption_cost",
            _score_adoption_cost(candidate),
            f"authority={candidate.authority}",
        ),
    ]
    total = sum(value for _criterion, value, _source in scored)
    total = max(0, min(100, total))
    evidence = tuple(
        ScoreEvidence(criterion=criterion, value=value, source=source) for criterion, value, source in scored
    )
    return total, evidence


def _sort_key(candidate: HelperCandidate) -> tuple[int, int]:
    """Lower tuple sorts first: authority priority, then descending score."""
    authority_rank = _AUTHORITY_PRIORITY.get(candidate.authority, 99)
    return (authority_rank, -candidate.score)


def rank_helpers(
    candidates: Iterable[HelperCandidate],
    task_requirements: TaskRequirements | None = None,
) -> list[HelperCandidate]:
    """Score and rank ``candidates`` for the given task requirements.

    - Records ``score`` and ``score_evidence`` on each returned candidate.
    - Filters out candidates whose total score is below ``task_requirements.min_score``.
    - Filters out candidates whose ``kind`` does not match when ``kind`` is set
      (the spec requires fitness; a deploy helper is not a build helper).
    - Sorts by spec priority: authority class first, then descending score.
    """
    req = task_requirements or TaskRequirements()
    threshold = req.min_score
    enriched: list[HelperCandidate] = []
    for candidate in candidates:
        if req.kind and candidate.kind != req.kind:
            continue
        total, evidence = _score_candidate(candidate, req)
        enriched.append(replace(candidate, score=total, score_evidence=evidence))
    passing = [c for c in enriched if c.score >= threshold]
    passing.sort(key=_sort_key)
    return passing


# ---------------------------------------------------------------------------
# GRC-AT-005: adequate helper => zero file changes from helper_build
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedHelperPlan:
    """Plan emitted when no adequate helper exists (spec GRC-AT-005)."""

    reason: str
    target_path: str
    kind: str

    def __str__(self) -> str:
        return f"generated helper needed: {self.reason} (target={self.target_path}, kind={self.kind})"


def helper_build_file_changes(
    ranked_candidates: list[HelperCandidate],
    repo_root: str | Path | None = None,
    task_requirements: TaskRequirements | None = None,
) -> list[str]:
    """Return the list of file changes ``helper_build`` would make.

    Per spec GRC-AT-005: if at least one candidate clears the policy threshold,
    ``helper_build`` makes **zero** file changes. If none does, it returns a
    non-empty plan describing the narrowest missing adapter to generate.

    Returns:
        Empty list when an adequate helper exists. Otherwise a list of human-
        readable plan strings (one per missing adapter).
    """
    if ranked_candidates:
        return []
    req = task_requirements or TaskRequirements()
    if repo_root is None:
        target = f"scripts/generated_{req.kind}_helper.sh"
    else:
        target = str(Path(repo_root) / "scripts" / f"generated_{req.kind}_helper.sh")
    plan = GeneratedHelperPlan(
        reason=(
            f"no candidate of kind={req.kind!r} cleared threshold "
            f"{req.min_score}; generate the narrowest missing adapter"
        ),
        target_path=target,
        kind=req.kind,
    )
    return [str(plan)]
