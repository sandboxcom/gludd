"""Composition helpers for the repository-bound self-improvement runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from general_ludd.self_improve.codex_comparison import CodexReference, ProposalManifest
from general_ludd.self_improve.live_candidate_wiring import (
    AzureCandidateBackendFactory,
    ContainerAppCandidateBackendFactory,
    ContainerAppCandidateBootstrapFactory,
    LiveCandidateWiringPolicy,
    LiveManagedCandidateWiring,
)
from general_ludd.self_improve.managed_runner import (
    AttemptResult,
    GeneratedProposal,
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

if TYPE_CHECKING:
    from general_ludd.self_improve.runtime import (
        _AttemptEvaluationAdapter,
        _MakeRunnerFactory,
        _RuntimeMakeRunner,
    )

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


def _configure_live_candidate_wiring(
    canonical_root: Path,
    self_improve_config: Mapping[str, object] | None,
    progress_sink: Callable[[str], None],
    live_candidate_policy: LiveCandidateWiringPolicy | None,
    azure_backend_factory: AzureCandidateBackendFactory | None,
    containerapp_backend_factory: ContainerAppCandidateBackendFactory | None,
    containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None,
) -> LiveManagedCandidateWiring | None:
    """Combine explicit and config-derived live candidate wiring."""
    from general_ludd.self_improve import runtime as runtime_api

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
    canonical_root: Path,
    operation_runner: _RuntimeMakeRunner,
    runner_factory: _MakeRunnerFactory,
    progress_sink: Callable[[str], None],
    attempt_evaluator: ManagedAttemptEvaluator[_RuntimeMakeRunner],
) -> tuple[_ManagedProposalGenerator, _ProposalEvaluator]:
    """Bind proposal generation and evaluation to one repository."""
    from general_ludd.self_improve import runtime as runtime_api

    def generate(
        model_path: Path,
        prompt: PromptPlan | str,
        task: TaskSpec,
        reference: CodexReference,
    ) -> ProposalManifest | GeneratedProposal:
        if isinstance(prompt, PromptPlan):
            return runtime_api._generate_local_proposal_plan_result(
                operation_runner,
                model_path,
                prompt,
                task,
                reference,
            )
        return runtime_api.generate_local_proposal(operation_runner, model_path, prompt)

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
    canonical_root: Path,
    progress_sink: Callable[[str], None],
    proposal_generator: _ManagedProposalGenerator,
    proposal_evaluator: _ProposalEvaluator,
    outcome_adapter_factory: _OutcomeAdapterFactory | None,
    live_candidate_wiring: LiveManagedCandidateWiring | None,
) -> ManagedSelfImproveRunner:
    """Construct and bind the managed runner from validated collaborators."""
    from general_ludd.self_improve import runtime as runtime_api

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
    service.bind_repository(canonical_root)
    return service


def build_managed_self_improve_runner(
    repo_root: Path,
    *,
    root_runner: _RuntimeMakeRunner | None = None,
    make_runner_factory: _MakeRunnerFactory | None = None,
    attempt_evaluator: _AttemptEvaluationAdapter | None = None,
    progress_sink: Callable[[str], None] | None = None,
    outcome_adapter_factory: _OutcomeAdapterFactory | None = None,
    live_candidate_policy: LiveCandidateWiringPolicy | None = None,
    azure_backend_factory: AzureCandidateBackendFactory | None = None,
    containerapp_backend_factory: ContainerAppCandidateBackendFactory | None = None,
    containerapp_bootstrap_factory: ContainerAppCandidateBootstrapFactory | None = None,
    self_improve_config: Mapping[str, object] | None = None,
) -> ManagedSelfImproveRunner:
    """Compose a repository-bound local/cloud service with Make-only evaluation."""
    from general_ludd.self_improve import runtime as runtime_api

    canonical_root = runtime_api._canonical_managed_repo_root(repo_root)
    runner_factory = make_runner_factory or runtime_api.MakeRunner
    operation_runner = root_runner or runner_factory(canonical_root)
    runtime_progress_sink = progress_sink or runtime_api._runtime_progress
    live_wiring = _configure_live_candidate_wiring(
        canonical_root,
        self_improve_config,
        runtime_progress_sink,
        live_candidate_policy,
        azure_backend_factory,
        containerapp_backend_factory,
        containerapp_bootstrap_factory,
    )
    managed_evaluator = cast(
        runtime_api.ManagedAttemptEvaluator[runtime_api._RuntimeMakeRunner],
        attempt_evaluator
        or partial(
            runtime_api.evaluate_attempt,
            make_runner_factory=runner_factory,
            progress_sink=runtime_progress_sink,
        ),
    )
    proposal_generator, proposal_evaluator = _build_managed_callbacks(
        canonical_root,
        operation_runner,
        runner_factory,
        runtime_progress_sink,
        managed_evaluator,
    )
    return _compose_service(
        canonical_root,
        runtime_progress_sink,
        proposal_generator,
        proposal_evaluator,
        outcome_adapter_factory,
        live_wiring,
    )


__all__ = ["build_managed_self_improve_runner"]
