"""Atomic generation pointers and deterministic ZDD rollout lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Protocol

from general_ludd.decision_codification.artifact_store import (
    ArtifactStoreError,
    DecisionArtifactStore,
    ReceiptChainError,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionApplicationOutcomeV1,
    DecisionKind,
    DecisionRuleBundleV1,
    LifecycleState,
    ReceiptType,
    RolloutStage,
    VerifiedOutcome,
)

_STAGE_THRESHOLDS: Final[dict[RolloutStage, int]] = {
    RolloutStage.SHADOW: 0,
    RolloutStage.CANARY: 100,
    RolloutStage.CANARY_10: 1_000,
    RolloutStage.CANARY_50: 5_000,
    RolloutStage.ACTIVE: 10_000,
}
_CANARY_DOMAIN: Final[bytes] = b"general_ludd.decision_codification.canary.v1\x00"
_MAX_IDEMPOTENCY_KEYS: Final[int] = 100_000


class RolloutError(RuntimeError):
    """Raised when an invalid lifecycle transition is requested."""


@dataclass(frozen=True, slots=True)
class GenerationPointer:
    """One complete immutable pointer observed atomically by workers."""

    project_id: str
    decision_kind: DecisionKind
    candidate_digest: str
    receipt_digest: str
    stage: RolloutStage
    epoch: int


@dataclass(frozen=True, slots=True)
class OutcomeFeedback:
    """Idempotent persistence result and any resulting closed drift reason."""

    recorded: bool
    drift_reason: str | None


class GenerationStore(Protocol):
    """Atomic generation state contract shared by local and durable stores."""

    def current(
        self, project_id: str, decision_kind: DecisionKind
    ) -> GenerationPointer | None:
        """Read the current pointer for one exact decision scope."""
        ...

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Publish only when candidate and optional full identity match."""
        ...

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Hold a candidate using one closed drift reason."""
        ...

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether feedback currently holds the candidate."""
        ...

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
        expected_generation: GenerationPointer | None = None,
    ) -> bool:
        """Revoke the expected current candidate atomically."""
        ...

    def inactive_reason(
        self, project_id: str, decision_kind: DecisionKind
    ) -> str | None:
        """Read the closed reason retained after pointer removal."""
        ...

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether a candidate is durably revoked."""
        ...

    def force_revoke(self, candidate_digest: str) -> None:
        """Persist an emergency candidate revocation."""
        ...

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Idempotently reserve one bounded candidate application."""
        ...

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether an application was issued for the candidate."""
        ...

    def use_count(self, candidate_digest: str) -> int:
        """Return the candidate's distinct application count."""
        ...

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently record terminal feedback for an issued application."""
        ...

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Read the bounded recent terminal-outcome window."""
        ...

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
        *,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Restore the newest eligible prior generation or disable lookup."""
        ...


def artifacts_have_exact_bindings(
    bundle: DecisionRuleBundleV1,
    receipt: ApprovalReceiptV1,
) -> bool:
    """Return whether an approval receipt binds the exact immutable bundle."""
    return (
        receipt.candidate_digest == bundle.candidate_digest
        and receipt.corpus_digest == bundle.corpus_digest
        and receipt.feature_schema == bundle.feature_schema
        and receipt.dependency_lock_digest == bundle.dependency_lock_digest
        and receipt.training_recipe_digest == bundle.training_recipe_digest
        and receipt.project_id == bundle.project_id
        and receipt.decision_kind is bundle.decision_kind
        and receipt.risk_class == bundle.risk_scope
        and receipt.policy_digest in bundle.policy_compatibility
        and receipt.maximum_use_count <= bundle.maximum_use_count
    )


def stable_canary_bucket(
    rollout_key: bytes,
    project_id: str,
    correlation_id: str,
    candidate_digest: str,
) -> int:
    """Return a project-scoped stable HMAC bucket in ``[0, 10_000)``."""
    if not rollout_key or not project_id or not correlation_id:
        raise ValueError("canary inputs and rollout key must be non-empty")
    message = (
        _CANARY_DOMAIN
        + project_id.encode("utf-8")
        + b"\x00"
        + correlation_id.encode("utf-8")
        + b"\x00"
        + candidate_digest.encode("ascii")
    )
    digest = hmac.new(rollout_key, message, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


class AtomicGenerationStore:
    """In-process CAS repository used by local mode and repository adapters."""

    def __init__(self) -> None:
        """Initialize empty atomic pointers and bounded lifecycle state."""
        self._lock = threading.RLock()
        self._pointers: dict[tuple[str, DecisionKind], GenerationPointer] = {}
        self._history: dict[tuple[str, DecisionKind], list[GenerationPointer]] = {}
        self._epochs: dict[tuple[str, DecisionKind], int] = {}
        self._drift_holds: dict[str, str] = {}
        self._revoked: set[str] = set()
        self._inactive_reasons: dict[tuple[str, DecisionKind], str] = {}
        self._use_counts: dict[str, int] = {}
        self._applications: dict[str, set[str]] = {}
        self._outcomes: dict[
            tuple[str, str], DecisionApplicationOutcomeV1
        ] = {}

    def current(
        self, project_id: str, decision_kind: DecisionKind
    ) -> GenerationPointer | None:
        """Return the complete current generation snapshot."""
        with self._lock:
            return self._pointers.get((project_id, decision_kind))

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Atomically replace an exact scope pointer if expectation matches."""
        scope = (pointer.project_id, pointer.decision_kind)
        with self._lock:
            current = self._pointers.get(scope)
            current_digest = current.candidate_digest if current is not None else None
            if current_digest != expected_candidate_digest:
                return None
            if expected_generation is not None and current != expected_generation:
                return None
            if current is not None and current.candidate_digest != pointer.candidate_digest:
                self._history.setdefault(scope, []).append(current)
            epoch = self._epochs.get(scope, 0) + 1
            self._epochs[scope] = epoch
            installed = replace(pointer, epoch=epoch)
            self._pointers[scope] = installed
            self._inactive_reasons.pop(scope, None)
            return installed

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Place a candidate on immediate fail-closed hold."""
        with self._lock:
            self._drift_holds[candidate_digest] = reason

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether any bounded drift reason holds the candidate."""
        with self._lock:
            return candidate_digest in self._drift_holds

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
        expected_generation: GenerationPointer | None = None,
    ) -> bool:
        """Atomically mark a candidate revoked and optionally remove its pointer."""
        scope = (project_id, decision_kind)
        with self._lock:
            current = self._pointers.get(scope)
            if current is None or current.candidate_digest != expected_candidate_digest:
                return False
            if expected_generation is not None and current != expected_generation:
                return False
            self._revoked.add(expected_candidate_digest)
            if remove_pointer:
                self._pointers.pop(scope, None)
                self._inactive_reasons[scope] = "revoked"
            return True

    def inactive_reason(
        self, project_id: str, decision_kind: DecisionKind
    ) -> str | None:
        """Return a bounded tombstone reason after atomic pointer removal."""
        with self._lock:
            return self._inactive_reasons.get((project_id, decision_kind))

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether emergency or receipt-backed revocation applies."""
        with self._lock:
            return candidate_digest in self._revoked

    def force_revoke(self, candidate_digest: str) -> None:
        """Mark a digest revoked after an integrity failure without a lookup."""
        with self._lock:
            self._revoked.add(candidate_digest)

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Idempotently reserve one bounded application for a generation."""
        with self._lock:
            applications = self._applications.setdefault(candidate_digest, set())
            if application_id in applications:
                return True
            count = self._use_counts.get(candidate_digest, 0)
            if count >= maximum_use_count or len(applications) >= _MAX_IDEMPOTENCY_KEYS:
                return False
            applications.add(application_id)
            self._use_counts[candidate_digest] = count + 1
            return True

    def use_count(self, candidate_digest: str) -> int:
        """Return the number of distinct reserved applications."""
        with self._lock:
            return self._use_counts.get(candidate_digest, 0)

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether lookup reserved the exact application identity."""
        with self._lock:
            return application_id in self._applications.get(candidate_digest, set())

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently retain one terminal result for an issued application."""
        key = (outcome.candidate_digest, outcome.application_id)
        with self._lock:
            if not self.has_application(*key):
                raise RolloutError("outcome does not reference an issued application")
            existing = self._outcomes.get(key)
            if existing is not None:
                if existing != outcome:
                    raise RolloutError("application has conflicting outcome feedback")
                return False
            self._outcomes[key] = outcome
            return True

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Return the newest bounded seven-day application outcome window."""
        cutoff = now - timedelta(days=7)
        with self._lock:
            selected = sorted(
                (
                    outcome
                    for (candidate, _), outcome in self._outcomes.items()
                    if candidate == candidate_digest
                    and cutoff <= outcome.occurred_at <= now
                ),
                key=lambda outcome: (outcome.occurred_at, outcome.application_id),
            )[-100:]
        return tuple(outcome.outcome for outcome in selected)

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
        *,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Atomically restore the newest eligible history entry or disable."""
        scope = (project_id, decision_kind)
        with self._lock:
            current = self._pointers.get(scope)
            if current is None or current.candidate_digest != expected_candidate_digest:
                raise RolloutError("rollback compare-and-swap expectation failed")
            if expected_generation is not None and current != expected_generation:
                raise RolloutError("rollback compare-and-swap expectation failed")
            history = self._history.get(scope, [])
            selected_index: int | None = None
            for index in range(len(history) - 1, -1, -1):
                candidate = history[index]
                if (
                    candidate.candidate_digest not in self._revoked
                    and candidate.candidate_digest not in self._drift_holds
                    and eligible(candidate)
                ):
                    selected_index = index
                    break
            epoch = self._epochs.get(scope, current.epoch) + 1
            self._epochs[scope] = epoch
            if selected_index is None:
                self._pointers.pop(scope, None)
                self._inactive_reasons.pop(scope, None)
                return None
            selected = replace(history[selected_index], epoch=epoch)
            self._history[scope] = history[:selected_index]
            self._pointers[scope] = selected
            self._inactive_reasons.pop(scope, None)
            return selected


class RolloutController:
    """Verify artifacts and apply receipt-backed atomic lifecycle transitions."""

    def __init__(
        self,
        artifacts: DecisionArtifactStore,
        pointers: GenerationStore,
        *,
        rollout_key: bytes,
    ) -> None:
        """Bind verified artifacts, atomic pointers, and a cohort HMAC key."""
        if not rollout_key:
            raise RolloutError("rollout HMAC key is required")
        self.artifacts = artifacts
        self.pointers = pointers
        self._rollout_key = rollout_key

    def current(
        self, project_id: str, decision_kind: DecisionKind
    ) -> GenerationPointer | None:
        """Return the current immutable pointer for an exact scope."""
        return self.pointers.current(project_id, decision_kind)

    def inactive_reason(
        self, project_id: str, decision_kind: DecisionKind
    ) -> str | None:
        """Return a closed reason retained after pointer removal."""
        return self.pointers.inactive_reason(project_id, decision_kind)

    def install(
        self,
        bundle: DecisionRuleBundleV1,
        approval: ApprovalReceiptV1,
        *,
        expected_candidate_digest: str | None,
    ) -> GenerationPointer | None:
        """CAS-install one complete shadow generation."""
        stored_bundle, stored_receipt = self._verified(bundle, approval)
        if (
            stored_receipt.receipt_type not in {ReceiptType.APPROVAL, ReceiptType.RENEWAL}
            or stored_receipt.lifecycle_state is not LifecycleState.SHADOW
        ):
            raise RolloutError("generation installation requires a shadow approval")
        expected_generation = None
        if expected_candidate_digest is not None:
            expected_generation = self.current(
                stored_bundle.project_id,
                stored_bundle.decision_kind,
            )
            if (
                expected_generation is None
                or expected_generation.candidate_digest
                != expected_candidate_digest
            ):
                return None
        pointer = GenerationPointer(
            project_id=stored_bundle.project_id,
            decision_kind=stored_bundle.decision_kind,
            candidate_digest=stored_bundle.candidate_digest,
            receipt_digest=stored_receipt.receipt_digest,
            stage=RolloutStage.SHADOW,
            epoch=0,
        )
        return self.pointers.compare_and_swap(
            pointer,
            expected_candidate_digest=expected_candidate_digest,
            expected_generation=expected_generation,
        )

    def promote(
        self,
        receipt: ApprovalReceiptV1,
        *,
        expected_candidate_digest: str,
    ) -> GenerationPointer:
        """Atomically advance to the next approved rollout-plan stage."""
        if receipt.receipt_type is not ReceiptType.PROMOTION:
            raise RolloutError("promotion requires a promotion receipt")
        stage = self._stage_for_state(receipt.lifecycle_state)
        current = self.current(receipt.project_id, receipt.decision_kind)
        if (
            current is None
            or current.candidate_digest != expected_candidate_digest
            or receipt.previous_receipt_digest != current.receipt_digest
        ):
            raise RolloutError("promotion compare-and-swap expectation failed")
        bundle, stored_receipt = self._verified_receipt(receipt)
        plan = stored_receipt.rollout_plan
        try:
            current_index = plan.index(current.stage)
            target_index = plan.index(stage)
        except ValueError as exc:
            raise RolloutError("promotion stage is outside the approved plan") from exc
        if target_index != current_index + 1:
            raise RolloutError("promotion must advance exactly one approved stage")
        pointer = GenerationPointer(
            project_id=bundle.project_id,
            decision_kind=bundle.decision_kind,
            candidate_digest=bundle.candidate_digest,
            receipt_digest=stored_receipt.receipt_digest,
            stage=stage,
            epoch=0,
        )
        installed = self.pointers.compare_and_swap(
            pointer,
            expected_candidate_digest=expected_candidate_digest,
            expected_generation=current,
        )
        if installed is None:
            raise RolloutError("promotion lost its atomic generation race")
        return installed

    def selected_for_execution(
        self, pointer: GenerationPointer | None, correlation_id: str
    ) -> bool:
        """Return stage eligibility using the stable project HMAC cohort."""
        if pointer is None:
            return False
        if self.is_revoked(pointer.candidate_digest) or self.is_drift_held(
            pointer.candidate_digest
        ):
            return False
        threshold = _STAGE_THRESHOLDS[pointer.stage]
        bucket = stable_canary_bucket(
            self._rollout_key,
            pointer.project_id,
            correlation_id,
            pointer.candidate_digest,
        )
        return bucket < threshold

    def mark_drift_hold(
        self, project_id: str, decision_kind: DecisionKind, reason: str
    ) -> bool:
        """Hold the currently observed exact scope without swapping it."""
        pointer = self.current(project_id, decision_kind)
        if pointer is None:
            return False
        self.pointers.mark_drift_hold(pointer.candidate_digest, reason)
        return True

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Delegate drift status to the atomic repository."""
        return self.pointers.is_drift_held(candidate_digest)

    def revoke(
        self,
        receipt: ApprovalReceiptV1,
        *,
        expected_candidate_digest: str,
    ) -> bool:
        """Verify an append-only revocation then atomically remove the pointer."""
        if (
            receipt.receipt_type is not ReceiptType.REVOCATION
            or receipt.lifecycle_state is not LifecycleState.REVOKED
        ):
            raise RolloutError("revocation requires a revoked receipt")
        current = self.current(receipt.project_id, receipt.decision_kind)
        if current is None or receipt.previous_receipt_digest != current.receipt_digest:
            raise RolloutError("revocation does not extend the active receipt")
        self._verified_receipt(receipt)
        return self.pointers.revoke(
            receipt.project_id,
            receipt.decision_kind,
            expected_candidate_digest,
            remove_pointer=True,
            expected_generation=current,
        )

    def force_revoke_for_integrity(self, candidate_digest: str) -> bool:
        """Emergency fail-closed revocation used after integrity detection."""
        self.pointers.force_revoke(candidate_digest)
        return True

    def is_revoked(self, candidate_digest: str) -> bool:
        """Delegate revocation status to the atomic repository."""
        return self.pointers.is_revoked(candidate_digest)

    def rollback(
        self,
        receipt: ApprovalReceiptV1,
        *,
        expected_candidate_digest: str,
        now: datetime,
        policy_digest: str,
    ) -> GenerationPointer | None:
        """Atomically restore the newest compatible generation or disable."""
        if (
            receipt.receipt_type is not ReceiptType.ROLLBACK
            or receipt.lifecycle_state is not LifecycleState.ROLLED_BACK
        ):
            raise RolloutError("rollback requires a rolled-back receipt")
        current = self.current(receipt.project_id, receipt.decision_kind)
        if current is None or receipt.previous_receipt_digest != current.receipt_digest:
            raise RolloutError("rollback does not extend the active receipt")
        self._verified_receipt(receipt)

        def eligible(pointer: GenerationPointer) -> bool:
            try:
                bundle, head = self.verified_generation(pointer)
            except (ArtifactStoreError, RolloutError):
                return False
            return (
                now < bundle.expires_at
                and now < head.expires_at
                and policy_digest in bundle.policy_compatibility
            )

        return self.pointers.rollback(
            receipt.project_id,
            receipt.decision_kind,
            expected_candidate_digest,
            eligible,
            expected_generation=current,
        )

    def verified_generation(
        self, pointer: GenerationPointer
    ) -> tuple[DecisionRuleBundleV1, ApprovalReceiptV1]:
        """Resolve and verify both immutable artifacts referenced by a pointer."""
        bundle = self.artifacts.read_rule_bundle(pointer.candidate_digest)
        chain = self.artifacts.verify_receipt_chain(pointer.receipt_digest)
        receipt = chain[-1]
        self._validate_bindings(bundle, receipt)
        if (
            bundle.project_id != pointer.project_id
            or bundle.decision_kind is not pointer.decision_kind
            or receipt.receipt_digest != pointer.receipt_digest
        ):
            raise RolloutError("generation pointer scope does not match artifacts")
        return bundle, receipt

    def reserve_use(
        self, candidate_digest: str, application_id: str, maximum_use_count: int
    ) -> bool:
        """Idempotently reserve one application against an approval bound."""
        return self.pointers.reserve_use(
            candidate_digest, application_id, maximum_use_count
        )

    def use_count(self, candidate_digest: str) -> int:
        """Return distinct application count for observability and bounds."""
        return self.pointers.use_count(candidate_digest)

    def record_application_outcome(
        self,
        outcome: DecisionApplicationOutcomeV1,
    ) -> OutcomeFeedback:
        """Persist terminal feedback and fail closed on unsafe live drift."""
        recorded = self.pointers.record_outcome(outcome)
        if not recorded:
            return OutcomeFeedback(recorded=False, drift_reason=None)
        window = self.pointers.recent_outcomes(
            outcome.candidate_digest,
            now=outcome.occurred_at,
        )
        drift_reason: str | None = None
        if outcome.outcome is VerifiedOutcome.UNSAFE:
            drift_reason = "safety_violation"
        elif window:
            failures = sum(item is VerifiedOutcome.FAILURE for item in window)
            if failures / len(window) > 0.01:
                drift_reason = "failure_rate"
        if drift_reason is not None:
            self.pointers.mark_drift_hold(outcome.candidate_digest, drift_reason)
        return OutcomeFeedback(recorded=True, drift_reason=drift_reason)

    def _verified(
        self, bundle: DecisionRuleBundleV1, receipt: ApprovalReceiptV1
    ) -> tuple[DecisionRuleBundleV1, ApprovalReceiptV1]:
        stored = self.artifacts.read_rule_bundle(bundle.candidate_digest)
        if stored != bundle:
            raise RolloutError("candidate object differs from stored artifact")
        return self._verified_receipt(receipt)

    def _verified_receipt(
        self, receipt: ApprovalReceiptV1
    ) -> tuple[DecisionRuleBundleV1, ApprovalReceiptV1]:
        try:
            chain = self.artifacts.verify_receipt_chain(receipt.receipt_digest)
        except ReceiptChainError as exc:
            raise RolloutError("receipt chain verification failed") from exc
        stored_receipt = chain[-1]
        if stored_receipt != receipt:
            raise RolloutError("receipt object differs from stored artifact")
        bundle = self.artifacts.read_rule_bundle(receipt.candidate_digest)
        self._validate_bindings(bundle, stored_receipt)
        return bundle, stored_receipt

    @staticmethod
    def _validate_bindings(
        bundle: DecisionRuleBundleV1, receipt: ApprovalReceiptV1
    ) -> None:
        if not artifacts_have_exact_bindings(bundle, receipt):
            raise RolloutError("approval receipt does not bind the rule artifact")

    @staticmethod
    def _stage_for_state(state: LifecycleState) -> RolloutStage:
        try:
            return RolloutStage(state.value)
        except ValueError as exc:
            raise RolloutError("lifecycle state is not an executable stage") from exc


__all__ = [
    "AtomicGenerationStore",
    "GenerationPointer",
    "GenerationStore",
    "OutcomeFeedback",
    "RolloutController",
    "RolloutError",
    "artifacts_have_exact_bindings",
    "stable_canary_bucket",
]
