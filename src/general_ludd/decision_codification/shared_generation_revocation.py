"""Apply generation drift holds and revocations."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from general_ludd.decision_codification.rollout import (
    GenerationPointer,
)
from general_ludd.decision_codification.schema import (
    DecisionKind,
)
from general_ludd.decision_codification.shared_generation_base import (
    _SAFE_IDENTIFIER,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_coordination import _PostgresGenerationCoordination


class _PostgresGenerationRevocation(_PostgresGenerationCoordination):
    """Apply generation drift holds and revocations."""

    def mark_drift_hold(self, candidate_digest: str, reason: str) -> None:
        """Persist the first closed drift reason across every host."""
        self._validate_digest(candidate_digest)
        if _SAFE_IDENTIFIER.fullmatch(reason) is None:
            raise SharedGenerationStoreError("generation drift reason is invalid")
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=True,
                lock=True,
            )
            assert namespace is not None
            existing = self._value_row(session, namespace.id, "hold", lock=True)
            if existing is None:
                self._set_record(
                    session,
                    namespace,
                    "hold",
                    project_id,
                    decision_kind,
                    {"candidate_digest": candidate_digest, "reason": reason},
                )
            else:
                payload = self._decode_record(
                    existing.value,
                    key="hold",
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if payload.get("candidate_digest") != candidate_digest:
                    raise SharedGenerationStoreError(
                        "shared generation hold is invalid"
                    )

    def is_drift_held(self, candidate_digest: str) -> bool:
        """Return whether the authenticated candidate scope is held."""
        return self._candidate_flag(candidate_digest, "hold")

    def revoke(
        self,
        project_id: str,
        decision_kind: DecisionKind,
        expected_candidate_digest: str,
        *,
        remove_pointer: bool,
        expected_generation: GenerationPointer | None = None,
    ) -> bool:
        """Revoke only the exact committed candidate and optionally tombstone it."""
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
                    return False
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
                    return False
                candidate_namespace = self._candidate_namespace(
                    session,
                    expected_candidate_digest,
                    project_id=project_id,
                    create=True,
                    lock=True,
                )
                assert candidate_namespace is not None
                self._set_record(
                    session,
                    candidate_namespace,
                    "revoked",
                    project_id,
                    decision_kind,
                    {"candidate_digest": expected_candidate_digest},
                )
                if remove_pointer:
                    self._next_epoch(
                        session,
                        namespace,
                        project_id,
                        decision_kind,
                    )
                    self._write_head(
                        session,
                        namespace,
                        None,
                        decision_kind=decision_kind,
                        inactive_reason="revoked",
                    )
                return True
        except SharedGenerationStoreError:
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None

    def inactive_reason(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> str | None:
        """Read the authenticated closed reason for an absent pointer."""
        self._validate_scope(project_id, decision_kind)
        try:
            with self._sessions() as session:
                pointer, reason = self._read_head(
                    session,
                    project_id,
                    decision_kind,
                )
                if pointer is not None and reason is not None:
                    raise SharedGenerationStoreError(
                        "shared generation head is invalid"
                    )
                return reason
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def is_revoked(self, candidate_digest: str) -> bool:
        """Return whether a candidate carries an authenticated revocation."""
        return self._candidate_flag(candidate_digest, "revoked")

    def force_revoke(self, candidate_digest: str) -> None:
        """Persist an emergency revocation from the durable candidate scope."""
        self._validate_digest(candidate_digest)
        project_id, decision_kind = self._candidate_scope(
            candidate_digest,
            require_artifact=False,
        )
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=True,
                lock=True,
            )
            assert namespace is not None
            self._set_record(
                session,
                namespace,
                "revoked",
                project_id,
                decision_kind,
                {"candidate_digest": candidate_digest},
            )
