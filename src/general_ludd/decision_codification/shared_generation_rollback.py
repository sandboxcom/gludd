"""Read recent outcomes and restore eligible history."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutError,
)
from general_ludd.decision_codification.schema import (
    DecisionApplicationOutcomeV1,
    DecisionKind,
    VerifiedOutcome,
)
from general_ludd.decision_codification.shared_generation_base import (
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_usage import _PostgresGenerationUsage


class _PostgresGenerationRollback(_PostgresGenerationUsage):
    """Read recent outcomes and restore eligible history."""

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Return the authenticated bounded seven-day feedback window."""
        self._validate_digest(candidate_digest)
        current_time = self._utc(now)
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        try:
            with self._sessions() as session:
                namespace = self._candidate_namespace(
                    session,
                    candidate_digest,
                    project_id=project_id,
                    create=False,
                    lock=False,
                )
                if namespace is None:
                    return ()
                index = self._read_outcome_index(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=False,
                )
                cutoff = current_time - timedelta(days=7)
                outcomes: list[DecisionApplicationOutcomeV1] = []
                for application_id, occurred_at in index:
                    if not cutoff <= occurred_at <= current_time:
                        continue
                    key = self._outcome_key(application_id)
                    row = self._value_row(
                        session,
                        namespace.id,
                        key,
                        lock=False,
                    )
                    if row is None:
                        raise SharedGenerationStoreError(
                            "shared outcome index is orphaned"
                        )
                    outcome = self._decode_outcome(
                        row.value,
                        key=key,
                        project_id=project_id,
                        decision_kind=decision_kind,
                    )
                    if (
                        outcome.candidate_digest != candidate_digest
                        or outcome.application_id != application_id
                        or outcome.occurred_at != occurred_at
                    ):
                        raise SharedGenerationStoreError(
                            "shared outcome index is invalid"
                        )
                    outcomes.append(outcome)
                outcomes.sort(
                    key=lambda item: (item.occurred_at, item.application_id)
                )
                return tuple(item.outcome for item in outcomes)
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
        *,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Restore the newest authenticated eligible history entry atomically."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        if expected_generation is None:
            raise SharedGenerationStoreError(
                "expected generation identity is required"
            )
        self._validate_pointer(expected_generation)
        try:
            with self._mutation(project_id, decision_kind) as session:
                namespace = self._head_namespace(
                    session,
                    project_id,
                    create=False,
                    lock=True,
                )
                if namespace is None:
                    raise RolloutError(
                        "rollback compare-and-swap expectation failed"
                    )
                current, _reason = self._read_head(
                    session,
                    project_id,
                    decision_kind,
                    namespace=namespace,
                    lock=True,
                )
                if (
                    current is None
                    or current.candidate_digest != expected_candidate_digest
                    or current != expected_generation
                ):
                    raise RolloutError(
                        "rollback compare-and-swap expectation failed"
                    )
                history = self._read_history(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=True,
                )
                selected = next(
                    (
                        candidate
                        for candidate in history
                        if self._rollback_candidate_is_eligible(
                            session,
                            candidate,
                            eligible,
                        )
                    ),
                    None,
                )
                epoch = self._next_epoch(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                )
                if selected is None:
                    self._write_head(
                        session,
                        namespace,
                        None,
                        decision_kind=decision_kind,
                        inactive_reason=None,
                    )
                    return None
                restored = replace(selected, epoch=epoch)
                self._delete_consumed_history(
                    session,
                    namespace,
                    decision_kind,
                    selected.epoch,
                )
                self._write_head(
                    session,
                    namespace,
                    restored,
                    decision_kind=decision_kind,
                    inactive_reason=None,
                )
                return restored
        except (RolloutError, SharedGenerationStoreError):
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None

    def _rollback_candidate_is_eligible(
        self,
        session: Session,
        candidate: GenerationPointer,
        eligible: Callable[[GenerationPointer], bool],
    ) -> bool:
        """Return whether one authenticated history entry may be restored."""
        return (
            not self._candidate_flag_in_session(session, candidate, "revoked")
            and not self._candidate_flag_in_session(session, candidate, "hold")
            and eligible(candidate)
        )
