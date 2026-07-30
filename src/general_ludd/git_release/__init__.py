"""Git Release Captain Expert collection (spec GRC-001).

Evidence-driven planner and operator for repository assessment, history
investigation, branch planning, and release execution. Public surface is kept
narrow on purpose; downstream code consumes the :class:`RepoEvidence` shape
rather than raw subprocess output.
"""

from __future__ import annotations

from .contracts import (
    HelperAuthority,
    ReleasePlan,
    ReleaseVerdict,
    ReleaseVerdictState,
)
from .deployment import (
    AbortDecision,
    BlueGreenCutComplete,
    Decision,
    DeploymentConfig,
    DeploymentOrchestrator,
    DeploymentStrategy,
    HealthGate,
    HealthSample,
    HoldDecision,
    PromoteDecision,
    RollbackDecision,
    TrafficShift,
)
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
from .provenance import (
    Attestation,
    ProvenanceRecord,
    SignatureState,
    VerificationResult,
    build_provenance,
    verify_provenance,
)
from .release_state import (
    AdvanceResult,
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)
from .source_registry import (
    FreshnessFlag,
    SourceAuthority,
    SourceEntry,
    SourceRegistry,
    default_registry,
)
from .topology import assess_repo

__all__ = [
    "DEFAULT_THRESHOLD",
    "SCORE_CRITERIA",
    "AbortDecision",
    "AdvanceResult",
    "Attestation",
    "BlueGreenCutComplete",
    "Decision",
    "DeploymentConfig",
    "DeploymentOrchestrator",
    "DeploymentStrategy",
    "FreshnessFlag",
    "GeneratedHelperPlan",
    "HealthGate",
    "HealthSample",
    "HelperAuthority",
    "HelperCandidate",
    "HelperInput",
    "HelperOutput",
    "HoldDecision",
    "PromoteDecision",
    "ProvenanceRecord",
    "ReleasePlan",
    "ReleaseState",
    "ReleaseStateMachine",
    "ReleaseVerdict",
    "ReleaseVerdictState",
    "RepoEvidence",
    "RollbackDecision",
    "ScoreEvidence",
    "SignatureState",
    "SourceAuthority",
    "SourceEntry",
    "SourceRegistry",
    "TaskRequirements",
    "TrafficShift",
    "TransitionError",
    "VerificationResult",
    "assess_repo",
    "build_provenance",
    "collect_repo_evidence",
    "default_registry",
    "discover_helpers",
    "helper_build_file_changes",
    "rank_helpers",
    "verify_provenance",
]
__version__ = "1.0.0"
