"""Authenticate and validate bounded shared-generation records."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from general_ludd.db.models import (
    VariableNamespaceModel,
    VariableValueModel,
)
from general_ludd.decision_codification.artifact_store import (
    ArtifactStoreError,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    artifacts_have_exact_bindings,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionKind,
    DecisionRuleBundleV1,
    RolloutStage,
    canonical_decision_json,
)
from general_ludd.decision_codification.shared_generation_base import (
    _CANDIDATE_NAMESPACE_PREFIX,
    _MAX_RECORD_BYTES,
    _SAFE_IDENTIFIER,
    _SHA256,
    _STATE_SCHEMA,
    SharedGenerationStoreError,
    _PostgresGenerationState,
)


class _PostgresGenerationCodec(_PostgresGenerationState):
    """Authenticate and validate bounded shared-generation records."""

    def _verified_bundle(self, candidate_digest: str) -> DecisionRuleBundleV1:
        try:
            return self._artifacts.read_rule_bundle(candidate_digest)
        except ArtifactStoreError:
            raise SharedGenerationStoreError(
                "shared generation artifact is unavailable or invalid"
            ) from None

    def _verify_pointer(self, pointer: GenerationPointer) -> None:
        self._validate_pointer(pointer)
        try:
            bundle = self._artifacts.read_rule_bundle(pointer.candidate_digest)
            chain = self._artifacts.verify_receipt_chain(pointer.receipt_digest)
            receipt = chain[-1]
        except (ArtifactStoreError, IndexError):
            raise SharedGenerationStoreError(
                "shared generation artifact is unavailable or invalid"
            ) from None
        self._validate_artifact_bindings(bundle, receipt)
        if (
            bundle.project_id != pointer.project_id
            or bundle.decision_kind is not pointer.decision_kind
            or receipt.receipt_digest != pointer.receipt_digest
            or receipt.lifecycle_state.value != pointer.stage.value
        ):
            raise SharedGenerationStoreError(
                "shared generation artifact scope is invalid"
            )

    @staticmethod
    def _validate_artifact_bindings(
        bundle: DecisionRuleBundleV1,
        receipt: ApprovalReceiptV1,
    ) -> None:
        if not artifacts_have_exact_bindings(bundle, receipt):
            raise SharedGenerationStoreError(
                "shared generation artifact binding is invalid"
            )

    def _set_record(
        self,
        session: Session,
        namespace: VariableNamespaceModel,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        payload: Mapping[str, object],
    ) -> None:
        value = self._encode_record(
            key=key,
            project_id=project_id,
            decision_kind=decision_kind,
            payload=payload,
        )
        row = self._value_row(session, namespace.id, key, lock=True)
        if row is None:
            session.add(
                VariableValueModel(
                    namespace_id=namespace.id,
                    key=key,
                    value=value,
                    value_type="decision_generation_v1",
                )
            )
        else:
            row.value = value
            row.value_type = "decision_generation_v1"
        session.flush()

    def _encode_record(
        self,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
        payload: Mapping[str, object],
    ) -> str:
        unsigned: dict[str, object] = {
            "schema": _STATE_SCHEMA,
            "key": key,
            "project_id": project_id,
            "decision_kind": decision_kind.value,
            "payload": dict(payload),
        }
        authentication_tag = self._artifacts.decision_generation_state_hmac(
            unsigned
        )
        encoded = canonical_decision_json(
            {**unsigned, "authentication_tag": authentication_tag}
        )
        if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise SharedGenerationStoreError(
                "shared generation record exceeds the bounded size"
            )
        return encoded

    def _decode_record(
        self,
        value: str,
        *,
        key: str,
        project_id: str,
        decision_kind: DecisionKind,
    ) -> dict[str, object]:
        unsigned = self._decode_record_unscoped(value, expected_key=key)
        stored_project = unsigned["project_id"]
        stored_kind = unsigned["decision_kind"]
        if (
            not isinstance(stored_project, str)
            or not hmac.compare_digest(stored_project, project_id)
            or stored_kind != decision_kind.value
        ):
            raise SharedGenerationStoreError(
                "shared generation record scope is invalid"
            )
        payload = unsigned["payload"]
        if not isinstance(payload, dict):
            raise SharedGenerationStoreError(
                "shared generation record payload is invalid"
            )
        return cast(dict[str, object], payload)

    def _decode_record_unscoped(
        self,
        value: str,
        *,
        expected_key: str,
    ) -> dict[str, object]:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            )
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            ) from None
        if not isinstance(loaded, dict) or set(loaded) != {
            "schema",
            "key",
            "project_id",
            "decision_kind",
            "payload",
            "authentication_tag",
        }:
            raise SharedGenerationStoreError(
                "shared generation record is invalid"
            )
        if loaded.get("schema") != _STATE_SCHEMA or loaded.get("key") != expected_key:
            raise SharedGenerationStoreError(
                "shared generation record identity is invalid"
            )
        authentication_tag = loaded.pop("authentication_tag")
        try:
            self._artifacts.verify_decision_generation_state_hmac(
                loaded,
                cast(str, authentication_tag),
            )
        except ArtifactStoreError:
            raise SharedGenerationStoreError(
                "shared generation record authentication is invalid"
            ) from None
        return cast(dict[str, object], loaded)

    @staticmethod
    def _value_row(
        session: Session,
        namespace_id: int,
        key: str,
        *,
        lock: bool,
    ) -> VariableValueModel | None:
        statement = select(VariableValueModel).where(
            VariableValueModel.namespace_id == namespace_id,
            VariableValueModel.key == key,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @classmethod
    def _pointer_from_payload(cls, payload: Mapping[str, object]) -> GenerationPointer:
        if set(payload) != {
            "project_id",
            "decision_kind",
            "candidate_digest",
            "receipt_digest",
            "stage",
            "epoch",
        }:
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            )
        try:
            pointer = GenerationPointer(
                project_id=cast(str, payload["project_id"]),
                decision_kind=DecisionKind(cast(str, payload["decision_kind"])),
                candidate_digest=cast(str, payload["candidate_digest"]),
                receipt_digest=cast(str, payload["receipt_digest"]),
                stage=RolloutStage(cast(str, payload["stage"])),
                epoch=cast(int, payload["epoch"]),
            )
            cls._validate_pointer(pointer)
            return pointer
        except (KeyError, TypeError, ValueError):
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            ) from None

    @staticmethod
    def _pointer_payload(pointer: GenerationPointer) -> dict[str, object]:
        return {
            "project_id": pointer.project_id,
            "decision_kind": pointer.decision_kind.value,
            "candidate_digest": pointer.candidate_digest,
            "receipt_digest": pointer.receipt_digest,
            "stage": pointer.stage.value,
            "epoch": pointer.epoch,
        }

    @classmethod
    def _validate_pointer(cls, pointer: GenerationPointer) -> None:
        if not isinstance(pointer, GenerationPointer):
            raise SharedGenerationStoreError(
                "shared generation pointer is invalid"
            )
        cls._validate_scope(pointer.project_id, pointer.decision_kind)
        cls._validate_digest(pointer.candidate_digest)
        cls._validate_digest(pointer.receipt_digest)
        if not isinstance(pointer.stage, RolloutStage):
            raise SharedGenerationStoreError(
                "shared generation pointer stage is invalid"
            )
        if type(pointer.epoch) is not int or pointer.epoch < 0:
            raise SharedGenerationStoreError(
                "shared generation pointer epoch is invalid"
            )

    @staticmethod
    def _validate_scope(project_id: str, decision_kind: DecisionKind) -> None:
        if not isinstance(project_id, str) or _SAFE_IDENTIFIER.fullmatch(project_id) is None:
            raise SharedGenerationStoreError(
                "shared generation project scope is invalid"
            )
        if not isinstance(decision_kind, DecisionKind):
            raise SharedGenerationStoreError(
                "shared generation decision scope is invalid"
            )

    @staticmethod
    def _validate_digest(value: object) -> None:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise SharedGenerationStoreError(
                "shared generation digest is invalid"
            )

    def _now(self) -> datetime:
        return self._utc(self._clock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise SharedGenerationStoreError(
                "shared generation timestamp is invalid"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _head_key(decision_kind: DecisionKind) -> str:
        return f"head:{decision_kind.value}"

    @staticmethod
    def _epoch_key(decision_kind: DecisionKind) -> str:
        return f"epoch:{decision_kind.value}"

    @staticmethod
    def _history_key(decision_kind: DecisionKind, epoch: int) -> str:
        return f"history:{decision_kind.value}:{epoch:020d}"

    @staticmethod
    def _candidate_namespace_name(candidate_digest: str) -> str:
        return _CANDIDATE_NAMESPACE_PREFIX + candidate_digest.removeprefix(
            "sha256:"
        )

    @staticmethod
    def _application_key(application_id: str) -> str:
        return "application:" + application_id.removeprefix("sha256:")

    @staticmethod
    def _outcome_key(application_id: str) -> str:
        return "outcome:" + application_id.removeprefix("sha256:")
