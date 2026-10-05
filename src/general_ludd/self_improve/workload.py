"""Top-level cross-backend self-improvement workload orchestrator.

The workload exposes a discover -> predict -> execute -> evaluate -> learn
pipeline that can compose local GGUF, Azure Foundry, Azure Container Apps,
Azure VM/VMSS, and catalog free-tier candidates. All backends are supplied by
a registry protocol so callers can inject fakes or live wiring without changing
the orchestrator.

Execution is concurrent across candidates so that independent cloud and local
calls progress simultaneously. Each candidate execution is wrapped in a uniform
protocol envelope, atomically claims a durable todo before invocation, releases
it after invocation, and runs terminal cleanup callbacks regardless of outcome.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    BackendInfrastructureError,
    BackendPolicyError,
    BoundedCandidateSession,
    CandidateBackend,
    ModelCandidateIdentity,
)

_WORKLOAD_PROTOCOL = "gludd-self-improve-workload-v1"


@runtime_checkable
class CandidateBackendRegistry(Protocol):
    """Discover candidate backends without constructing cloud clients."""

    def discover(self) -> tuple[CandidateBackend[str, str], ...]:
        """Return zero or more backends available from this registry."""
        ...


@runtime_checkable
class WorkloadTodoStore(Protocol):
    """Atomic durable claim/release boundary for candidate execution todos."""

    def claim(self, identity: ModelCandidateIdentity) -> str:
        """Atomically claim one todo for ``identity`` and return its claim id."""
        ...

    def release(self, claim_id: str) -> None:
        """Release a previously claimed todo."""
        ...


@dataclass(frozen=True, slots=True)
class WorkloadProtocolEnvelope:
    """Provider-neutral request/response envelope shared across every backend."""

    protocol: str
    request: str
    response: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkloadCandidate:
    """One discovered candidate with its immutable identity and backend."""

    identity: ModelCandidateIdentity
    backend: CandidateBackend[str, str]


@dataclass(frozen=True, slots=True)
class WorkloadExecution:
    """One candidate invocation outcome."""

    candidate_identity: ModelCandidateIdentity
    request: str
    response: str
    passed: bool
    evidence_identity_digest: str = ""
    envelope: WorkloadProtocolEnvelope | None = None
    claim_id: str | None = None

    def __post_init__(self) -> None:
        """Derive the evidence identity from the candidate when omitted."""
        if not self.evidence_identity_digest:
            object.__setattr__(
                self,
                "evidence_identity_digest",
                self.candidate_identity.evidence_identity_digest,
            )


@dataclass(frozen=True, slots=True)
class WorkloadResult:
    """Completed workload output across all pipeline phases."""

    phase: str
    candidates: tuple[WorkloadCandidate, ...]
    executions: tuple[WorkloadExecution, ...]
    evaluation: dict[str, Any]
    learned_outcomes: list[dict[str, Any]]


class SelfImprovementWorkload:
    """Orchestrate self-improvement across heterogeneous model candidates."""

    def __init__(
        self,
        registries: Sequence[CandidateBackendRegistry],
        *,
        azure_enabled: bool = False,
        external_enabled: bool = False,
        budget: BackendCallBudget | None = None,
        evaluator: Callable[[Sequence[WorkloadExecution]], dict[str, Any]] | None = None,
        learner: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        todo_store: WorkloadTodoStore | None = None,
        cleanup_callbacks: Sequence[Callable[[WorkloadExecution], None]] | None = None,
        max_workers: int | None = None,
    ) -> None:
        """Bind registries, opt-ins, budget, and optional evaluation/learning hooks."""
        if isinstance(registries, str) or not isinstance(registries, Sequence):
            raise ValueError("registries must be a sequence of CandidateBackendRegistry")
        if evaluator is not None and not callable(evaluator):
            raise ValueError("evaluator must be callable")
        if learner is not None and not callable(learner):
            raise ValueError("learner must be callable")
        if todo_store is not None and not isinstance(todo_store, WorkloadTodoStore):
            raise ValueError("todo_store must implement WorkloadTodoStore")
        callbacks = tuple(cleanup_callbacks or ())
        for callback in callbacks:
            if not callable(callback):
                raise ValueError("cleanup_callbacks must contain only callables")
        if max_workers is not None and (
            isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1
        ):
            raise ValueError("max_workers must be a positive integer")
        self._registries = registries
        self._azure_enabled = azure_enabled
        self._external_enabled = external_enabled
        self._budget = budget
        self._evaluator = evaluator
        self._learner = learner
        self._todo_store = todo_store
        self._cleanup_callbacks = callbacks
        self._max_workers = max_workers

    def discover(self) -> tuple[WorkloadCandidate, ...]:
        """Collect candidates from every bound registry."""
        candidates: list[WorkloadCandidate] = []
        for registry in self._registries:
            if not isinstance(registry, CandidateBackendRegistry):
                raise ValueError("registries must implement CandidateBackendRegistry")
            for backend in registry.discover():
                candidates.append(WorkloadCandidate(backend.candidate_identity, backend))
        return tuple(candidates)

    def predict(
        self,
        task: str,
        candidates: tuple[WorkloadCandidate, ...],
    ) -> tuple[WorkloadCandidate, ...]:
        """Select candidates predicted to be useful for the task.

        The default predictor admits every discovered candidate; callers may
        subclass or inject an evaluator to implement ranking or filtering.
        """
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be non-empty text")
        return candidates

    def _budget_or_default(self) -> BackendCallBudget:
        return self._budget or BackendCallBudget(
            max_calls=4,
            max_input_tokens=2_000,
            max_output_tokens=1_000,
            max_total_tokens=4_000,
            max_cost_microusd=50_000,
            timeout_seconds=30.0,
        )

    def _execute_one(
        self,
        task: str,
        candidate: WorkloadCandidate,
    ) -> WorkloadExecution:
        """Invoke a single candidate with claim, envelope, and cleanup."""
        identity = candidate.identity
        claim_id: str | None = None
        if self._todo_store is not None:
            claim_id = self._todo_store.claim(identity)

        budget = self._budget_or_default()
        session = BoundedCandidateSession(
            candidate.backend,
            budget,
            azure_enabled=self._azure_enabled,
            external_enabled=self._external_enabled,
        )
        try:
            response = session.generate(
                task,
                input_tokens=max(1, len(task.encode("utf-8")) // 4),
                max_output_tokens=100,
                estimated_cost_microusd=0,
            )
            passed = response.startswith("proposal:")
        except (BackendPolicyError, BackendInfrastructureError):
            response = ""
            passed = False

        envelope = WorkloadProtocolEnvelope(
            protocol=_WORKLOAD_PROTOCOL,
            request=task,
            response=response,
            metadata={
                "provider": identity.provider.value,
                "evidence_identity_digest": identity.evidence_identity_digest,
            },
        )
        execution = WorkloadExecution(
            candidate_identity=identity,
            evidence_identity_digest=identity.evidence_identity_digest,
            request=task,
            response=response,
            passed=passed,
            envelope=envelope,
            claim_id=claim_id,
        )

        try:
            for callback in self._cleanup_callbacks:
                callback(execution)
        finally:
            if claim_id is not None and self._todo_store is not None:
                self._todo_store.release(claim_id)

        return execution

    def execute(
        self,
        task: str,
        candidates: tuple[WorkloadCandidate, ...],
    ) -> tuple[WorkloadExecution, ...]:
        """Invoke each predicted candidate through a bounded policy session."""
        if not candidates:
            return ()

        max_workers = self._max_workers
        if max_workers is None:
            max_workers = min(32, len(candidates) or 1)

        # Single candidate avoids thread-pool overhead.
        if len(candidates) == 1:
            return (self._execute_one(task, candidates[0]),)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="self-improve-workload-",
        ) as executor:
            futures = [executor.submit(self._execute_one, task, candidate) for candidate in candidates]
            executions = [future.result() for future in concurrent.futures.as_completed(futures)]

        # Preserve candidate order for stable evaluation and learning.
        order = {id(candidate.identity): index for index, candidate in enumerate(candidates)}
        executions.sort(key=lambda execution: order[id(execution.candidate_identity)])
        return tuple(executions)

    def evaluate(self, executions: Sequence[WorkloadExecution]) -> dict[str, Any]:
        """Summarize execution outcomes into a secret-free evaluation record."""
        if self._evaluator is not None:
            return self._evaluator(executions)
        total = len(executions)
        passed = sum(1 for execution in executions if execution.passed)
        return {
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total > 0 else 0.0,
        }

    def learn(self, evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        """Record the evaluation for future learning."""
        if self._learner is not None:
            return self._learner(evaluation)
        return [{"status": "recorded", "evaluation": evaluation}]

    def run(self, task: str) -> WorkloadResult:
        """Run the full discover -> predict -> execute -> evaluate -> learn pipeline."""
        candidates = self.discover()
        predicted = self.predict(task, candidates)
        executions = self.execute(task, predicted)
        evaluation = self.evaluate(executions)
        learned = self.learn(evaluation)
        return WorkloadResult(
            phase="completed",
            candidates=candidates,
            executions=executions,
            evaluation=evaluation,
            learned_outcomes=learned,
        )


__all__ = (
    "CandidateBackendRegistry",
    "SelfImprovementWorkload",
    "WorkloadCandidate",
    "WorkloadExecution",
    "WorkloadProtocolEnvelope",
    "WorkloadResult",
    "WorkloadTodoStore",
)
