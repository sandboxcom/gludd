"""Project-policy boundary around one managed proposal evaluation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import (
    AttemptResult,
    PlanBoundProposal,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_RunnerT = TypeVar("_RunnerT", contravariant=True)


class ManagedAttemptEvaluator(Protocol[_RunnerT]):
    """Evaluate one immutable plan-bound proposal with an owned runner."""

    def __call__(
        self,
        runner: _RunnerT,
        task: TaskSpec,
        reference: CodexReference,
        bound_proposal: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        """Evaluate one exact proposal and return immutable attempt evidence."""
        ...


def evaluate_policy_bound_managed_proposal(
    canonical_root: Path,
    operation_runner: _RunnerT,
    progress_sink: Callable[[str], None],
    task: TaskSpec,
    reference: CodexReference,
    bound_proposal: PlanBoundProposal,
    attempt: int,
    *,
    evaluator: ManagedAttemptEvaluator[_RunnerT],
    expected_attempt_identity_digest: str,
    merge: bool,
) -> AttemptResult:
    """Recheck project policy immediately around one managed evaluation."""
    if merge:
        raise ValueError("managed self-improvement cannot merge a live branch")
    proposal_paths = tuple(
        sorted(
            {
                *(edit.path for edit in bound_proposal.proposal.edits),
                *bound_proposal.proposal.tests,
            }
        )
    )
    policy_guard = SelfImproveRuntimePolicyGuard.bound(
        canonical_root,
        bound_proposal.policy_digest,
        progress_sink,
        SelfImprovePolicyViolation,
    )
    policy_guard.require(proposal_paths, emit_loaded=True)
    result = evaluator(
        operation_runner,
        task,
        reference,
        bound_proposal,
        attempt,
        expected_attempt_identity_digest=expected_attempt_identity_digest,
        merge=False,
    )
    policy_guard.require(proposal_paths)
    return result


__all__ = ("ManagedAttemptEvaluator", "evaluate_policy_bound_managed_proposal")
