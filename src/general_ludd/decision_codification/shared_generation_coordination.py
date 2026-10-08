"""Coordinate exact generation heads across hosts."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from general_ludd.db.models import (
    BucketLeaseModel,
    ProjectModel,
    VariableNamespaceModel,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
)
from general_ludd.decision_codification.schema import (
    DecisionKind,
)
from general_ludd.decision_codification.shared_generation_base import (
    _HEAD_NAMESPACE,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_outcomes import _PostgresGenerationOutcomes


class _PostgresGenerationCoordination(_PostgresGenerationOutcomes):
    """Coordinate exact generation heads across hosts."""

    def verify_ready(self, project_id: str) -> None:
        """Fail startup unless the migrated shared scope is reachable."""
        self._validate_scope(project_id, DecisionKind.REVIEW)
        try:
            with self._sessions() as session, session.begin():
                self._set_transaction_timeout(session)
                project = session.scalar(
                    select(ProjectModel.project_id).where(
                        ProjectModel.project_id == project_id
                    )
                )
                session.scalar(
                    select(BucketLeaseModel.id)
                    .where(BucketLeaseModel.bucket_key == self.coordination_key(project_id))
                    .limit(1)
                )
                session.scalar(
                    select(VariableNamespaceModel.id)
                    .where(
                        VariableNamespaceModel.namespace == _HEAD_NAMESPACE,
                        VariableNamespaceModel.project_id == project_id,
                    )
                    .limit(1)
                )
                if project != project_id:
                    raise SharedGenerationStoreError(
                        "shared generation project scope is unavailable"
                    )
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation database is unavailable"
            ) from None

    def current(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> GenerationPointer | None:
        """Load and authenticate the newest committed exact-scope pointer."""
        self._validate_scope(project_id, decision_kind)
        try:
            with self._sessions() as session:
                head = self._read_head(session, project_id, decision_kind)
                pointer = head[0]
                if pointer is not None:
                    self._verify_shared_pointer(pointer)
                    self._require_epoch(session, pointer)
                return pointer
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def compare_and_swap(
        self,
        pointer: GenerationPointer,
        *,
        expected_candidate_digest: str | None,
        expected_generation: GenerationPointer | None = None,
    ) -> GenerationPointer | None:
        """Publish only from the exact current fenced generation identity."""
        self._validate_pointer(pointer)
        if expected_candidate_digest is not None:
            self._validate_digest(expected_candidate_digest)
            if expected_generation is None:
                raise SharedGenerationStoreError(
                    "expected generation identity is required"
                )
        if expected_generation is not None:
            self._validate_pointer(expected_generation)
            if (
                expected_candidate_digest is None
                or expected_generation.candidate_digest
                != expected_candidate_digest
                or expected_generation.project_id != pointer.project_id
                or expected_generation.decision_kind is not pointer.decision_kind
            ):
                raise SharedGenerationStoreError(
                    "expected generation identity is invalid"
                )
        self._verify_pointer(pointer)
        try:
            with self._mutation(pointer.project_id, pointer.decision_kind) as session:
                namespace = self._head_namespace(
                    session,
                    pointer.project_id,
                    create=True,
                    lock=True,
                )
                assert namespace is not None
                current, _reason = self._read_head(
                    session,
                    pointer.project_id,
                    pointer.decision_kind,
                    namespace=namespace,
                    lock=True,
                )
                current_digest = (
                    None if current is None else current.candidate_digest
                )
                if current is not None:
                    self._verify_shared_pointer(current)
                if current_digest != expected_candidate_digest:
                    return None
                if (
                    expected_generation is not None
                    and current != expected_generation
                ):
                    return None
                epoch = self._next_epoch(
                    session,
                    namespace,
                    pointer.project_id,
                    pointer.decision_kind,
                )
                if (
                    current is not None
                    and current.candidate_digest != pointer.candidate_digest
                ):
                    self._write_history(session, namespace, current)
                installed = replace(pointer, epoch=epoch)
                self._ensure_candidate_scope(session, installed)
                self._write_head(
                    session,
                    namespace,
                    installed,
                    decision_kind=pointer.decision_kind,
                    inactive_reason=None,
                )
                return installed
        except SharedGenerationStoreError:
            raise
        except Exception:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None
