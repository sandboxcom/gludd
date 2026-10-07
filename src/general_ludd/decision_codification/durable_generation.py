"""Generation-pointer and revocation operations for durable decision state."""

from __future__ import annotations

from general_ludd.decision_codification.durable_storage import _DurableStorage
from general_ludd.decision_codification.rollout import GenerationPointer
from general_ludd.decision_codification.schema import DecisionKind


class _DurableGenerationState(_DurableStorage):
    """Implement atomic pointer, hold, and revocation state transitions."""

    def current(
        self, project_id: str, decision_kind: DecisionKind
    ) -> GenerationPointer | None:
        """Read the currently published complete pointer for one exact scope."""
        self._validate_scope(project_id, decision_kind)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
        return None if row is None else self._pointer_from_row(row)

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
    ) -> GenerationPointer | None:
        """Atomically publish one pointer only when the exact expectation holds."""
        self._validate_pointer(pointer)
        if expected_candidate_digest is not None:
            self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            current_row = connection.execute(
                """
                SELECT project_id, decision_kind, candidate_digest,
                       receipt_digest, stage, epoch
                FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (pointer.project_id, pointer.decision_kind.value),
            ).fetchone()
            current = (
                None if current_row is None else self._pointer_from_row(current_row)
            )
            current_digest = current.candidate_digest if current is not None else None
            if current_digest != expected_candidate_digest:
                return None
            if current is not None and current.candidate_digest != pointer.candidate_digest:
                connection.execute(
                    """
                    INSERT INTO generation_history (
                        project_id, decision_kind, candidate_digest,
                        receipt_digest, stage, epoch
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._pointer_values(current),
                )
            epoch = self._next_epoch(
                connection, pointer.project_id, pointer.decision_kind
            )
            installed = GenerationPointer(
                project_id=pointer.project_id,
                decision_kind=pointer.decision_kind,
                candidate_digest=pointer.candidate_digest,
                receipt_digest=pointer.receipt_digest,
                stage=pointer.stage,
                epoch=epoch,
            )
            connection.execute(
                """
                INSERT INTO generation_pointers (
                    project_id, decision_kind, candidate_digest,
                    receipt_digest, stage, epoch
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, decision_kind) DO UPDATE SET
                    candidate_digest = excluded.candidate_digest,
                    receipt_digest = excluded.receipt_digest,
                    stage = excluded.stage,
                    epoch = excluded.epoch
                """,
                self._pointer_values(installed),
            )
            connection.execute(
                "DELETE FROM inactive_reasons WHERE project_id = ? AND decision_kind = ?",
                (pointer.project_id, pointer.decision_kind.value),
            )
        return installed

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Persist the first bounded drift reason for a candidate."""
        self._validate_digest(candidate_digest)
        self._validate_reason(reason)
        with self._write_connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO drift_holds(candidate_digest, reason) VALUES (?, ?)",
                (candidate_digest, reason),
            )

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether durable feedback has held a candidate."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM drift_holds WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return row is not None

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
    ) -> bool:
        """Atomically revoke the exact current generation and optional pointer."""
        self._validate_scope(project_id, decision_kind)
        self._validate_digest(expected_candidate_digest)
        with self._write_connection() as connection:
            row = connection.execute(
                """
                SELECT candidate_digest FROM generation_pointers
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
            if row is None or row["candidate_digest"] != expected_candidate_digest:
                return False
            connection.execute(
                "INSERT OR IGNORE INTO revoked_candidates(candidate_digest) VALUES (?)",
                (expected_candidate_digest,),
            )
            if remove_pointer:
                connection.execute(
                    "DELETE FROM generation_pointers WHERE project_id = ? AND decision_kind = ?",
                    (project_id, decision_kind.value),
                )
                connection.execute(
                    """
                    INSERT INTO inactive_reasons(project_id, decision_kind, reason)
                    VALUES (?, ?, 'revoked')
                    ON CONFLICT(project_id, decision_kind) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (project_id, decision_kind.value),
                )
        return True

    def inactive_reason(
        self, project_id: str, decision_kind: DecisionKind
    ) -> str | None:
        """Read the bounded tombstone retained after pointer removal."""
        self._validate_scope(project_id, decision_kind)
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT reason FROM inactive_reasons
                WHERE project_id = ? AND decision_kind = ?
                """,
                (project_id, decision_kind.value),
            ).fetchone()
        return None if row is None else str(row["reason"])

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether a candidate is durably revoked."""
        self._validate_digest(candidate_digest)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM revoked_candidates WHERE candidate_digest = ?",
                (candidate_digest,),
            ).fetchone()
        return row is not None

    def force_revoke(self, candidate_digest: str) -> None:
        """Persist an emergency integrity revocation without a pointer lookup."""
        self._validate_digest(candidate_digest)
        with self._write_connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO revoked_candidates(candidate_digest) VALUES (?)",
                (candidate_digest,),
            )
