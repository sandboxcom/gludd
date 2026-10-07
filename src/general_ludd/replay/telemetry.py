"""Bounded, content-free telemetry primitives for replay operations."""

from __future__ import annotations

import math
import threading
from typing import Final, Protocol


class ReplayMetricsBackend(Protocol):
    """Structural subset implemented by the maintained metrics exporter."""

    def counter_inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: int = 1,
    ) -> None:
        """Increment a counter."""
        ...

    def gauge_set(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge."""
        ...

    def histogram_observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Observe a histogram value."""
        ...


_UNKNOWN: Final[str] = "unknown"
_SCHEMA_CLASSES: Final[dict[str, str]] = {
    "legacy-v0": "legacy-v0",
    "v0": "legacy-v0",
    "gludd.run-bundle/v1": "v1",
    "gludd.run-event/v1": "v1",
    "v1": "v1",
}
_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "run.started",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "prompt.rendered",
        "model.requested",
        "model.responded",
        "model.failed",
        "tool.requested",
        "tool.responded",
        "tool.failed",
        "workspace.snapshot",
        "workspace.diff",
        "gate.started",
        "gate.completed",
        "review.decided",
        "policy.decided",
        "budget.decided",
        "reconcile.decided",
        "recording.degraded",
    }
)
_STATUSES: Final[frozenset[str]] = frozenset(
    {"running", "completed", "failed", "cancelled", "incomplete"}
)
_RECORD_FAILURE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "validation",
        "redaction",
        "serialization",
        "storage",
        "concurrency",
        "integrity",
        "timeout",
        "unavailable",
        "disabled",
        "internal",
    }
)
_REDACTION_KINDS: Final[frozenset[str]] = frozenset(
    {
        "credential_text",
        "credential_url",
        "hidden_reasoning",
        "secret_key",
        "unsupported_key",
        "unsupported_type",
    }
)
_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"event", "prompt", "response", "tool", "workspace", "attachment", "metadata"}
)
_VERIFY_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"valid", "invalid", "unsupported", "error"}
)
_VERIFY_REASONS: Final[frozenset[str]] = frozenset(
    {
        "ok",
        "schema",
        "manifest",
        "sequence",
        "missing",
        "extra",
        "digest",
        "signature",
        "key",
        "corrupt",
        "storage",
    }
)
_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"record", "finalize", "list", "show", "verify", "export", "simulate", "reexecute", "retention"}
)
_OPERATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"success", "failure", "denied", "disabled", "unsupported"}
)
_RETENTION_REASONS: Final[frozenset[str]] = frozenset(
    {"expired", "quota", "operator", "invalid", "unreadable"}
)


def _closed_label(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else _UNKNOWN


def _schema_label(value: object) -> str:
    return _SCHEMA_CLASSES.get(value, _UNKNOWN) if type(value) is str else _UNKNOWN


def _positive_count(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _nonnegative_int(value: object) -> float | None:
    return float(value) if type(value) is int and value >= 0 else None


def _nonnegative_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class ReplayTelemetry:
    """Emit replay metrics through a safe optional in-process backend.

    Callers can pass :class:`MetricsExporter` from the observability package.
    Every label is selected from a fixed class set; arbitrary values are never
    stringified or forwarded. Backend absence and backend failures are silent,
    deterministic no-ops so replay work never depends on telemetry health.
    """

    def __init__(self, backend: ReplayMetricsBackend | None = None) -> None:
        """Initialize with an optional maintained metrics-compatible backend."""
        self._backend = backend
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Return whether an emission backend is configured."""
        return self._backend is not None

    def _counter(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        *,
        count: int = 1,
    ) -> bool:
        safe_count = _positive_count(count)
        backend = self._backend
        if safe_count is None or backend is None:
            return False
        try:
            with self._lock:
                backend.counter_inc(name, labels=labels, value=safe_count)
        except Exception:
            return False
        return True

    def _histogram(
        self,
        name: str,
        value: int | float,
        labels: dict[str, str],
    ) -> bool:
        safe_value = _nonnegative_finite(value)
        backend = self._backend
        if safe_value is None or backend is None:
            return False
        try:
            with self._lock:
                backend.histogram_observe(name, safe_value, labels=labels)
        except Exception:
            return False
        return True

    def _gauge(self, name: str, value: int) -> bool:
        safe_value = _nonnegative_int(value)
        backend = self._backend
        if safe_value is None or backend is None:
            return False
        try:
            with self._lock:
                backend.gauge_set(name, safe_value)
        except Exception:
            return False
        return True

    def events_recorded(
        self,
        schema: object,
        event_type: object,
        *,
        count: int = 1,
    ) -> bool:
        """Increment the recorded-event counter with closed schema/type labels."""
        return self._counter(
            "gludd_replay_events_recorded_total",
            {
                "schema": _schema_label(schema),
                "event_type": _closed_label(event_type, _EVENT_TYPES),
            },
            count=count,
        )

    def record_failure(self, reason: object, *, count: int = 1) -> bool:
        """Increment recorder failures using a bounded reason class."""
        return self._counter(
            "gludd_replay_record_failures_total",
            {"reason": _closed_label(reason, _RECORD_FAILURE_REASONS)},
            count=count,
        )

    def bundle_finalized(
        self,
        schema: object,
        status: object,
        *,
        count: int = 1,
    ) -> bool:
        """Increment finalized bundles using bounded schema/status labels."""
        return self._counter(
            "gludd_replay_bundles_finalized_total",
            {
                "schema": _schema_label(schema),
                "status": _closed_label(status, _STATUSES),
            },
            count=count,
        )

    def bundle_bytes(self, schema: object, size_bytes: int) -> bool:
        """Observe a non-negative bundle size under a closed schema label."""
        return self._histogram(
            "gludd_replay_bundle_bytes",
            size_bytes,
            {"schema": _schema_label(schema)},
        )

    def record_seconds(self, event_type: object, seconds: int | float) -> bool:
        """Observe recorder latency under a closed event-type label."""
        return self._histogram(
            "gludd_replay_record_seconds",
            seconds,
            {"event_type": _closed_label(event_type, _EVENT_TYPES)},
        )

    def redactions(self, kind: object, *, count: int = 1) -> bool:
        """Increment redactions using only canonical content-free kinds."""
        return self._counter(
            "gludd_replay_redactions_total",
            {"kind": _closed_label(kind, _REDACTION_KINDS)},
            count=count,
        )

    def truncations(self, content_type: object, *, count: int = 1) -> bool:
        """Increment truncations using a bounded content category."""
        return self._counter(
            "gludd_replay_truncations_total",
            {"content_type": _closed_label(content_type, _CONTENT_TYPES)},
            count=count,
        )

    def verification(
        self,
        schema: object,
        outcome: object,
        reason: object,
        *,
        count: int = 1,
    ) -> bool:
        """Increment verification outcomes with three closed label classes."""
        return self._counter(
            "gludd_replay_verify_total",
            {
                "schema": _schema_label(schema),
                "outcome": _closed_label(outcome, _VERIFY_OUTCOMES),
                "reason": _closed_label(reason, _VERIFY_REASONS),
            },
            count=count,
        )

    def operation(
        self,
        operation: object,
        outcome: object,
        *,
        count: int = 1,
    ) -> bool:
        """Increment replay operations with bounded operation/outcome labels."""
        return self._counter(
            "gludd_replay_operations_total",
            {
                "operation": _closed_label(operation, _OPERATIONS),
                "outcome": _closed_label(outcome, _OPERATION_OUTCOMES),
            },
            count=count,
        )

    def legacy_read(self, *, count: int = 1) -> bool:
        """Increment the unlabeled legacy-read counter."""
        return self._counter("gludd_replay_legacy_reads_total", count=count)

    def retention_deleted(self, reason: object, *, count: int = 1) -> bool:
        """Increment retention deletions using a bounded reason class."""
        return self._counter(
            "gludd_replay_retention_deleted_total",
            {"reason": _closed_label(reason, _RETENTION_REASONS)},
            count=count,
        )

    def store_bytes(self, size_bytes: int) -> bool:
        """Set the current non-negative replay-store byte gauge."""
        return self._gauge("gludd_replay_store_bytes", size_bytes)

    def incomplete_bundles(self, count: int) -> bool:
        """Set the current non-negative incomplete-bundle gauge."""
        return self._gauge("gludd_replay_incomplete_bundles", count)


__all__ = ["ReplayMetricsBackend", "ReplayTelemetry"]
