"""Git Release Captain Expert collection (spec GRC-001).

Evidence-driven planner and operator for repository assessment, history
investigation, branch planning, and release execution. Public surface is kept
narrow on purpose; downstream code consumes the :class:`RepoEvidence` shape
rather than raw subprocess output.
"""

from __future__ import annotations

from .evidence import RepoEvidence, collect_repo_evidence
from .helper_catalog import (
    HelperCandidate,
    HelperInput,
    HelperOutput,
    ScoreEvidence,
    discover_helpers,
)
from .helper_ranker import (
    DEFAULT_THRESHOLD,
    SCORE_CRITERIA,
    GeneratedHelperPlan,
    TaskRequirements,
    helper_build_file_changes,
    rank_helpers,
)

__all__ = [
    "DEFAULT_THRESHOLD",
    "SCORE_CRITERIA",
    "GeneratedHelperPlan",
    "HelperCandidate",
    "HelperInput",
    "HelperOutput",
    "RepoEvidence",
    "ScoreEvidence",
    "TaskRequirements",
    "collect_repo_evidence",
    "discover_helpers",
    "helper_build_file_changes",
    "rank_helpers",
]
__version__ = "1.0.0"
