"""Verify the 4 expert packages import cleanly and expose expected symbols."""

from __future__ import annotations

import sys

failures: list[str] = []


def _check(pkg: str, expected: list[str]) -> None:
    try:
        mod = __import__(pkg, fromlist=["__all__"])
    except Exception as exc:  # noqa: BLE001 - want all failures reported
        failures.append(f"{pkg}: IMPORT FAILED: {exc!r}")
        return
    missing = [name for name in expected if not hasattr(mod, name)]
    if missing:
        failures.append(f"{pkg}: missing symbols: {missing}")
    all_set = getattr(mod, "__all__", None)
    if all_set is None:
        failures.append(f"{pkg}: no __all__ defined")
    else:
        declared_missing = [n for n in (expected or []) if n not in all_set]
        if declared_missing:
            failures.append(f"{pkg}: symbols not in __all__: {declared_missing}")
    print(f"OK {pkg}: {len(getattr(mod, '__all__', []))} symbols exported")


_check(
    "general_ludd.materials",
    [
        "AdditiveManufacturingAdvisor",
        "JoiningAdvisor",
        "MachiningAdvisor",
        "MetalFormingAdvisor",
        "PolymerProcessAdvisor",
        "TextileAdvisor",
        "FailureAnalyzer",
        "RouteCard",
        "plan_manufacturing",
        "DesignRequirements",
        "ProcessPlan",
        "ToleranceChain",
        "PropertyStore",
        "SourceRegistry",
        "convert",
    ],
)
_check(
    "general_ludd.chemistry",
    [
        "CalibrationCurve",
        "validate_structure",
        "QuantumJob",
        "validate_quantum",
        "nernst_equation",
        "EntityRegistry",
        "InventoryRecord",
        "ProcessScaleUp",
        "PromotionPipeline",
        "lookup_property",
        "validate_protocol",
        "ProvenanceChain",
        "balance_reaction",
        "SafetyScreen",
        "ChemicalEntity",
        "SpectraAnalyzer",
        "calculate_yield",
        "equilibrium_constant",
        "validate_result",
    ],
)
_check(
    "general_ludd.ai_ml",
    [
        "AcceleratorPlanner",
        "AdapterManifest",
        "plan_adaptation",
        "DatasetManifest",
        "validate_dataset",
        "DistillationPlan",
        "EvaluationHarness",
        "EvidenceStore",
        "PolicyEngine",
        "PromotionGate",
        "ReasoningEngine",
        "Registry",
        "ResearchDiscovery",
        "RetrievalService",
        "ExpertRouter",
        "answer_question",
        "run_simulation",
        "ASRRequest",
        "VisionResult",
        "evaluate_rollout",
    ],
)
_check(
    "general_ludd.git_release",
    [
        "RepoEvidence",
        "collect_repo_evidence",
        "HelperCandidate",
        "discover_helpers",
        "rank_helpers",
        "ReleasePlan",
        "ReleaseVerdict",
        "ReleaseState",
        "ReleaseStateMachine",
        "DeploymentOrchestrator",
        "ProvenanceRecord",
        "build_provenance",
        "SourceRegistry",
        "assess_repo",
    ],
)

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nALL 4 PACKAGES OK")
