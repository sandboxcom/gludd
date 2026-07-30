"""AI/ML expert service package.

Implements the top-five capabilities from docs/specs/FEATURE_AI_ML_EXPERT.md:

  - AIML-001 Expert router  -> :class:`ExpertRouter`
  - AIML-002 Research discovery (evidence pipeline input shape, see schemas)
  - AIML-003 Evidence store  -> :class:`EvidenceStore`
  - AIML-007 Reasoning/answer -> :func:`answer_question`
  - AIML-018 Tool discovery   -> :func:`discover_tools`

The ansible collection under
``collections/ansible_collections/general_ludd/ai_ml/`` wraps these typed
entry points; it never duplicates prompts or knowledge (spec §3.1).
"""

from __future__ import annotations

from general_ludd.ai_ml.router import (
    EvidenceStore,
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
    "ArtifactInput",
    "ArtifactOutput",
    "Citation",
    "Constraints",
    "CostRecord",
    "DataClassification",
    "ErrorRecord",
    "EvidenceArtifact",
    "EvidenceStore",
    "ExpertRequest",
    "ExpertResult",
    "ExpertRouter",
    "ExpertTask",
    "PolicyDecision",
    "ResultStatus",
    "RouterDecision",
    "ToolCandidate",
    "ToolDecisionRecord",
    "Uncertainty",
    "Verification",
    "VerificationStatus",
    "answer_question",
    "discover_tools",
]
