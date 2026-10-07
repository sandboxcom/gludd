"""Tests for bounded, content-free decision-codification telemetry."""

from __future__ import annotations

from general_ludd.decision_codification.telemetry import DecisionCodificationTelemetry


class _Backend:
    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.calls: list[tuple[str, str, float, dict[str, str]]] = []

    def counter_inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: int = 1,
    ) -> None:
        if self.failing:
            raise RuntimeError("metrics unavailable")
        self.calls.append(("counter", name, float(value), labels or {}))

    def gauge_set(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        if self.failing:
            raise RuntimeError("metrics unavailable")
        self.calls.append(("gauge", name, value, labels or {}))

    def histogram_observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        if self.failing:
            raise RuntimeError("metrics unavailable")
        self.calls.append(("histogram", name, value, labels or {}))


def test_all_metrics_use_closed_labels_and_expected_names() -> None:
    backend = _Backend()
    telemetry = DecisionCodificationTelemetry(backend)

    assert telemetry.envelope("review", "accepted")
    assert telemetry.candidate("review", "approved", "eligible")
    assert telemetry.lookup("review", "codified", "active")
    assert telemetry.fallback("review", "scope_miss")
    assert telemetry.application("review", "success", "active")
    assert telemetry.drift("review", "failure_rate")
    assert telemetry.active_rules("review", "active", 2)
    assert telemetry.mining_seconds("success", 1.25)
    assert telemetry.estimated_calls_avoided("review", 3)

    assert [call[1] for call in backend.calls] == [
        "gludd_decision_codification_envelopes_total",
        "gludd_decision_codification_candidates_total",
        "gludd_decision_codification_lookup_total",
        "gludd_decision_codification_fallback_total",
        "gludd_decision_codification_applications_total",
        "gludd_decision_codification_drift_total",
        "gludd_decision_codification_active_rules",
        "gludd_decision_codification_mining_seconds",
        "gludd_decision_codification_estimated_calls_avoided_total",
    ]


def test_untrusted_labels_never_escape_or_create_cardinality() -> None:
    backend = _Backend()
    telemetry = DecisionCodificationTelemetry(backend)
    secret = "project-123/token=secret"

    telemetry.lookup(secret, secret, secret)
    telemetry.candidate(secret, secret, secret)

    serialized = repr(backend.calls)
    assert secret not in serialized
    assert serialized.count("unknown") >= 6


def test_backend_outage_is_no_throw_bounded_and_flushable() -> None:
    backend = _Backend(failing=True)
    telemetry = DecisionCodificationTelemetry(backend, buffer_limit=2)

    assert not telemetry.fallback("review", "scope_miss")
    assert not telemetry.fallback("policy", "expired")
    assert not telemetry.fallback("budget", "revoked")
    assert telemetry.buffered_count == 2

    backend.failing = False
    assert telemetry.flush() == 2
    assert telemetry.buffered_count == 0
    assert len(backend.calls) == 2


def test_invalid_values_are_ignored_without_reaching_backend() -> None:
    backend = _Backend()
    telemetry = DecisionCodificationTelemetry(backend)

    assert not telemetry.active_rules("review", "active", -1)
    assert not telemetry.mining_seconds("success", float("nan"))
    assert not telemetry.estimated_calls_avoided("review", 0)
    assert backend.calls == []
