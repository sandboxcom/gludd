"""Track bounded candidate use and record outcomes."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from general_ludd.decision_codification.rollout import (
    RolloutError,
)
from general_ludd.decision_codification.schema import (
    DecisionApplicationOutcomeV1,
)
from general_ludd.decision_codification.shared_generation_base import (
    _MAX_APPLICATIONS,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_revocation import _PostgresGenerationRevocation


class _PostgresGenerationUsage(_PostgresGenerationRevocation):
    """Track bounded candidate use and record outcomes."""

    def reserve_use(
        self,
        candidate_digest: str,
        application_id: str,
        maximum_use_count: int,
    ) -> bool:
        """Reserve one globally idempotent application below the approval cap."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
        if (
            type(maximum_use_count) is not int
            or not 1 <= maximum_use_count <= _MAX_APPLICATIONS
        ):
            raise SharedGenerationStoreError("generation use count is invalid")
        project_id, decision_kind = self._candidate_scope(candidate_digest)
        app_key = self._application_key(application_id)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                candidate_digest,
                project_id=project_id,
                create=False,
                lock=True,
            )
            if namespace is None:
                raise SharedGenerationStoreError(
                    "shared candidate scope is unavailable"
                )
            existing = self._value_row(session, namespace.id, app_key, lock=True)
            if existing is not None:
                self._require_application(
                    existing.value,
                    key=app_key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                    candidate_digest=candidate_digest,
                    application_id=application_id,
                )
                return True
            count = self._read_count(
                session,
                namespace,
                project_id,
                decision_kind,
                lock=True,
            )
            if count >= maximum_use_count or count >= _MAX_APPLICATIONS:
                return False
            self._set_record(
                session,
                namespace,
                app_key,
                project_id,
                decision_kind,
                {
                    "candidate_digest": candidate_digest,
                    "application_id": application_id,
                },
            )
            self._write_count(
                session,
                namespace,
                project_id,
                decision_kind,
                count + 1,
            )
            return True

    def has_application(self, candidate_digest: str, application_id: str) -> bool:
        """Return whether the exact authenticated application was issued."""
        self._validate_digest(candidate_digest)
        self._validate_digest(application_id)
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
                    return False
                key = self._application_key(application_id)
                row = self._value_row(session, namespace.id, key, lock=False)
                if row is None:
                    return False
                self._require_application(
                    row.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                    candidate_digest=candidate_digest,
                    application_id=application_id,
                )
                return True
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def use_count(self, candidate_digest: str) -> int:
        """Return the authenticated cross-host distinct application count."""
        self._validate_digest(candidate_digest)
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
                    return 0
                return self._read_count(
                    session,
                    namespace,
                    project_id,
                    decision_kind,
                    lock=False,
                )
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def record_outcome(self, outcome: DecisionApplicationOutcomeV1) -> bool:
        """Idempotently persist terminal feedback for an issued application."""
        try:
            validated = DecisionApplicationOutcomeV1.model_validate(
                outcome.model_dump(mode="python", by_alias=True),
                strict=True,
            )
        except (AttributeError, ValidationError):
            raise SharedGenerationStoreError(
                "generation application outcome is invalid"
            ) from None
        project_id, decision_kind = self._candidate_scope(
            validated.candidate_digest
        )
        if (
            validated.project_id != project_id
            or validated.decision_kind is not decision_kind
        ):
            raise SharedGenerationStoreError(
                "generation application outcome scope is invalid"
            )
        key = self._outcome_key(validated.application_id)
        with self._mutation(project_id, decision_kind) as session:
            namespace = self._candidate_namespace(
                session,
                validated.candidate_digest,
                project_id=project_id,
                create=False,
                lock=True,
            )
            if namespace is None:
                raise SharedGenerationStoreError(
                    "shared candidate scope is unavailable"
                )
            app_key = self._application_key(validated.application_id)
            application = self._value_row(
                session,
                namespace.id,
                app_key,
                lock=True,
            )
            if application is None:
                raise RolloutError(
                    "outcome does not reference an issued application"
                )
            self._require_application(
                application.value,
                key=app_key,
                project_id=project_id,
                decision_kind=decision_kind,
                candidate_digest=validated.candidate_digest,
                application_id=validated.application_id,
            )
            existing = self._value_row(session, namespace.id, key, lock=True)
            if existing is not None:
                stored = self._decode_outcome(
                    existing.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if stored != validated:
                    raise RolloutError(
                        "application has conflicting outcome feedback"
                    )
                return False
            self._set_record(
                session,
                namespace,
                key,
                project_id,
                decision_kind,
                validated.model_dump(mode="json", by_alias=True),
            )
            self._append_outcome_index(
                session,
                namespace,
                project_id,
                decision_kind,
                validated,
            )
            return True
