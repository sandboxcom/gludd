"""Application feedback, use accounting, and rollback for durable decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from general_ludd.decision_codification.durable_generation import (
    _DurableGenerationState,
)
from general_ludd.decision_codification.durable_storage import (
    DurableGenerationStoreError,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutError,
)
from general_ludd.decision_codification.schema import (
    DecisionApplicationOutcomeV1,
    DecisionKind,
    VerifiedOutcome,
)

_MAX_IDEMPOTENCY_KEYS: Final[int] = 100_000


class _DurableFeedbackStore(_DurableGenerationState):
    """Implement durable use, terminal feedback, and rollback operations."""

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Idempotently reserve one application across all daemon workers."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        if type(maximum_use_count) is not int or maximum_use_count < 1:
            raise DurableGenerationStoreError("maximum use count is invalid")
        with self._write_connection() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (candidate_digest, application_id),
            ).fetchone()
            if existing is not None:
                return True
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM applications WHERE candidate_digest = ?",
                    (candidate_digest,),
                ).fetchone()[0]
            )
            if count >= min(maximum_use_count, _MAX_IDEMPOTENCY_KEYS):
                return False
            connection.execute(
                "INSERT INTO applications(candidate_digest, application_id) VALUES (?, ?)",
                (candidate_digest, application_id),
            )
        return True

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether an exact application was durably issued."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (candidate_digest, application_id),
            ).fetchone()
        return row is not None

    def use_count(self, candidate_digest: str) -> int:
        """Return the durable distinct-application count."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM applications WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return int(row[0])

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently append terminal feedback for an issued application."""
        validated = DecisionApplicationOutcomeV1.model_validate(
            outcome.model_dump(mode="python", by_alias=True)
        )
        with self._write_connection() as connection:
            issued = connection.execute(
                """
                SELECT 1 FROM applications
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (validated.candidate_digest, validated.application_id),
            ).fetchone()
            if issued is None:
                raise RolloutError("outcome does not reference an issued application")
            existing = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       application_id, rollout_stage, outcome, occurred_at,
                       terminal_event_id, evidence_digest
                FROM application_outcomes
                WHERE candidate_digest = ? AND application_id = ?
                """,
                (validated.candidate_digest, validated.application_id),
            ).fetchone()
            if existing is not None:
                stored = self._outcome_from_row(existing)
                if stored != validated:
                    raise RolloutError(
                        "application has conflicting outcome feedback"
                    )
                return False
            connection.execute(
                """
                INSERT INTO application_outcomes (
                    project_id, decision_kind, candidate_digest, application_id,
                    rollout_stage, outcome, occurred_at, terminal_event_id,
                    evidence_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._outcome_values(validated),
            )
        return True

    def recent_outcomes(
        self,
        candidate_digest: str,
        *,
        now: datetime,
    ) -> tuple[VerifiedOutcome, ...]:
        """Return at most 100 outcomes from the bounded seven-day window."""
        self._validate_digest(candidate_digest)
        normalized_now = self._utc(now)
        cutoff = normalized_now - timedelta(days=7)
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT outcome FROM application_outcomes
                WHERE candidate_digest = ? AND occurred_at >= ? AND occurred_at <= ?
                ORDER BY occurred_at DESC, application_id DESC
                LIMIT 100
                """,
                (
                    candidate_digest,
                    cutoff.isoformat(),
                    normalized_now.isoformat(),
                ),
            ).fetchall()
        try:
            return tuple(
                VerifiedOutcome(str(row["outcome"])) for row in reversed(rows)
            )
        except ValueError as exc:
            raise DurableGenerationStoreError(
                "durable application outcome is invalid"
            ) from exc

    def rollback(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        eligible: Callable[[GenerationPointer], bool],
        *,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Atomically restore the newest eligible durable history entry."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            current_row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
            if current_row is None:
                raise RolloutError("rollback compare-and-swap expectation failed")
            current = self._pointer_from_row(current_row)
            if current.candidate_digest != expected_candidate_digest:
                raise RolloutError("rollback compare-and-swap expectation failed")
            if expected_generation is not None and current != expected_generation:
                raise RolloutError("rollback compare-and-swap expectation failed")
            history_rows = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_history
                WHERE project_id = ? AND decision_kind = ?
                ORDER BY epoch DESC
                """,
                (project_id, decision_kind.value),
            ).fetchall()
            selected: GenerationPointer | None = None
            for row in history_rows:
                candidate = self._pointer_from_row(row)
                if (
                    not self._is_revoked_connection(
                        connection, candidate.candidate_digest
                    )
                    and not self._is_held_connection(
                        connection, candidate.candidate_digest
                    )
                    and eligible(candidate)
                ):
                    selected = candidate
                    break
            epoch = self._next_epoch(connection, project_id, decision_kind)
            if selected is None:
                connection.execute(
                    "DELETE FROM generation_pointers WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                connection.execute(
                    "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                return None
            restored = GenerationPointer(
                project_id=selected.project_id,
                decision_kind=selected.decision_kind,
                candidate_digest=selected.candidate_digest,
                receipt_digest=selected.receipt_digest,
                stage=selected.stage,
                epoch=epoch,
            )
            connection.execute(
                """
                UPDATE generation_pointers
                SET candidate_digest = ?, receipt_digest = ?, stage = ?, epoch = ?
                WHERE project_id = ? AND decision_kind = ?
                """,
                (
                    restored.candidate_digest,
                    restored.receipt_digest,
                    restored.stage.value,
                    restored.epoch,
                    project_id,
                    decision_kind.value,
                ),
            )
            connection.execute(
                """
                DELETE FROM generation_history
                WHERE project_id = ? AND decision_kind = ? AND epoch >= ?
                """,
                (project_id, decision_kind.value, selected.epoch),
            )
            connection.execute(
                "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                (project_id, decision_kind.value),
            )
        return restored
