"""Stable compatibility surface for Codex proposal comparison components.

Protocol contracts remain import-compatible here while evaluation and local-model
execution live in cohesive implementation modules. Attribute updates are forwarded
to those owners so established monkeypatch seams retain their historical behavior.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Final

from general_ludd.self_improve import codex_evaluation as _evaluation
from general_ludd.self_improve import codex_gateway as _gateway
from general_ludd.self_improve import codex_protocol as _protocol
from general_ludd.self_improve.codex_evaluation import (
    CandidateEvidence as CandidateEvidence,
)
from general_ludd.self_improve.codex_evaluation import (
    CodexReference as CodexReference,
)
from general_ludd.self_improve.codex_evaluation import (
    ComparisonResult as ComparisonResult,
)
from general_ludd.self_improve.codex_evaluation import (
    PlannerFeedbackExchange,
    build_retry_prompt,
    compare_with_codex,
    merge_proposal_manifests,
    safe_evaluation_retry_diagnosis,
)
from general_ludd.self_improve.codex_gateway import (
    LocalProposalGateway,
    bind_compact_focus_path,
)
from general_ludd.self_improve.codex_protocol import (
    COMPACT_PROPOSAL_CONTRACT_TRANSPORT_PROTOCOL,
    COMPACT_PROPOSAL_PROTOCOL_V3,
    COMPACT_PROPOSAL_PROTOCOL_V4,
    COMPACT_V4_REPAIR_CANDIDATE_FEEDBACK_POLICY_ID,
    COMPACT_V4_REPAIR_CANDIDATE_LIMIT,
    COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID,
    COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID,
    COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID,
    COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID,
    COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID,
    DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID,
    EVALUATION_DIAGNOSIS_PROTOCOL,
    CompactLineSpan,
    CompactSpanProposal,
    CompactSpanScopeError,
    compact_v4_repair_shard_state_digest,
    compact_v4_syntax_repair_sampling_identity,
    decode_compact_span_batch,
    decode_prompt_batch,
    decode_proposal_batch,
    encode_compact_span_batch,
    encode_proposal_batch,
    expand_compact_span_proposals,
    local_proposal_attempt_identity_digest,
    proposal_batch_json_schema,
    proposal_batch_response_instruction,
)
from general_ludd.self_improve.codex_protocol import (
    LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL as LEGACY_LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
)
from general_ludd.self_improve.codex_protocol import (
    LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL as LOCAL_MODEL_ATTEMPT_OUTCOME_PROTOCOL,
)
from general_ludd.self_improve.codex_protocol import (
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL as LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
)
from general_ludd.self_improve.codex_protocol import (
    EvaluationDiagnosisProtocol as EvaluationDiagnosisProtocol,
)
from general_ludd.self_improve.codex_protocol import (
    ModelAttemptOutcomeProtocol as ModelAttemptOutcomeProtocol,
)
from general_ludd.self_improve.codex_protocol import (
    ProposalContract as ProposalContract,
)
from general_ludd.self_improve.codex_protocol import (
    ProposalEdit as ProposalEdit,
)
from general_ludd.self_improve.codex_protocol import (
    ProposalManifest as ProposalManifest,
)
from general_ludd.self_improve.codex_protocol import (
    ValidationRetryProtocol as ValidationRetryProtocol,
)
from general_ludd.self_improve.codex_protocol import (
    encode_prompt_batch as encode_prompt_batch,
)

_IMPLEMENTATION_MODULES: Final = (_protocol, _evaluation, _gateway)

def _install_protocol_delegate(name: str, value: object) -> None:
    """Bind one cross-module callback without introducing an import cycle."""
    setattr(_protocol, name, value)


_install_protocol_delegate("merge_proposal_manifests", merge_proposal_manifests)
_install_protocol_delegate(
    "_decode_compact_span_proposal",
    _gateway._decode_compact_span_proposal,
)


def _copy_compatibility_names() -> None:
    """Expose historical private helpers without duplicating their implementations."""
    for module in _IMPLEMENTATION_MODULES:
        public_names = frozenset(getattr(module, "__all__", ()))
        for name, value in vars(module).items():
            if name.startswith("__") or (
                not name.startswith("_") and name not in public_names
            ):
                continue
            globals()[name] = value


_copy_compatibility_names()


class _ComparisonFacade(ModuleType):
    """Forward compatibility-surface mutations to implementation owners."""

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__"):
            return
        for module in _IMPLEMENTATION_MODULES:
            if name in vars(module):
                setattr(module, name, value)


_module = sys.modules[__name__]
_module.__class__ = _ComparisonFacade

# Preserve the historical module identity for public classes and functions.
for _compatibility_object in (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    CompactLineSpan,
    CompactSpanProposal,
    CompactSpanScopeError,
    EvaluationDiagnosisProtocol,
    LocalProposalGateway,
    ModelAttemptOutcomeProtocol,
    PlannerFeedbackExchange,
    ProposalContract,
    ProposalEdit,
    ProposalManifest,
    ValidationRetryProtocol,
    bind_compact_focus_path,
    build_retry_prompt,
    compact_v4_repair_shard_state_digest,
    compact_v4_syntax_repair_sampling_identity,
    compare_with_codex,
    decode_compact_span_batch,
    decode_prompt_batch,
    decode_proposal_batch,
    encode_compact_span_batch,
    encode_prompt_batch,
    encode_proposal_batch,
    expand_compact_span_proposals,
    local_proposal_attempt_identity_digest,
    merge_proposal_manifests,
    proposal_batch_json_schema,
    proposal_batch_response_instruction,
    safe_evaluation_retry_diagnosis,
):
    _compatibility_object.__module__ = __name__

__all__ = [
    "COMPACT_PROPOSAL_CONTRACT_TRANSPORT_PROTOCOL",
    "COMPACT_PROPOSAL_PROTOCOL_V3",
    "COMPACT_PROPOSAL_PROTOCOL_V4",
    "COMPACT_V4_REPAIR_CANDIDATE_FEEDBACK_POLICY_ID",
    "COMPACT_V4_REPAIR_CANDIDATE_LIMIT",
    "COMPACT_V4_REPAIR_SEED_DERIVATION_POLICY_ID",
    "COMPACT_V4_REPAIR_SHARD_PROMPT_POLICY_ID",
    "COMPACT_V4_REPAIR_SHARD_STATE_POLICY_ID",
    "COMPACT_V4_REPAIR_SPAN_PROVENANCE_POLICY_ID",
    "COMPACT_V4_SYNTAX_REPAIR_SAMPLING_PROFILE_ID",
    "DEFAULT_PROPOSAL_SAMPLING_PROFILE_ID",
    "EVALUATION_DIAGNOSIS_PROTOCOL",
    "CompactLineSpan",
    "CompactSpanProposal",
    "CompactSpanScopeError",
    "LocalProposalGateway",
    "PlannerFeedbackExchange",
    "bind_compact_focus_path",
    "build_retry_prompt",
    "compact_v4_repair_shard_state_digest",
    "compact_v4_syntax_repair_sampling_identity",
    "compare_with_codex",
    "decode_compact_span_batch",
    "decode_prompt_batch",
    "decode_proposal_batch",
    "encode_compact_span_batch",
    "encode_proposal_batch",
    "expand_compact_span_proposals",
    "local_proposal_attempt_identity_digest",
    "merge_proposal_manifests",
    "proposal_batch_json_schema",
    "proposal_batch_response_instruction",
    "safe_evaluation_retry_diagnosis",
]
