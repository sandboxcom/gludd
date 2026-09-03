"""Self-improvement harness for gap analysis and automated fixes."""

__all__ = (
    "ApprovedSelfImprovePlan",
    "CodeTaskShape",
    "ExternalApply",
    "GateDecision",
    "ManagedRunResult",
    "ManagedSelfImproveResultArtifact",
    "ManagedSelfImproveRunner",
    "SelfApply",
    "SelfImproveGate",
    "SelfImprovementHarness",
    "build_managed_self_improve_runner",
    "prepare_managed_self_improve_plan",
)

from general_ludd.self_improve.apply import ExternalApply, SelfApply
from general_ludd.self_improve.gate import GateDecision, SelfImproveGate
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    ManagedRunResult,
    ManagedSelfImproveRunner,
)
from general_ludd.self_improve.model_candidate_planner import CodeTaskShape
from general_ludd.self_improve.result_artifact import ManagedSelfImproveResultArtifact
from general_ludd.self_improve.runtime import (
    build_managed_self_improve_runner,
    prepare_managed_self_improve_plan,
)
