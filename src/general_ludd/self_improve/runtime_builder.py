"""Composition helpers for the repository-bound self-improvement runner."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Protocol, cast

from general_ludd.self_improve._callback_compat import (
    invoke_with_optional_timeout,
    invoke_with_supported_keywords,
)
from general_ludd.self_improve.codex_comparison import CodexReference, ProposalManifest
from general_ludd.self_improve.live_candidate_wiring import (
    AzureCandidateBackendFactory,
    ContainerAppCandidateBackendFactory,
    ContainerAppCandidateBootstrapFactory,
    LiveCandidateWiringPolicy,
    LiveManagedCandidateWiring,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_runner import (
    AttemptResult,
    CapabilityEvidenceOutcomeAdapter,
    GeneratedProposal,
    ManagedOutcomeAdapter,
    ManagedSelfImproveRunner,
    PlanBoundProposal,
    PromptPlan,
    TaskSpec,
    _OutcomeAdapterFactory,
)
from general_ludd.self_improve.managed_runner import (
    _ProposalGenerator as _ManagedProposalGenerator,
)
from general_ludd.self_improve.managed_runtime_evaluation import ManagedAttemptEvaluator
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_MAX_CONFIGURED_EVIDENCE_BYTES = 67_108_864


class _ConfiguredAzureBootstrapWiring(Protocol):
    """Minimal configured Azure wiring exposed by the runtime composition API."""

    @property
    def policy(self) -> LiveCandidateWiringPolicy: ...

    @property
    def bootstrap_factory(self) -> ContainerAppCandidateBootstrapFactory: ...


class _RepositoryBindable(Protocol):
    """Repository binding exposed by the concrete managed service."""

    def bind_repository(self, repo_root: Path) -> None: ...


class _RuntimeCompositionApi(Protocol):
    """Patch-compatible runtime capabilities consumed by the composition helper."""

    MakeRunner: Callable[[Path], object]
    ModelLeaseManager: object
    ModelAcquisitionError: object
    _RepositoryBoundManagedSelfImproveRunner: Callable[..., ManagedSelfImproveRunner]
    _default_runtime_outcome_adapter: _OutcomeAdapterFactory
    plan_model_candidates: object
    unified_probe: object
    _planned_artifact_identity: object
    _report_model_acquisition_event: object
    _report_model_resolution_failure: object
    _report_model_release: object
    _build_validation_retry_prompt_plan: object
    _managed_remote_proposal_codec: object

    def _canonical_managed_repo_root(self, repo_root: Path) -> Path:
        """Return one canonical repository root."""

    def _runtime_progress(self, message: str) -> None:
        """Publish one bounded progress event."""

    def build_azure_containerapp_bootstrap_wiring(
        self,
        repo_root: Path,
        self_improve_config: Mapping[str, object],
        *,
        progress_sink: Callable[[str], None],
    ) -> _ConfiguredAzureBootstrapWiring | None:
        """Build optional config-derived Azure environment wiring."""

    def build_live_managed_candidate_wiring(
        self,
        policy: LiveCandidateWiringPolicy | None,
        *,
        azure_backend_factory: AzureCandidateBackendFactory | None,
        containerapp_backend_factory: ContainerAppCandidateBackendFactory | None,
        containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None,
        progress_sink: Callable[[str], None],
    ) -> LiveManagedCandidateWiring | None:
        """Build optional live candidate wiring."""

    def _generate_local_proposal_plan_result(
        self,
        root_runner: object,
        model_path: Path,
        prompt: PromptPlan,
        task: TaskSpec,
        reference: CodexReference,
        *,
        proposal_codec: ManagedCandidateProposalCodec[GeneratedProposal] | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float = 300.0,
    ) -> ProposalManifest | GeneratedProposal:
        """Generate one plan-bound local proposal."""

    def generate_local_proposal(
        self,
        root_runner: object,
        model_path: Path,
        prompt: str,
        *,
        timeout_seconds: float = 300.0,
    ) -> ProposalManifest:
        """Generate one legacy string-prompt proposal."""

    def evaluate_policy_bound_managed_proposal(
        self,
        canonical_root: Path,
        operation_runner: object,
        progress_sink: Callable[[str], None],
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        evaluator: ManagedAttemptEvaluator[object],
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        """Evaluate one proposal inside its project-policy boundary."""

    def evaluate_attempt(
        self,
        root_runner: object,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
        make_runner_factory: Callable[[Path], object] | None = None,
        progress_sink: Callable[[str], None] | None = None,
    ) -> AttemptResult:
        """Evaluate one managed attempt through Make-only operations."""

    def _managed_comparison_retry_builder(
        self,
        progress_sink: Callable[[str], None],
    ) -> object:
        """Build the comparison retry callback."""

    def _managed_syntax_retry_builder(
        self,
        progress_sink: Callable[[str], None],
    ) -> object:
        """Build the syntax retry callback."""

class _ProposalEvaluator(Protocol):
    """Evaluate one plan-bound proposal through the managed policy boundary."""

    def __call__(
        self,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        """Return one validated attempt result."""


class _FixedEvidenceOutcomeAdapterFactory:
    """Reuse one validated store for planning and concurrent candidate outcomes."""

    def __init__(self, store: CapabilityEvidenceStore) -> None:
        self._store = store

    def __call__(self, _cache_root: Path) -> ManagedOutcomeAdapter:
        return CapabilityEvidenceOutcomeAdapter(self._store)


def _configured_capability_evidence_store(
    self_improve_config: Mapping[str, object] | None,
) -> CapabilityEvidenceStore | None:
    """Load the exact runtime store selected by the Azure planning phase."""
    if self_improve_config is None:
        return None
    if not isinstance(self_improve_config, Mapping):
        raise ValueError("self_improve configuration must be a mapping")
    raw = self_improve_config.get("capability_evidence")
    if raw is None:
        return None
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema_version", "path"}
        or raw.get("schema_version") != 1
    ):
        raise ValueError("capability evidence configuration is invalid")
    configured_path = raw.get("path")
    if not isinstance(configured_path, str) or not configured_path:
        raise ValueError("capability evidence configuration is invalid")
    path = Path(configured_path)
    if not path.is_absolute():
        raise ValueError("capability evidence path must be absolute")
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_CONFIGURED_EVIDENCE_BYTES
        ):
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or any(
            not isinstance(record, Mapping) for record in payload
        ):
            raise ValueError
        canonical = path.resolve(strict=True)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("capability evidence store is invalid") from None
    return CapabilityEvidenceStore(str(canonical))


def _configure_live_candidate_wiring(
    runtime_api: _RuntimeCompositionApi,
    canonical_root: Path,
    self_improve_config: Mapping[str, object] | None,
    progress_sink: Callable[[str], None],
    live_candidate_policy: LiveCandidateWiringPolicy | None,
    azure_backend_factory: AzureCandidateBackendFactory | None,
    containerapp_backend_factory: ContainerAppCandidateBackendFactory | None,
    containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None,
) -> LiveManagedCandidateWiring | None:
    """Combine explicit and config-derived live candidate wiring."""
    if self_improve_config is not None:
        configured = runtime_api.build_azure_containerapp_bootstrap_wiring(
            canonical_root,
            self_improve_config,
            progress_sink=progress_sink,
        )
        if configured is not None:
            if (
                live_candidate_policy is not None
                or containerapp_backend_factory is not None
                or containerapp_bootstrap_factory is not None
            ):
                raise ValueError(
                    "configured Container App bootstrap conflicts with explicit wiring"
                )
            live_candidate_policy = configured.policy
            containerapp_bootstrap_factory = configured.bootstrap_factory
    return runtime_api.build_live_managed_candidate_wiring(
        live_candidate_policy,
        azure_backend_factory=azure_backend_factory,
        containerapp_backend_factory=containerapp_backend_factory,
        containerapp_bootstrap_factory=containerapp_bootstrap_factory,
        progress_sink=progress_sink,
    )


def _build_managed_callbacks(
    runtime_api: _RuntimeCompositionApi,
    canonical_root: Path,
    operation_runner: object,
    runner_factory: Callable[[Path], object],
    progress_sink: Callable[[str], None],
    attempt_evaluator: ManagedAttemptEvaluator[object],
) -> tuple[_ManagedProposalGenerator, _ProposalEvaluator]:
    """Bind proposal generation and evaluation to one repository."""
    def generate(
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
        *,
        proposal_codec: ManagedCandidateProposalCodec[GeneratedProposal] | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float = 300.0,
    ) -> ProposalManifest | GeneratedProposal:
        if isinstance(prompt, PromptPlan):
            return invoke_with_supported_keywords(
                runtime_api._generate_local_proposal_plan_result,
                (operation_runner, model_path, prompt, task, reference),
                {
                    "proposal_codec": proposal_codec,
                    "max_output_tokens": max_output_tokens,
                    "timeout_seconds": timeout_seconds,
                },
            )
        local_generator = runtime_api.generate_local_proposal
        generated = invoke_with_optional_timeout(
            local_generator,
            (operation_runner, model_path, prompt),
            timeout_seconds=timeout_seconds,
        )
        return (
            generated
            if proposal_codec is None
            else proposal_codec.decoder(generated.to_json())
        )

    def evaluate(
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        return runtime_api.evaluate_policy_bound_managed_proposal(
            canonical_root,
            operation_runner,
            progress_sink,
            task,
            reference,
            bound_proposal,
            attempt,
            evaluator=attempt_evaluator,
            expected_attempt_identity_digest=expected_attempt_identity_digest,
            merge=merge,
        )

    return generate, evaluate


def _compose_service(
    runtime_api: _RuntimeCompositionApi,
    canonical_root: Path,
    progress_sink: Callable[[str], None],
    proposal_generator: _ManagedProposalGenerator,
    proposal_evaluator: _ProposalEvaluator,
    outcome_adapter_factory: _OutcomeAdapterFactory | None,
    live_candidate_wiring: LiveManagedCandidateWiring | None,
) -> ManagedSelfImproveRunner:
    """Construct and bind the managed runner from validated collaborators."""
    service = runtime_api._RepositoryBoundManagedSelfImproveRunner(
        proposal_generator=proposal_generator,
        attempt_evaluator=proposal_evaluator,
        model_manager_factory=runtime_api.ModelLeaseManager,
        outcome_adapter_factory=(
            outcome_adapter_factory or runtime_api._default_runtime_outcome_adapter
        ),
        candidate_planner=runtime_api.plan_model_candidates,
        hardware_probe=runtime_api.unified_probe,
        artifact_identity=runtime_api._planned_artifact_identity,
        acquisition_event_sink=runtime_api._report_model_acquisition_event,
        resolution_failure_sink=runtime_api._report_model_resolution_failure,
        release_sink=runtime_api._report_model_release,
        progress_sink=progress_sink,
        model_acquisition_error=runtime_api.ModelAcquisitionError,
        comparison_retry_builder=runtime_api._managed_comparison_retry_builder(progress_sink),
        validation_retry_builder=runtime_api._build_validation_retry_prompt_plan,
        syntax_repair_builder=runtime_api._managed_syntax_retry_builder(progress_sink),
        live_candidate_wiring=live_candidate_wiring,
        remote_proposal_codec_factory=(
            runtime_api._managed_remote_proposal_codec
            if live_candidate_wiring is not None
            else None
        ),
    )
    cast(_RepositoryBindable, service).bind_repository(canonical_root)
    return service


def build_managed_self_improve_runner(
    repo_root: Path,
    *,
    root_runner: object | None = None,
    make_runner_factory: Callable[[Path], object] | None = None,
    attempt_evaluator: object | None = None,
    progress_sink: Callable[[str], None] | None = None,
    outcome_adapter_factory: _OutcomeAdapterFactory | None = None,
    live_candidate_policy: LiveCandidateWiringPolicy | None = None,
    azure_backend_factory: AzureCandidateBackendFactory | None = None,
    containerapp_backend_factory: ContainerAppCandidateBackendFactory | None = None,
    containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None = None,
    self_improve_config: Mapping[str, object] | None = None,
    _runtime_api: _RuntimeCompositionApi,
) -> ManagedSelfImproveRunner:
    """Compose a repository-bound local/cloud service with Make-only evaluation."""
    canonical_root = _runtime_api._canonical_managed_repo_root(repo_root)
    runner_factory = make_runner_factory or _runtime_api.MakeRunner
    operation_runner = root_runner or runner_factory(canonical_root)
    runtime_progress_sink = progress_sink or _runtime_api._runtime_progress
    configured_evidence_store = _configured_capability_evidence_store(
        self_improve_config
    )
    if configured_evidence_store is not None:
        if outcome_adapter_factory is not None:
            raise ValueError(
                "configured capability evidence conflicts with explicit outcome adapter"
            )
        outcome_adapter_factory = cast(
            _OutcomeAdapterFactory,
            _FixedEvidenceOutcomeAdapterFactory(configured_evidence_store),
        )
    live_wiring = _configure_live_candidate_wiring(
        _runtime_api,
        canonical_root,
        self_improve_config,
        runtime_progress_sink,
        live_candidate_policy,
        azure_backend_factory,
        containerapp_backend_factory,
        containerapp_bootstrap_factory,
    )
    managed_evaluator = cast(
        ManagedAttemptEvaluator[object],
        attempt_evaluator
        or partial(
            _runtime_api.evaluate_attempt,
            make_runner_factory=runner_factory,
            progress_sink=runtime_progress_sink,
        ),
    )
    proposal_generator, proposal_evaluator = _build_managed_callbacks(
        _runtime_api,
        canonical_root,
        operation_runner,
        runner_factory,
        runtime_progress_sink,
        managed_evaluator,
    )
    return _compose_service(
        _runtime_api,
        canonical_root,
        runtime_progress_sink,
        proposal_generator,
        proposal_evaluator,
        outcome_adapter_factory,
        live_wiring,
    )


__all__ = ["build_managed_self_improve_runner"]
