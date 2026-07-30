"""AI/ML expert service package.

Implements capabilities from docs/specs/FEATURE_AI_ML_EXPERT.md:

  - AIML-001 Expert router  -> :class:`ExpertRouter`
  - AIML-002 Research discovery (evidence pipeline input shape, see schemas)
  - AIML-003 Evidence store  -> :class:`EvidenceStore`
  - AIML-007 Reasoning/answer -> :func:`answer_question`
  - AIML-018 Tool discovery   -> :func:`discover_tools`
  - Phase A registries        -> :class:`Registry`, :class:`RegistryRecord`,
    :class:`Source`, :class:`Dataset`, :class:`Model`, :class:`Adapter`,
    :class:`Simulator`, :class:`EvaluationSuite`, :class:`Deployment`

The ansible collection under
``collections/ansible_collections/general_ludd/ai_ml/`` wraps these typed
entry points; it never duplicates prompts or knowledge (spec §3.1).
"""

from __future__ import annotations

from general_ludd.ai_ml.evidence import (
    EVIDENCE_POLICY_RULESET_SHA256,
    EvidenceStore,
)
from general_ludd.ai_ml.registries import (
    Adapter,
    Dataset,
    Deployment,
    EvaluationSuite,
    Model,
    Registry,
    RegistryRecord,
    Simulator,
    Source,
    ValidationState,
)
from general_ludd.ai_ml.router import (
    ExpertRouter,
    answer_question,
    discover_tools,
)
from general_ludd.ai_ml.schemas import (
    ArtifactInput,
    ArtifactOutput,
    Citation,
    Constraints,
    CostRecord,
    DataClassification,
    ErrorRecord,
    EvidenceArtifact,
    ExpertRequest,
    ExpertResult,
    ExpertTask,
    PolicyDecision,
    ResultStatus,
    RouterDecision,
    ToolCandidate,
    ToolDecisionRecord,
    Uncertainty,
    Verification,
    VerificationStatus,
)

__all__ = [
    "EVIDENCE_POLICY_RULESET_SHA256",
    "Adapter",
    "ArtifactInput",
    "ArtifactOutput",
    "Citation",
    "Constraints",
    "CostRecord",
    "DataClassification",
    "Dataset",
    "Deployment",
    "ErrorRecord",
    "EvaluationSuite",
    "EvidenceArtifact",
    "EvidenceStore",
    "ExpertRequest",
    "ExpertResult",
    "ExpertRouter",
    "ExpertTask",
    "Model",
    "PolicyDecision",
    "Registry",
    "RegistryRecord",
    "ResultStatus",
    "RouterDecision",
    "Simulator",
    "Source",
    "ToolCandidate",
    "ToolDecisionRecord",
    "Uncertainty",
    "ValidationState",
    "Verification",
    "VerificationStatus",
    "answer_question",
    "discover_tools",
]
