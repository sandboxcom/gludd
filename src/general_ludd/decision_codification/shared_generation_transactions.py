"""Fence shared-generation database transactions and namespaces."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import timedelta
from secrets import token_hex
from typing import Any, cast

from sqlalchemy import delete, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    BucketLeaseModel,
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
    _SAFE_IDENTIFIER,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_codec import _PostgresGenerationCodec


class _PostgresGenerationTransactions(_PostgresGenerationCodec):
    """Fence shared-generation database transactions and namespaces."""

    @contextlib.contextmanager
    def _mutation(
        self,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> Iterator[Session]:
        self._validate_scope(project_id, decision_kind)
        session = self._sessions()
        try:
            with session.begin():
                self._set_transaction_timeout(session)
                holder_id = f"decision-generation-{token_hex(16)}"
                self._acquire_lease(session, project_id, holder_id)
                yield session
                self._release_lease(session, project_id, holder_id)
        except SharedGenerationStoreError:
            raise
        except SQLAlchemyError:
            raise SharedGenerationStoreError(
                "shared generation state write failed"
            ) from None
        finally:
            session.close()

    def _set_transaction_timeout(self, session: Session) -> None:
        if self._engine.dialect.name != "postgresql":
            return
        timeout = f"{int(self._lock_timeout * 1_000)}ms"
        session.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": timeout},
        )
        session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": timeout},
        )

    def _acquire_lease(
        self,
        session: Session,
        project_id: str,
        holder_id: str,
    ) -> None:
        key = self.coordination_key(project_id)
        now = self._now()
        existing = session.scalar(
            select(BucketLeaseModel)
            .where(BucketLeaseModel.bucket_key == key)
            .with_for_update()
        )
        if existing is not None:
            if existing.expires_at > now:
                raise SharedGenerationStoreError(
                    "shared generation coordination is unavailable"
                )
            session.delete(existing)
            session.flush()
        session.add(
            BucketLeaseModel(
                bucket_key=key,
                project_id=project_id,
                holder_id=holder_id,
                expires_at=now + timedelta(seconds=self._lease_ttl),
                heartbeat_at=now,
                updated_at=now,
            )
        )
        session.flush()

    def _release_lease(
        self,
        session: Session,
        project_id: str,
        holder_id: str,
    ) -> None:
        result = session.execute(
            delete(BucketLeaseModel).where(
                BucketLeaseModel.bucket_key == self.coordination_key(project_id),
                BucketLeaseModel.holder_id == holder_id,
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) != 1:
            raise SharedGenerationStoreError(
                "shared generation coordination is unavailable"
            )

    def _head_namespace(
        self,
        session: Session,
        project_id: str,
        *,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        return self._namespace(
            session,
            name=_HEAD_NAMESPACE,
            project_id=project_id,
            description="Authenticated shared decision generation state",
            create=create,
            lock=lock,
        )

    def _candidate_namespace(
        self,
        session: Session,
        candidate_digest: str,
        *,
        project_id: str,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        return self._namespace(
            session,
            name=self._candidate_namespace_name(candidate_digest),
            project_id=project_id,
            description="Authenticated shared decision candidate state",
            create=create,
            lock=lock,
        )

    @staticmethod
    def _namespace(
        session: Session,
        *,
        name: str,
        project_id: str,
        description: str,
        create: bool,
        lock: bool,
    ) -> VariableNamespaceModel | None:
        statement = select(VariableNamespaceModel).where(
            VariableNamespaceModel.namespace == name,
            VariableNamespaceModel.project_id == project_id,
        )
        if lock:
            statement = statement.with_for_update()
        namespace = session.scalar(statement)
        if namespace is None and create:
            namespace = VariableNamespaceModel(
                namespace=name,
                project_id=project_id,
                description=description,
            )
            session.add(namespace)
            session.flush()
        return namespace

    def _read_head(
        self,
        session: Session,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        namespace: VariableNamespaceModel | None = None,
        lock: bool = False,
    ) -> tuple[GenerationPointer | None, str | None]:
        selected_namespace = namespace or self._head_namespace(
            session,
            project_id,
            create=False,
            lock=lock,
        )
        if selected_namespace is None:
            return None, None
        key = self._head_key(decision_kind)
        row = self._value_row(session, selected_namespace.id, key, lock=lock)
        if row is None:
            return None, None
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        reason = payload.get("inactive_reason")
        if reason is not None and (
            not isinstance(reason, str)
            or _SAFE_IDENTIFIER.fullmatch(reason) is None
        ):
            raise SharedGenerationStoreError("shared generation head is invalid")
        pointer_payload = payload.get("pointer")
        if pointer_payload is None:
            if set(payload) != {"pointer", "inactive_reason"}:
                raise SharedGenerationStoreError(
                    "shared generation head is invalid"
                )
            return None, reason
        if reason is not None or not isinstance(pointer_payload, dict):
            raise SharedGenerationStoreError("shared generation head is invalid")
        pointer = self._pointer_from_payload(pointer_payload)
        if (
            pointer.project_id != project_id
            or pointer.decision_kind is not decision_kind
        ):
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )
        return pointer, None

    def _write_head(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        pointer: GenerationPointer | None,
        *,
        decision_kind: DecisionKind,
        inactive_reason: str | None,
    ) -> None:
        if pointer is not None and pointer.decision_kind is not decision_kind:
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )
        project_id = namespace.project_id
        if project_id is None:
            raise SharedGenerationStoreError(
                "shared generation namespace scope is invalid"
            )
        self._set_record(
            session,
            namespace,
            self._head_key(decision_kind),
            project_id,
            decision_kind,
            {
                "pointer": (
                    None if pointer is None else self._pointer_payload(pointer)
                ),
                "inactive_reason": inactive_reason,
            },
        )
