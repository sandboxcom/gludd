"""Manage exact candidate scopes, flags, and use counts."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    VariableNamespaceModel,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
)
from general_ludd.decision_codification.schema import (
    DecisionKind,
)
from general_ludd.decision_codification.shared_generation_base import (
    _MAX_APPLICATIONS,
    _SAFE_IDENTIFIER,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_history import _PostgresGenerationHistory


class _PostgresGenerationCandidates(_PostgresGenerationHistory):
    """Manage exact candidate scopes, flags, and use counts."""

    def _ensure_candidate_scope(
        self,
        session: Session,
        pointer: GenerationPointer,
    ) -> None:
        namespace = self._candidate_namespace(
            session,
            pointer.candidate_digest,
            project_id=pointer.project_id,
            create=True,
            lock=True,
        )
        assert namespace is not None
        key = "scope"
        row = self._value_row(session, namespace.id, key, lock=True)
        expected = {
            "candidate_digest": pointer.candidate_digest,
            "project_id": pointer.project_id,
            "decision_kind": pointer.decision_kind.value,
        }
        if row is None:
            self._set_record(
                session,
                namespace,
                key,
                pointer.project_id,
                pointer.decision_kind,
                expected,
            )
            return
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload != expected:
            raise SharedGenerationStoreError(
                "shared candidate scope is invalid"
            )

    def _candidate_scope(
        self,
        candidate_digest: str,
        *,
        require_artifact: bool = True,
    ) -> tuple[str, DecisionKind]:
        self._validate_digest(candidate_digest)
        try:
            with self._sessions() as session:
                namespaces = tuple(
                    session.scalars(
                        select(VariableNamespaceModel).where(
                            VariableNamespaceModel.namespace
                            == self._candidate_namespace_name(candidate_digest)
                        )
                    ).all()
                )
                if len(namespaces) > 1:
                    raise SharedGenerationStoreError(
                        "shared candidate scope is ambiguous"
                    )
                if namespaces:
                    namespace = namespaces[0]
                    if namespace.project_id is None:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    row = self._value_row(
                        session,
                        namespace.id,
                        "scope",
                        lock=False,
                    )
                    if row is None:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is orphaned"
                        )
                    unsigned = self._decode_record_unscoped(
                        row.value,
                        expected_key="scope",
                    )
                    payload = unsigned["payload"]
                    project_id = unsigned["project_id"]
                    raw_kind = unsigned["decision_kind"]
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(project_id, str)
                        or project_id != namespace.project_id
                        or not isinstance(raw_kind, str)
                    ):
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    try:
                        decision_kind = DecisionKind(raw_kind)
                    except ValueError:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        ) from None
                    expected = {
                        "candidate_digest": candidate_digest,
                        "project_id": project_id,
                        "decision_kind": decision_kind.value,
                    }
                    if payload != expected:
                        raise SharedGenerationStoreError(
                            "shared candidate scope is invalid"
                        )
                    if require_artifact:
                        bundle = self._verified_bundle(candidate_digest)
                        if (
                            bundle.project_id != project_id
                            or bundle.decision_kind is not decision_kind
                        ):
                            raise SharedGenerationStoreError(
                                "shared candidate artifact scope is invalid"
                            )
                    return project_id, decision_kind
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None
        if not require_artifact:
            raise SharedGenerationStoreError(
                "shared candidate scope is unavailable"
            )
        bundle = self._verified_bundle(candidate_digest)
        return bundle.project_id, bundle.decision_kind

    def _candidate_flag(self, candidate_digest: str, key: str) -> bool:
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
                    return False
                row = self._value_row(session, namespace.id, key, lock=False)
                if row is None:
                    return False
                payload = self._decode_record(
                    row.value,
                    key=key,
                    project_id=project_id,
                    decision_kind=decision_kind,
                )
                if payload.get("candidate_digest") != candidate_digest:
                    raise SharedGenerationStoreError(
                        "shared candidate flag is invalid"
                    )
                if key == "hold" and (
                    not isinstance(payload.get("reason"), str)
                    or _SAFE_IDENTIFIER.fullmatch(cast(str, payload["reason"]))
                    is None
                ):
                    raise SharedGenerationStoreError(
                        "shared candidate flag is invalid"
                    )
                return True
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state read failed"
            ) from None

    def _candidate_flag_in_session(
        self,
        session: Session,
        pointer: GenerationPointer,
        key: str,
    ) -> bool:
        namespace = self._candidate_namespace(
            session,
            pointer.candidate_digest,
            project_id=pointer.project_id,
            create=False,
            lock=True,
        )
        if namespace is None:
            raise SharedGenerationStoreError(
                "shared candidate scope is unavailable"
            )
        row = self._value_row(session, namespace.id, key, lock=True)
        if row is None:
            return False
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload.get("candidate_digest") != pointer.candidate_digest:
            raise SharedGenerationStoreError("shared candidate flag is invalid")
        return True

    def _read_count(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> int:
        row = self._value_row(session, namespace.id, "use-count", lock=lock)
        if row is None:
            return 0
        payload = self._decode_record(
            row.value,
            key="use-count",
            project_id=project_id,
            decision_kind=decision_kind,
        )
        count = payload.get("count")
        if type(count) is not int or not 0 <= count <= _MAX_APPLICATIONS:
            raise SharedGenerationStoreError(
                "shared generation use count is invalid"
            )
        return count

    def _write_count(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        count: int,
    ) -> None:
        self._set_record(
            session,
            namespace,
            "use-count",
            project_id,
            decision_kind,
            {"count": count},
        )
