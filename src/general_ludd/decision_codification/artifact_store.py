"""Create-only, HMAC-authenticated storage for decision artifacts.

The adapter intentionally narrows :class:`IntegrityStore` to keyed operation and
adds immutable names plus receipt-chain validation.  A create claim is written
with ``O_EXCL`` before the existing store performs its atomic payload writes, so
concurrent writers can never replace an accepted artifact.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionRuleBundleV1,
    EvaluationReportV1,
    ReceiptType,
    canonical_decision_json,
)
from general_ludd.integrity.store import IntegrityError, IntegrityStore

ArtifactKind = Literal["rule", "report", "receipt"]
_MODEL = TypeVar("_MODEL", bound=BaseModel)
_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:([0-9a-f]{64})$")
_PROJECT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_RECORD_SCHEMA: Final[str] = "gludd.decision-authenticated-artifact/v1"
_DOMAIN: Final[bytes] = b"general_ludd.decision_codification.artifacts.v1\x00"
_GENERATION_STATE_PURPOSE: Final[str] = "shared-generation-state/v1"
_MAX_RECEIPT_CHAIN: Final[int] = 256


class ArtifactStoreError(RuntimeError):
    """Base failure for the fail-closed artifact adapter."""


class ArtifactAlreadyExists(ArtifactStoreError):
    """Raised when a create-only artifact name has already been claimed."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when authenticated storage is unavailable or invalid."""


class ReceiptChainError(ArtifactStoreError):
    """Raised when an append would break immutable lifecycle history."""


class DecisionArtifactStore:
    """Typed create-only facade over the repository's canonical HMAC store."""

    def __init__(self, base_dir: str, *, key: bytes | None) -> None:
        """Create a keyed immutable artifact store under ``base_dir``."""
        if not key:
            raise ArtifactIntegrityError("a non-empty HMAC key is required")
        self._base = Path(base_dir)
        self._integrity = IntegrityStore(base_dir=base_dir, key=key, domain=_DOMAIN)

    def identity_hmac(self, identity: str, project_id: str) -> str:
        """Return a domain-scoped pseudonym without persisting the identity."""
        if not identity or not project_id:
            raise ArtifactIntegrityError("identity HMAC inputs must be non-empty")
        digest = self._integrity.sign({
            "purpose": "approver-identity/v1",
            "project_id": project_id,
            "identity": identity,
        })
        return f"hmac-sha256:{digest}"

    def decision_observability_hmac(
        self,
        project_id: str,
        policy_digest: str,
        receipt_digest: str,
    ) -> str:
        """Authenticate one exact-scope observability receipt digest."""
        payload = self._observability_hmac_payload(
            project_id,
            policy_digest,
            receipt_digest,
        )
        return f"hmac-sha256:{self._integrity.sign(payload)}"

    def verify_decision_observability_hmac(
        self,
        project_id: str,
        policy_digest: str,
        receipt_digest: str,
        authentication_tag: str,
    ) -> None:
        """Verify one status-receipt HMAC without exposing key material."""
        if (
            not isinstance(authentication_tag, str)
            or not authentication_tag.startswith("hmac-sha256:")
        ):
            raise ArtifactIntegrityError(
                "observability receipt authentication tag is invalid"
            )
        payload = self._observability_hmac_payload(
            project_id,
            policy_digest,
            receipt_digest,
        )
        try:
            self._integrity.verify(
                payload,
                authentication_tag.removeprefix("hmac-sha256:"),
            )
        except IntegrityError:
            raise ArtifactIntegrityError(
                "observability receipt authentication tag mismatched"
            ) from None

    def decision_generation_state_hmac(
        self,
        state: Mapping[str, object],
    ) -> str:
        """Authenticate one bounded shared-generation state record."""
        payload = self._generation_state_hmac_payload(state)
        return f"hmac-sha256:{self._integrity.sign(payload)}"

    def verify_decision_generation_state_hmac(
        self,
        state: Mapping[str, object],
        authentication_tag: str,
    ) -> None:
        """Verify a shared-generation record through the artifact trust root."""
        if (
            not isinstance(authentication_tag, str)
            or not authentication_tag.startswith("hmac-sha256:")
        ):
            raise ArtifactIntegrityError(
                "generation state authentication tag is invalid"
            )
        try:
            self._integrity.verify(
                self._generation_state_hmac_payload(state),
                authentication_tag.removeprefix("hmac-sha256:"),
            )
        except (IntegrityError, TypeError, ValueError):
            raise ArtifactIntegrityError(
                "generation state authentication tag mismatched"
            ) from None

    def create_rule_bundle(self, bundle: DecisionRuleBundleV1) -> None:
        """Persist one immutable rule bundle under its canonical digest."""
        validated = self._revalidate(bundle, DecisionRuleBundleV1)
        self._create("rule", validated.candidate_digest, validated)

    def read_rule_bundle(self, candidate_digest: str) -> DecisionRuleBundleV1:
        """Load and revalidate one authenticated rule bundle."""
        return self._read_model(
            "rule", candidate_digest, DecisionRuleBundleV1
        )

    def create_evaluation_report(self, report: EvaluationReportV1) -> None:
        """Persist one immutable offline evaluation report."""
        validated = self._revalidate(report, EvaluationReportV1)
        self._create("report", validated.report_digest, validated)

    def read_evaluation_report(self, report_digest: str) -> EvaluationReportV1:
        """Load and revalidate one authenticated evaluation report."""
        return self._read_model("report", report_digest, EvaluationReportV1)

    def append_receipt(self, receipt: ApprovalReceiptV1) -> None:
        """Append one receipt after verifying its declared predecessor."""
        previous: ApprovalReceiptV1 | None = None
        if receipt.previous_receipt_digest is not None:
            try:
                previous = self.read_receipt(receipt.previous_receipt_digest)
            except ArtifactStoreError as exc:
                raise ReceiptChainError("previous receipt is unavailable") from exc
            self._validate_link(previous, receipt)
        elif receipt.receipt_type is not ReceiptType.APPROVAL:
            raise ReceiptChainError("only an approval receipt may begin a chain")

        validated = self._revalidate(receipt, ApprovalReceiptV1)
        self._create("receipt", validated.receipt_digest, validated)

    def read_receipt(self, receipt_digest: str) -> ApprovalReceiptV1:
        """Load and revalidate one authenticated lifecycle receipt."""
        return self._read_model("receipt", receipt_digest, ApprovalReceiptV1)

    def verify_receipt_chain(
        self, head_receipt_digest: str
    ) -> tuple[ApprovalReceiptV1, ...]:
        """Return an oldest-first verified chain or fail closed."""
        newest_first: list[ApprovalReceiptV1] = []
        seen: set[str] = set()
        digest: str | None = head_receipt_digest
        while digest is not None:
            if digest in seen:
                raise ReceiptChainError("receipt chain contains a cycle")
            if len(newest_first) >= _MAX_RECEIPT_CHAIN:
                raise ReceiptChainError("receipt chain exceeds the bounded limit")
            seen.add(digest)
            try:
                current = self.read_receipt(digest)
            except ArtifactStoreError as exc:
                raise ReceiptChainError("receipt chain is incomplete") from exc
            newest_first.append(current)
            digest = current.previous_receipt_digest

        chain = tuple(reversed(newest_first))
        if not chain or chain[0].receipt_type is not ReceiptType.APPROVAL:
            raise ReceiptChainError("receipt chain must begin with approval")
        for previous, current in pairwise(chain):
            if current.previous_receipt_digest != previous.receipt_digest:
                raise ReceiptChainError("receipt chain link does not match")
            self._validate_link(previous, current)
        return chain

    @staticmethod
    def _revalidate(value: _MODEL, model: type[_MODEL]) -> _MODEL:
        try:
            return model.model_validate(value.model_dump(mode="python", by_alias=True))
        except ValidationError as exc:
            raise ArtifactIntegrityError("artifact failed canonical validation") from exc

    @staticmethod
    def _validate_link(
        previous: ApprovalReceiptV1, current: ApprovalReceiptV1
    ) -> None:
        if current.created_at < previous.created_at:
            raise ReceiptChainError("receipt time precedes its previous receipt")
        if current.receipt_type is ReceiptType.RENEWAL:
            return
        binding_fields = (
            "candidate_digest",
            "corpus_digest",
            "evaluator_report_digest",
            "feature_schema",
            "policy_digest",
            "source_code_digest",
            "dependency_lock_digest",
            "training_recipe_digest",
            "project_id",
            "decision_kind",
            "risk_class",
            "rollout_plan",
            "maximum_use_count",
        )
        if any(
            getattr(previous, field) != getattr(current, field)
            for field in binding_fields
        ):
            raise ReceiptChainError("lifecycle receipt changes an immutable binding")

    def _create(self, kind: ArtifactKind, identifier: str, model: BaseModel) -> None:
        name = self._record_name(kind, identifier)
        claim = self._base / f"{name}.create-only"
        payload_path = self._base / f"{name}.json"
        mac_path = self._base / f"{name}.json.mac"
        if payload_path.exists() or mac_path.exists():
            raise ArtifactAlreadyExists(f"{kind} artifact already exists")
        try:
            descriptor = os.open(
                claim,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
        except FileExistsError as exc:
            raise ArtifactAlreadyExists(f"{kind} artifact already exists") from exc
        except OSError as exc:
            raise ArtifactIntegrityError("create-only claim could not be persisted") from exc

        payload = model.model_dump(mode="json", by_alias=True)
        unsigned: dict[str, object] = {
            "schema": _RECORD_SCHEMA,
            "kind": kind,
            "identifier": identifier,
            "payload": payload,
        }
        record = {
            **unsigned,
            "authentication_tag": f"hmac-sha256:{self._integrity.sign(unsigned)}",
        }
        try:
            self._integrity.save(name, record)
        except (IntegrityError, OSError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("authenticated artifact write failed") from exc

    def _read_model(
        self,
        kind: ArtifactKind,
        identifier: str,
        model: type[_MODEL],
    ) -> _MODEL:
        payload = self._read(kind, identifier)
        try:
            return model.model_validate_json(canonical_decision_json(payload))
        except ValidationError as exc:
            raise ArtifactIntegrityError("stored artifact schema is invalid") from exc

    def _read(self, kind: ArtifactKind, identifier: str) -> Mapping[str, object]:
        name = self._record_name(kind, identifier)
        try:
            loaded = self._integrity.load(name)
        except (IntegrityError, OSError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("authenticated artifact read failed") from exc
        if not isinstance(loaded, dict):
            raise ArtifactIntegrityError("authenticated artifact is not an object")
        expected_keys = {
            "schema",
            "kind",
            "identifier",
            "payload",
            "authentication_tag",
        }
        if set(loaded) != expected_keys:
            raise ArtifactIntegrityError("authenticated artifact fields are invalid")
        tag = loaded.get("authentication_tag")
        if not isinstance(tag, str) or not tag.startswith("hmac-sha256:"):
            raise ArtifactIntegrityError("authenticated artifact tag is invalid")
        unsigned = {
            "schema": loaded.get("schema"),
            "kind": loaded.get("kind"),
            "identifier": loaded.get("identifier"),
            "payload": loaded.get("payload"),
        }
        if (
            unsigned["schema"] != _RECORD_SCHEMA
            or unsigned["kind"] != kind
            or unsigned["identifier"] != identifier
        ):
            raise ArtifactIntegrityError("authenticated artifact scope is invalid")
        try:
            self._integrity.verify(unsigned, tag.removeprefix("hmac-sha256:"))
        except IntegrityError as exc:
            raise ArtifactIntegrityError("artifact authentication tag mismatched") from exc
        payload = loaded.get("payload")
        if not isinstance(payload, dict):
            raise ArtifactIntegrityError("authenticated artifact payload is invalid")
        return cast(Mapping[str, object], payload)

    @staticmethod
    def _record_name(kind: ArtifactKind, identifier: str) -> str:
        matched = _DIGEST_PATTERN.fullmatch(identifier)
        if matched is None:
            raise ArtifactIntegrityError("artifact identifier is not a SHA-256 digest")
        return f"decision-{kind}-{matched.group(1)}"

    @staticmethod
    def _observability_hmac_payload(
        project_id: str,
        policy_digest: str,
        receipt_digest: str,
    ) -> dict[str, str]:
        if (
            not isinstance(project_id, str)
            or _PROJECT_PATTERN.fullmatch(project_id) is None
            or not isinstance(policy_digest, str)
            or _DIGEST_PATTERN.fullmatch(policy_digest) is None
            or not isinstance(receipt_digest, str)
            or _DIGEST_PATTERN.fullmatch(receipt_digest) is None
        ):
            raise ArtifactIntegrityError(
                "observability receipt authentication scope is invalid"
            )
        return {
            "purpose": "decision-observability-status/v1",
            "project_id": project_id,
            "policy_digest": policy_digest,
            "receipt_digest": receipt_digest,
        }

    @staticmethod
    def _generation_state_hmac_payload(
        state: Mapping[str, object],
    ) -> dict[str, object]:
        if not isinstance(state, dict):
            raise ArtifactIntegrityError("generation state must be an object")
        return {
            "purpose": _GENERATION_STATE_PURPOSE,
            "state": state,
        }


__all__ = [
    "ArtifactAlreadyExists",
    "ArtifactIntegrityError",
    "ArtifactStoreError",
    "DecisionArtifactStore",
    "ReceiptChainError",
]
