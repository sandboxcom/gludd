"""Bounded producer capture for signed, reusable agent-decision evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import ConfigDict, TypeAdapter, ValidationError

from general_ludd.decision_codification.capture_support import (
    bounded_private_identifier,
    correlation_digest,
    receipt_for_existing_bundle,
)
from general_ludd.decision_codification.normalize import normalize_decision_context
from general_ludd.decision_codification.schema import (
    DECISION_ACTIONS_V1,
    DecisionContextV1,
    DecisionKind,
    VerifiedOutcome,
    canonical_sha256,
)
from general_ludd.replay.schema import (
    ModelIdentityV1,
    RuntimeIdentityV1,
    SourceIdentityV1,
)
from general_ludd.replay.store import ReplayStoreError, RunBundleStore, VerifiedBundle
from general_ludd.schemas.execution_identity import BoundedIdentifier, Sha256Digest

MAX_CAPTURE_IDENTIFIER_BYTES: Final[int] = 1_024
MAX_CAPTURE_BUNDLE_BYTES: Final[int] = 128 * 1_024
_CAPTURE_LOCK_ID: Final[str] = "decision-capture-retention"
_EVENT_TYPES: Final[dict[DecisionKind, str]] = {
    DecisionKind.REVIEW: "review.decided",
    DecisionKind.POLICY: "policy.decided",
    DecisionKind.BUDGET: "budget.decided",
    DecisionKind.RECONCILE: "reconcile.decided",
}
_IDENTIFIER = TypeAdapter(BoundedIdentifier, config=ConfigDict(strict=True))
_DIGEST = TypeAdapter(Sha256Digest, config=ConfigDict(strict=True))


class DecisionCaptureError(RuntimeError):
    """Reject unsafe, ambiguous, conflicting, or unavailable capture."""


@dataclass(frozen=True, slots=True)
class DecisionCaptureReceipt:
    """Content-free receipt for one finalized signed evidence pair."""

    run_id: str
    decision_event_digest: str
    outcome_event_digest: str
    events_digest: str


class DecisionOutcomeRecorder:
    """Write one decision/outcome pair to an immutable signed replay bundle."""

    def __init__(
        self,
        store: RunBundleStore,
        *,
        project_id: str,
        policy_digest: str,
        correlation_key: bytes,
        source: SourceIdentityV1,
        runtime: RuntimeIdentityV1,
        model: ModelIdentityV1,
        retention_days: int,
        max_total_bytes: int,
        scan_limit: int,
    ) -> None:
        """Bind exact provenance, secret correlation, and hard storage bounds."""
        if not isinstance(store, RunBundleStore):
            raise DecisionCaptureError("decision capture requires a replay store")
        try:
            self._project_id = _IDENTIFIER.validate_python(project_id, strict=True)
            self._policy_digest = _DIGEST.validate_python(policy_digest, strict=True)
        except ValidationError as exc:
            raise DecisionCaptureError("decision capture scope is invalid") from exc
        if not isinstance(correlation_key, bytes) or len(correlation_key) < 16:
            raise DecisionCaptureError("decision capture correlation key is unavailable")
        if not isinstance(source, SourceIdentityV1):
            raise DecisionCaptureError("decision capture source identity is invalid")
        if not isinstance(runtime, RuntimeIdentityV1):
            raise DecisionCaptureError("decision capture runtime identity is invalid")
        if not isinstance(model, ModelIdentityV1) or model.request_parameters:
            raise DecisionCaptureError("decision capture model identity is invalid")
        if type(retention_days) is not int or not 1 <= retention_days <= 366:
            raise DecisionCaptureError("decision capture retention is invalid")
        if (
            type(max_total_bytes) is not int
            or not MAX_CAPTURE_BUNDLE_BYTES <= max_total_bytes <= 10 * 1024**3
        ):
            raise DecisionCaptureError("decision capture storage bound is invalid")
        if type(scan_limit) is not int or not 1 <= scan_limit <= 10_000:
            raise DecisionCaptureError("decision capture scan bound is invalid")

        self._store = store
        self._correlation_key = correlation_key
        self._source = source
        self._runtime = runtime
        self._model = model
        self._retention_days = retention_days
        self._max_total_bytes = max_total_bytes
        self._scan_limit = scan_limit

    @staticmethod
    def _bounded_private_identifier(value: object) -> str:
        return bounded_private_identifier(
            value,
            max_bytes=MAX_CAPTURE_IDENTIFIER_BYTES,
            error_type=DecisionCaptureError,
        )

    def _correlation_digest(self, label: str, *values: str) -> str:
        return correlation_digest(
            correlation_key=self._correlation_key,
            project_id=self._project_id,
            policy_digest=self._policy_digest,
            label=label,
            values=values,
        )

    def coordination_key(
        self,
        *,
        capture_id: str,
        root_task_id: str,
        decision_kind: DecisionKind,
    ) -> str:
        """Return one opaque cross-host lease key for a private capture identity."""
        private_capture_id = self._bounded_private_identifier(capture_id)
        private_root_task_id = self._bounded_private_identifier(root_task_id)
        if not isinstance(decision_kind, DecisionKind) or decision_kind not in _EVENT_TYPES:
            raise DecisionCaptureError("decision capture kind is unsupported")
        capture_digest = self._correlation_digest(
            "capture",
            decision_kind.value,
            private_capture_id,
            private_root_task_id,
        )
        return f"decision-capture:{capture_digest}"

    @staticmethod
    def _utc(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise DecisionCaptureError("decision capture time must be timezone-aware")
        return value.astimezone(UTC)

    def capture(
        self,
        *,
        capture_id: str,
        root_task_id: str,
        decision_kind: DecisionKind,
        features: object,
        decision: str,
        outcome: VerifiedOutcome,
        occurred_at: datetime,
    ) -> DecisionCaptureReceipt:
        """Validate, persist, sign, and return one idempotent evidence receipt."""
        private_capture_id = self._bounded_private_identifier(capture_id)
        private_root_task_id = self._bounded_private_identifier(root_task_id)
        if not isinstance(decision_kind, DecisionKind) or decision_kind not in _EVENT_TYPES:
            raise DecisionCaptureError("decision capture kind is unsupported")
        if type(decision) is not str or decision not in DECISION_ACTIONS_V1[decision_kind]:
            raise DecisionCaptureError("decision capture action is invalid")
        if not isinstance(outcome, VerifiedOutcome) or outcome is VerifiedOutcome.UNKNOWN:
            raise DecisionCaptureError("decision capture outcome is not terminal")
        event_time = self._utc(occurred_at)
        normalized = normalize_decision_context(
            project_id=self._project_id,
            expected_project_id=self._project_id,
            decision_kind=decision_kind,
            policy_digest=self._policy_digest,
            features=features,
        )
        if not isinstance(normalized, DecisionContextV1):
            raise DecisionCaptureError("decision capture context is not eligible")
        if type(features) is not dict:
            raise DecisionCaptureError("decision capture context is invalid")

        capture_digest = self.coordination_key(
            capture_id=private_capture_id,
            root_task_id=private_root_task_id,
            decision_kind=decision_kind,
        ).removeprefix("decision-capture:")
        task_digest = self._correlation_digest("task", private_root_task_id)
        run_id = f"decision-{decision_kind.value}-{capture_digest}"
        correlation: dict[str, object] = {
            "todo_id": None,
            "task_id": f"task-{task_digest}",
            "trace_id": f"trace-{capture_digest}",
        }
        decision_payload: dict[str, object] = {
            "policy_digest": self._policy_digest,
            "features": dict(features),
            "decision": decision,
        }
        terminal_event_id = f"terminal-{capture_digest}"
        status_digest = canonical_sha256(
            {
                "schema": "gludd.decision-capture-status/v1",
                "capture": capture_digest,
                "decision_kind": decision_kind.value,
                "decision": decision,
                "outcome": outcome.value,
            }
        )

        try:
            with self._store.run_lock(_CAPTURE_LOCK_ID):
                existing = self._existing_bundle(run_id)
                if existing is not None:
                    return receipt_for_existing_bundle(
                        existing,
                        project_id=self._project_id,
                        event_type=_EVENT_TYPES[decision_kind],
                        correlation=correlation,
                        decision_payload=decision_payload,
                        outcome=outcome,
                        terminal_event_id=terminal_event_id,
                        status_digest=status_digest,
                        error_type=DecisionCaptureError,
                        receipt_factory=DecisionCaptureReceipt,
                    )
                retention = self._store.enforce_retention(
                    now=event_time,
                    max_total_bytes=self._max_total_bytes - MAX_CAPTURE_BUNDLE_BYTES,
                    max_deletions=min(self._scan_limit, 100),
                    scan_limit=self._scan_limit,
                )
                if not retention.quota_satisfied:
                    raise DecisionCaptureError("decision capture storage quota is unavailable")
                return self._write_bundle(
                    run_id=run_id,
                    event_type=_EVENT_TYPES[decision_kind],
                    correlation=correlation,
                    decision_payload=decision_payload,
                    outcome=outcome,
                    terminal_event_id=terminal_event_id,
                    status_digest=status_digest,
                    occurred_at=event_time,
                )
        except DecisionCaptureError:
            raise
        except Exception as exc:
            raise DecisionCaptureError("decision capture failed closed") from exc

    def _existing_bundle(self, run_id: str) -> VerifiedBundle | None:
        path = self._store.bundle_path(run_id)
        if not path.exists():
            return None
        try:
            return self._store.read_verified(run_id)
        except ReplayStoreError as exc:
            raise DecisionCaptureError(
                "decision capture identity belongs to incomplete evidence"
            ) from exc

    def _write_bundle(
        self,
        *,
        run_id: str,
        event_type: str,
        correlation: dict[str, object],
        decision_payload: dict[str, object],
        outcome: VerifiedOutcome,
        terminal_event_id: str,
        status_digest: str,
        occurred_at: datetime,
    ) -> DecisionCaptureReceipt:
        recorded_at = max(datetime.now(UTC), occurred_at)
        timestamp = occurred_at.isoformat().replace("+00:00", "Z")
        observed = recorded_at.isoformat().replace("+00:00", "Z")
        decision_event = self._store.append_event(
            run_id,
            {
                "event_id": f"decision-{run_id.removeprefix('decision-')}",
                "occurred_at": timestamp,
                "recorded_at": observed,
                "type": event_type,
                "project_id": self._project_id,
                "correlation": correlation,
                "payload": decision_payload,
                "redaction": {"count": 0, "kinds": []},
            },
        )
        outcome_event = self._store.append_event(
            run_id,
            {
                "event_id": f"outcome-{run_id.removeprefix('decision-')}",
                "occurred_at": timestamp,
                "recorded_at": observed,
                "type": "decision.outcome",
                "project_id": self._project_id,
                "correlation": correlation,
                "payload": {
                    "decision_event_digest": decision_event.digest,
                    "verified_outcome": outcome.value,
                    "terminal_event_ids": [terminal_event_id],
                    "gate_digests": [],
                    "status_digests": [status_digest],
                },
                "redaction": {"count": 0, "kinds": []},
            },
        )
        manifest = self._store.finalize(
            run_id,
            {
                "schema": "gludd.run-bundle/v1",
                "run_id": run_id,
                "parent_run_id": None,
                "operation": "record",
                "created_at": timestamp,
                "finalized_at": observed,
                "status": "completed",
                "project_id": self._project_id,
                "source": self._source.model_dump(mode="json"),
                "runtime": self._runtime.model_dump(mode="json"),
                "model": self._model.model_dump(mode="json"),
                "event_count": 0,
                "events_sha256": "sha256:" + "0" * 64,
                "attachments": [],
                "completeness": {
                    "expected_stages": [event_type, "decision.outcome"],
                    "observed_stages": [event_type, "decision.outcome"],
                    "recorder_errors": [],
                    "missing_ranges": [],
                },
                "retention": {
                    "expires_at": (
                        occurred_at + timedelta(days=self._retention_days)
                    ).isoformat().replace("+00:00", "Z"),
                    "pinned": False,
                    "hold_reason": None,
                },
                "integrity": "unsigned",
                "signing_key_id": None,
            },
        )
        return DecisionCaptureReceipt(
            run_id=run_id,
            decision_event_digest=decision_event.digest,
            outcome_event_digest=outcome_event.digest,
            events_digest=manifest.events_sha256,
        )


__all__ = [
    "MAX_CAPTURE_BUNDLE_BYTES",
    "MAX_CAPTURE_IDENTIFIER_BYTES",
    "DecisionCaptureError",
    "DecisionCaptureReceipt",
    "DecisionOutcomeRecorder",
]
