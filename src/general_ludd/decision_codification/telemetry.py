"""Closed-cardinality, content-free decision-codification telemetry."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Final, Literal

from general_ludd.replay.telemetry import ReplayMetricsBackend

_UNKNOWN: Final[str] = "unknown"
_KINDS: Final[frozenset[str]] = frozenset(
    {"review", "policy", "budget", "routing", "reconcile"}
)
_ENVELOPE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"accepted", "refused", "error"}
)
_CANDIDATE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"proposed", "rejected", "approved", "error"}
)
_CANDIDATE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "eligible",
        "insufficient_support",
        "insufficient_confidence",
        "nondeterministic",
        "evaluation_failed",
        "unsafe",
        "integrity",
    }
)
_LOOKUP_RESULTS: Final[frozenset[str]] = frozenset(
    {"codified", "abstained", "error"}
)
_STAGES: Final[frozenset[str]] = frozenset(
    {"none", "shadow", "canary", "canary_10", "canary_50", "active"}
)
_FALLBACK_REASONS: Final[frozenset[str]] = frozenset(
    {
        "no_active_rule",
        "scope_miss",
        "normalization_refused",
        "no_leaf",
        "multiple_leaves",
        "policy_changed",
        "expired",
        "revoked",
        "canary_excluded",
        "drift_hold",
        "integrity_failure",
        "runtime_error",
    }
)
_APPLICATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"success", "failure", "reverted", "unknown", "unsafe"}
)
_DRIFT_REASONS: Final[frozenset[str]] = frozenset(
    {
        "safety_violation",
        "integrity_error",
        "multiple_leaves",
        "policy_changed",
        "failure_rate",
        "shadow_disagreement",
        "unseen_feature",
        "schema_changed",
        "vocabulary_changed",
        "expiry",
        "use_limit",
        "corpus_revoked",
        "retention_hold",
    }
)
_MINING_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"success", "failure", "refused", "cancelled"}
)
MetricKind = Literal["counter", "gauge", "histogram"]


def _closed(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else _UNKNOWN


@dataclass(frozen=True, slots=True)
class _Emission:
    kind: MetricKind
    name: str
    value: float
    labels: dict[str, str]


class DecisionCodificationTelemetry:
    """Emit safe metrics and retain a bounded content-free outage buffer."""

    def __init__(
        self,
        backend: ReplayMetricsBackend | None = None,
        *,
        buffer_limit: int = 256,
    ) -> None:
        """Create a no-throw emitter with a bounded outage buffer."""
        if buffer_limit < 1 or buffer_limit > 10_000:
            raise ValueError("telemetry buffer_limit must be between 1 and 10000")
        self._backend = backend
        self._buffer: deque[_Emission] = deque(maxlen=buffer_limit)
        self._lock = threading.RLock()

    @property
    def buffered_count(self) -> int:
        """Return the number of retained content-free emissions."""
        with self._lock:
            return len(self._buffer)

    def envelope(self, kind: object, outcome: object, *, count: int = 1) -> bool:
        """Count normalized envelope outcomes."""
        return self._counter(
            "gludd_decision_codification_envelopes_total",
            {"kind": _closed(kind, _KINDS), "outcome": _closed(outcome, _ENVELOPE_OUTCOMES)},
            count,
        )

    def candidate(
        self, kind: object, outcome: object, reason: object, *, count: int = 1
    ) -> bool:
        """Count bounded candidate lifecycle outcomes."""
        return self._counter(
            "gludd_decision_codification_candidates_total",
            {
                "kind": _closed(kind, _KINDS),
                "outcome": _closed(outcome, _CANDIDATE_OUTCOMES),
                "reason": _closed(reason, _CANDIDATE_REASONS),
            },
            count,
        )

    def lookup(
        self, kind: object, result: object, stage: object, *, count: int = 1
    ) -> bool:
        """Count deterministic lookup results."""
        return self._counter(
            "gludd_decision_codification_lookup_total",
            {
                "kind": _closed(kind, _KINDS),
                "result": _closed(result, _LOOKUP_RESULTS),
                "stage": _closed(stage, _STAGES),
            },
            count,
        )

    def fallback(self, kind: object, reason: object, *, count: int = 1) -> bool:
        """Count typed fallback reasons."""
        return self._counter(
            "gludd_decision_codification_fallback_total",
            {"kind": _closed(kind, _KINDS), "reason": _closed(reason, _FALLBACK_REASONS)},
            count,
        )

    def application(
        self, kind: object, outcome: object, stage: object, *, count: int = 1
    ) -> bool:
        """Count terminal deterministic application outcomes."""
        return self._counter(
            "gludd_decision_codification_applications_total",
            {
                "kind": _closed(kind, _KINDS),
                "outcome": _closed(outcome, _APPLICATION_OUTCOMES),
                "stage": _closed(stage, _STAGES),
            },
            count,
        )

    def drift(self, kind: object, reason: object, *, count: int = 1) -> bool:
        """Count bounded drift-hold reasons."""
        return self._counter(
            "gludd_decision_codification_drift_total",
            {"kind": _closed(kind, _KINDS), "reason": _closed(reason, _DRIFT_REASONS)},
            count,
        )

    def active_rules(self, kind: object, stage: object, value: int) -> bool:
        """Set active-rule gauge for a closed scope class."""
        if type(value) is not int or value < 0:
            return False
        return self._emit(_Emission(
            "gauge",
            "gludd_decision_codification_active_rules",
            float(value),
            {"kind": _closed(kind, _KINDS), "stage": _closed(stage, _STAGES)},
        ))

    def mining_seconds(self, outcome: object, seconds: int | float) -> bool:
        """Observe finite, non-negative offline mining duration."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return False
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            return False
        return self._emit(_Emission(
            "histogram",
            "gludd_decision_codification_mining_seconds",
            value,
            {"outcome": _closed(outcome, _MINING_OUTCOMES)},
        ))

    def estimated_calls_avoided(
        self, kind: object, count: int = 1
    ) -> bool:
        """Increment the explicitly estimated avoided-call counter."""
        return self._counter(
            "gludd_decision_codification_estimated_calls_avoided_total",
            {"kind": _closed(kind, _KINDS)},
            count,
        )

    def flush(self) -> int:
        """Retry buffered emissions in FIFO order, stopping at first outage."""
        sent = 0
        with self._lock:
            pending = tuple(self._buffer)
            self._buffer.clear()
            for index, emission in enumerate(pending):
                if self._send(emission):
                    sent += 1
                    continue
                self._buffer.extend(pending[index:])
                break
        return sent

    def _counter(self, name: str, labels: dict[str, str], count: int) -> bool:
        if type(count) is not int or count <= 0:
            return False
        return self._emit(_Emission("counter", name, float(count), labels))

    def _emit(self, emission: _Emission) -> bool:
        with self._lock:
            if self._send(emission):
                return True
            self._buffer.append(emission)
            return False

    def _send(self, emission: _Emission) -> bool:
        backend = self._backend
        if backend is None:
            return False
        try:
            if emission.kind == "counter":
                backend.counter_inc(
                    emission.name,
                    labels=emission.labels,
                    value=int(emission.value),
                )
            elif emission.kind == "gauge":
                backend.gauge_set(
                    emission.name, emission.value, labels=emission.labels
                )
            else:
                backend.histogram_observe(
                    emission.name, emission.value, labels=emission.labels
                )
        except Exception:
            return False
        return True


__all__ = ["DecisionCodificationTelemetry"]
