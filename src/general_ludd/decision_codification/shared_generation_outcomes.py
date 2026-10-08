"""Persist and verify candidate application outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    VariableNamespaceModel,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
)
from general_ludd.decision_codification.schema import (
    DecisionApplicationOutcomeV1,
    DecisionKind,
    canonical_decision_json,
)
from general_ludd.decision_codification.shared_generation_base import (
    _MAX_RECENT_OUTCOMES,
    SharedGenerationStoreError,
)
from general_ludd.decision_codification.shared_generation_candidates import _PostgresGenerationCandidates


class _PostgresGenerationOutcomes(_PostgresGenerationCandidates):
    """Persist and verify candidate application outcomes."""

    def _require_application(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        candidate_digest: str,
        application_id: str,
    ) -> None:
        payload = self._decode_record(
            value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        if payload != {
            "candidate_digest": candidate_digest,
            "application_id": application_id,
        }:
            raise SharedGenerationStoreError(
                "shared generation application is invalid"
            )

    def _append_outcome_index(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        outcome: DecisionApplicationOutcomeV1,
    ) -> None:
        existing = list(
            self._read_outcome_index(
                session,
                namespace,
                project_id,
                decision_kind,
                lock=True,
            )
        )
        existing.append((outcome.application_id, outcome.occurred_at))
        deduplicated = {
            application_id: occurred_at
            for application_id, occurred_at in existing
        }
        bounded = sorted(
            deduplicated.items(),
            key=lambda item: (item[1], item[0]),
        )[-_MAX_RECENT_OUTCOMES:]
        self._set_record(
            session,
            namespace,
            "outcome-index",
            project_id,
            decision_kind,
            {
                "items": [
                    {
                        "application_id": application_id,
                        "occurred_at": occurred_at.isoformat(),
                    }
                    for application_id, occurred_at in bounded
                ]
            },
        )

    def _read_outcome_index(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        project_id: str,
        decision_kind: DecisionKind,
        *,
        lock: bool,
    ) -> tuple[tuple[str, datetime], ...]:
        row = self._value_row(
            session,
            namespace.id,
            "outcome-index",
            lock=lock,
        )
        if row is None:
            return ()
        payload = self._decode_record(
            row.value,
            key="outcome-index",
            project_id=project_id,
            decision_kind=decision_kind,
        )
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > _MAX_RECENT_OUTCOMES:
            raise SharedGenerationStoreError(
                "shared outcome index is invalid"
            )
        parsed: list[tuple[str, datetime]] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                "application_id",
                "occurred_at",
            }:
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                )
            application_id = item.get("application_id")
            occurred = item.get("occurred_at")
            self._validate_digest(application_id)
            if not isinstance(occurred, str):
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                )
            try:
                occurred_at = self._utc(datetime.fromisoformat(occurred))
            except ValueError:
                raise SharedGenerationStoreError(
                    "shared outcome index is invalid"
                ) from None
            parsed.append((cast(str, application_id), occurred_at))
        if parsed != sorted(parsed, key=lambda item: (item[1], item[0])) or len(
            {item[0] for item in parsed}
        ) != len(parsed):
            raise SharedGenerationStoreError(
                "shared outcome index is invalid"
            )
        return tuple(parsed)

    def _decode_outcome(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> DecisionApplicationOutcomeV1:
        payload = self._decode_record(
            value,
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
        )
        try:
            return DecisionApplicationOutcomeV1.model_validate_json(
                canonical_decision_json(payload),
                strict=True,
            )
        except ValidationError:
            raise SharedGenerationStoreError(
                "shared generation application outcome is invalid"
            ) from None

    def _verify_shared_pointer(self, pointer: GenerationPointer) -> None:
        self._verify_pointer(pointer)
        project_id, decision_kind = self._candidate_scope(
            pointer.candidate_digest
        )
        if (
            project_id != pointer.project_id
            or decision_kind is not pointer.decision_kind
        ):
            raise SharedGenerationStoreError(
                "shared generation pointer scope is invalid"
            )
