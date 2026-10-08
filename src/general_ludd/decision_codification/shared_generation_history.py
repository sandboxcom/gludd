"""Persist authenticated generation heads and bounded history."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
)
from general_ludd.decision_codification.schema import (
    DecisionKind,
)
from general_ludd.decision_codification.shared_generation_base import (
    _MAX_HISTORY,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_transactions import _PostgresGenerationTransactions


class _PostgresGenerationHistory(_PostgresGenerationTransactions):
    """Persist authenticated generation heads and bounded history."""

    def _next_epoch(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> int:
        key = self._epoch_key(decision_kind)
        row = self._value_row(session, namespace.id, key, lock=True)
        current = 0
        if row is not None:
            payload = self._decode_record(
                row.value,
                key=key,
                project_id=project_id,
                decision_kind=decision_kind,
            )
            raw_current = payload.get("epoch", -1)
            if type(raw_current) is not int or raw_current < 0:
                raise SharedGenerationStoreError(
                    "shared generation epoch is invalid"
                )
            current = raw_current
        updated = current + 1
        self._set_record(
            session,
            namespace,
            key,
            project_id,
            decision_kind,
            {"epoch": updated},
        )
        return updated

    def _require_epoch(
        self,
        session: Session,
        pointer: GenerationPointer,
    ) -> None:
        namespace = self._head_namespace(
            session,
            pointer.project_id,
            create=False,
            lock=False,
        )
        if namespace is None:
            raise SharedGenerationStoreError("shared generation epoch is missing")
        key = self._epoch_key(pointer.decision_kind)
        row = self._value_row(session, namespace.id, key, lock=False)
        if row is None:
            raise SharedGenerationStoreError("shared generation epoch is missing")
        payload = self._decode_record(
            row.value,
            key=key,
            project_id=pointer.project_id,
            decision_kind=pointer.decision_kind,
        )
        if payload.get("epoch") != pointer.epoch:
            raise SharedGenerationStoreError(
                "shared generation identity is stale"
            )

    def _write_history(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        pointer: GenerationPointer,
    ) -> None:
        history = self._read_history(
            session,
            namespace,
            pointer.project_id,
            pointer.decision_kind,
            lock=True,
        )
        if len(history) >= _MAX_HISTORY:
            oldest = history[-1]
            session.execute(
                delete(VariableValueModel).where(
                    VariableValueModel.namespace_id == namespace.id,
                    VariableValueModel.key
                    == self._history_key(pointer.decision_kind, oldest.epoch),
                )
            )
        self._set_record(
            session,
            namespace,
            self._history_key(pointer.decision_kind, pointer.epoch),
            pointer.project_id,
            pointer.decision_kind,
            self._pointer_payload(pointer),
        )

    def _read_history(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> tuple[GenerationPointer, ...]:
        prefix = f"history:{decision_kind.value}:"
        statement = (
            select(VariableValueModel)
            .where(
                VariableValueModel.namespace_id == namespace.id,
                VariableValueModel.key.like(f"{prefix}%"),
            )
            .order_by(VariableValueModel.key.desc())
            .limit(_MAX_HISTORY + 1)
        )
        if lock:
            statement = statement.with_for_update()
        rows = tuple(session.scalars(statement).all())
        if len(rows) > _MAX_HISTORY:
            raise SharedGenerationStoreError(
                "shared generation history bound exceeded"
            )
        pointers: list[GenerationPointer] = []
        for row in rows:
            payload = self._decode_record(
                row.value,
                key=row.key,
                project_id=project_id,
                decision_kind=decision_kind,
            )
            pointer = self._pointer_from_payload(payload)
            if row.key != self._history_key(decision_kind, pointer.epoch):
                raise SharedGenerationStoreError(
                    "shared generation history is invalid"
                )
            self._verify_pointer(pointer)
            pointers.append(pointer)
        return tuple(pointers)

    @staticmethod
    def _delete_consumed_history(
        session: Session,
        namespace: VariableNamespaceModel,
        decision_kind: DecisionKind,
        selected_epoch: int,
    ) -> None:
        prefix = f"history:{decision_kind.value}:"
        session.execute(
            delete(VariableValueModel).where(
                VariableValueModel.namespace_id == namespace.id,
                VariableValueModel.key.like(f"{prefix}%"),
                VariableValueModel.key >= f"{prefix}{selected_epoch:020d}",
            )
        )
