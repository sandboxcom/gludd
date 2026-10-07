"""Injected service boundaries for the managed self-improvement runner."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from general_ludd.hardware.survey import HardwareInventory
from general_ludd.local_model import LocalModelConfig
from general_ludd.self_improve._callback_compat import invoke_with_supported_keywords
from general_ludd.self_improve.codex_comparison import (
    CodexReference,
    CompactSpanProposal,
    ProposalManifest,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_runner_contracts import (
    AttemptResult,
    GeneratedProposal,
    PlanBoundProposal,
    PromptPlan,
    TaskSpec,
)
from general_ludd.self_improve.model_candidate_planner import (
    CodeTaskShape,
    PlannedModelCandidate,
    load_latest_failed_model_ids,
    record_self_improve_outcome,
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BackendInfrastructureError,
    LocalGGUFCandidateIdentity,
)
from general_ludd.self_improve.model_lifecycle import (
    AcquiredModel,
    ModelAcquisitionEvent,
    ModelArtifactIdentity,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


class ManagedOutcomeAdapter(Protocol):
    """Durable evidence seam shared by JSON CLI and future database workers."""

    planner_store: object

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Return failures scoped to the exact task and prompt protocol."""

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> object:
        """Durably record one candidate result and return its identity."""


@runtime_checkable
class _FailureLoader(Protocol):
    def __call__(
        self,
        store: CapabilityEvidenceStore,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]: ...


@runtime_checkable
class _OutcomeRecorder(Protocol):
    def __call__(
        self,
        store: CapabilityEvidenceStore,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> int: ...


class CapabilityEvidenceOutcomeAdapter:
    """Persist managed outcomes through the established capability evidence store."""

    def __init__(
        self,
        store: CapabilityEvidenceStore,
        *,
        failure_loader: _FailureLoader = load_latest_failed_model_ids,
        outcome_recorder: _OutcomeRecorder = record_self_improve_outcome,
    ) -> None:
        """Bind an existing durable store and the canonical outcome functions."""
        self.planner_store: object = store
        self._store = store
        self._failure_loader = failure_loader
        self._outcome_recorder = outcome_recorder

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Load exact-identity failures through the canonical parser."""
        return self._failure_loader(
            self._store,
            task_text=task_text,
            attempt_identity_digest=attempt_identity_digest,
        )

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: PlannedModelCandidate,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> int:
        """Write one canonical revision- and protocol-bound result."""
        return self._outcome_recorder(
            self._store,
            task_text=task_text,
            candidate=candidate,
            succeeded=succeeded,
            attempt_identity_digest=attempt_identity_digest,
        )


@runtime_checkable
class _Reservation(Protocol):
    def mark_eligible(self, identity: ModelArtifactIdentity) -> None: ...

    def mark_failed(self, identity: ModelArtifactIdentity) -> None: ...


class _LeaseManager(Protocol):
    cache_root: Path

    def resolve_revision(self, repo_id: str) -> str: ...

    def owned_identities_for_model_ids(
        self,
        model_ids: tuple[str, ...],
    ) -> tuple[ModelArtifactIdentity, ...]: ...

    def reserve_plan(
        self,
        identities: tuple[ModelArtifactIdentity, ...],
        *,
        failure_hints: tuple[ModelArtifactIdentity, ...] = (),
    ) -> AbstractContextManager[_Reservation]: ...

    def acquire(
        self,
        task_description: str,
        *,
        explicit_path: Path | None = None,
        model_config: LocalModelConfig | None = None,
        resolved_revision: str | None = None,
    ) -> AbstractContextManager[AcquiredModel]: ...


@runtime_checkable
class _ModelManagerFactory(Protocol):
    def __call__(
        self,
        *,
        event_sink: Callable[[ModelAcquisitionEvent], None] | None = None,
    ) -> _LeaseManager: ...


@runtime_checkable
class _OutcomeAdapterFactory(Protocol):
    def __call__(self, cache_root: Path) -> ManagedOutcomeAdapter: ...


@runtime_checkable
class _CandidatePlanner(Protocol):
    def __call__(
        self,
        task_text: str,
        output_tokens: int,
        prior_failed_model_ids: tuple[str, ...],
        hardware: HardwareInventory,
        evidence_store: CapabilityEvidenceStore,
        revision_resolver: Callable[[str], str],
        *,
        input_tokens: int | None = None,
        task_shape: CodeTaskShape | None = None,
        max_candidates: int = 3,
        on_resolution_failure: Callable[[LocalModelConfig, str], None] | None = None,
    ) -> tuple[PlannedModelCandidate, ...]: ...


@runtime_checkable
class _ProposalGenerator(Protocol):
    def __call__(
        self,
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
        *,
        proposal_codec: ManagedCandidateProposalCodec[GeneratedProposal] | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float = 300.0,
    ) -> ProposalManifest | GeneratedProposal: ...


@runtime_checkable
class _RemoteProposalCodecFactory(Protocol):
    """Prepare one exact remote request/decoder without invoking a provider."""

    def __call__(
        self,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
        *,
        max_output_tokens: int | None = None,
    ) -> ManagedCandidateProposalCodec[GeneratedProposal] | None: ...


@dataclass(frozen=True, slots=True)
class LocalProposalInvocation:
    """Exact local arguments plus the shared provider-neutral envelope."""

    model_path: Path
    prompt: PromptPlan | str
    task: TaskSpec
    reference: CodexReference
    proposal_codec: ManagedCandidateProposalCodec[GeneratedProposal] | None = None


class LocalProposalBackendAdapter:
    """Expose the established local generator as one exact-candidate backend."""

    def __init__(
        self,
        identity: LocalGGUFCandidateIdentity,
        generator: _ProposalGenerator,
    ) -> None:
        """Bind one acquired identity without changing the generator callback."""
        if not isinstance(identity, LocalGGUFCandidateIdentity):
            raise ValueError("identity must be a LocalGGUFCandidateIdentity")
        self._identity = identity
        self._generator = generator

    @property
    def candidate_identity(self) -> LocalGGUFCandidateIdentity:
        """Return the exact acquired local artifact identity."""
        return self._identity

    def generate(
        self,
        request: LocalProposalInvocation,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProposalManifest | GeneratedProposal:
        """Forward the approved deadline when supported and type worker timeouts."""
        arguments = (request.model_path, request.prompt, request.task, request.reference)
        optional_keywords: dict[str, object] = {
            "max_output_tokens": max_output_tokens,
            "timeout_seconds": timeout_seconds,
        }
        if request.proposal_codec is not None:
            optional_keywords["proposal_codec"] = request.proposal_codec
        try:
            return invoke_with_supported_keywords(
                self._generator,
                arguments,
                optional_keywords,
            )
        except TimeoutError:
            pass
        raise BackendInfrastructureError(BackendFailure.TIMEOUT) from None


@dataclass(frozen=True, slots=True)
class _LocalProposalDecodeRejected:
    """Content-free local protocol rejection retained as a trial response."""


class _RoutingLocalProposalBackend:
    """Convert deterministic local decode failures into calibratable responses."""

    def __init__(self, backend: LocalProposalBackendAdapter) -> None:
        self._backend = backend

    @property
    def candidate_identity(self) -> LocalGGUFCandidateIdentity:
        """Return the exact acquired artifact identity."""
        return self._backend.candidate_identity

    def generate(
        self,
        request: LocalProposalInvocation,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProposalManifest | GeneratedProposal | _LocalProposalDecodeRejected:
        """Invoke once and retain only the fact of deterministic invalid output."""
        try:
            return self._backend.generate(
                request,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        except ValueError:
            return _LocalProposalDecodeRejected()


@runtime_checkable
class _SyntaxRepairBuilder(Protocol):
    def __call__(
        self,
        plan: PromptPlan,
        compact_proposals: tuple[CompactSpanProposal, ...],
        diagnostics: str,
    ) -> PromptPlan: ...


@runtime_checkable
class _AttemptEvaluator(Protocol):
    def __call__(
        self,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult: ...


@dataclass(frozen=True, slots=True)
class ManagedRunResult:
    """Bounded service result retaining final evidence and durable identities."""

    final_result: AttemptResult
    attempts: int
    plan_identity_digest: str
    attempted_model_ids: tuple[str, ...]
    outcome_record_ids: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        """Return whether the final Codex comparison accepted the candidate."""
        return self.final_result.comparison.accepted

    @property
    def attempt_identity_digest(self) -> str:
        """Expose the final attempt identity for durable event correlation."""
        return self.final_result.attempt_identity_digest


@dataclass(frozen=True, slots=True)
class _PendingSyntaxRepair:
    """One same-candidate regeneration scheduled inside the approved attempt bound."""

    prompt: PromptPlan
    candidate: PlannedModelCandidate | None
    candidate_identity: ModelArtifactIdentity | None


@dataclass(slots=True)
class _ManagedRunState:
    """Mutable orchestration state shared by bounded attempt helpers."""

    prompt: PromptPlan | str
    final: AttemptResult | None = None
    model_manager: _LeaseManager | None = None
    outcomes: ManagedOutcomeAdapter | None = None
    candidates: tuple[PlannedModelCandidate, ...] | None = None
    reservation: _Reservation | None = None
    candidate_index: int = 0
    attempted_models: list[str] = field(default_factory=list)
    outcome_ids: list[str] = field(default_factory=list)
    pending_repair: _PendingSyntaxRepair | None = None
    syntax_repair_used: bool = False


@dataclass(frozen=True, slots=True)
class _ManagedAttemptContext:
    """Resolved model and prompt inputs for one bounded attempt."""

    prompt: PromptPlan | str
    candidate: PlannedModelCandidate | None
    candidate_identity: ModelArtifactIdentity | None
    repairing: bool
    use_mechanical: bool


_COMPATIBILITY_MODULE = "general_ludd.self_improve.managed_runner"
CapabilityEvidenceOutcomeAdapter.__module__ = _COMPATIBILITY_MODULE
LocalProposalBackendAdapter.__module__ = _COMPATIBILITY_MODULE
LocalProposalInvocation.__module__ = _COMPATIBILITY_MODULE
ManagedOutcomeAdapter.__module__ = _COMPATIBILITY_MODULE
ManagedRunResult.__module__ = _COMPATIBILITY_MODULE


__all__ = (
    "CapabilityEvidenceOutcomeAdapter",
    "LocalProposalBackendAdapter",
    "LocalProposalInvocation",
    "ManagedOutcomeAdapter",
    "ManagedRunResult",
)
