"""Bounded and content-free replay telemetry contract tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from prometheus_client import CollectorRegistry

from general_ludd.observability.metrics_exporter import MetricsExporter
from general_ludd.replay.telemetry import ReplayTelemetry


def _exporter() -> MetricsExporter:
    return MetricsExporter(registry=CollectorRegistry(auto_describe=False))


def _samples(exporter: MetricsExporter) -> list[dict[str, Any]]:
    report = exporter.get_json()
    return [sample for family in report["metrics"].values() for sample in family]


def _sample_value(
    exporter: MetricsExporter,
    name: str,
    labels: dict[str, str],
) -> float:
    matches = [
        sample["value"]
        for sample in _samples(exporter)
        if sample["name"] == name and sample["labels"] == labels
    ]
    assert matches == [matches[0]]
    return float(matches[0])


def test_spec_operations_use_the_maintained_metrics_exporter() -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)

    assert telemetry.events_recorded("gludd.run-bundle/v1", "run.started")
    assert telemetry.record_failure("storage")
    assert telemetry.bundle_finalized("v1", "completed")
    assert telemetry.bundle_bytes("v1", 512)
    assert telemetry.record_seconds("run.completed", 0.25)
    assert telemetry.redactions("secret_key", count=2)
    assert telemetry.truncations("attachment")
    assert telemetry.verification("legacy-v0", "invalid", "digest")
    assert telemetry.operation("verify", "failure")
    assert telemetry.legacy_read()
    assert telemetry.retention_deleted("expired")
    assert telemetry.store_bytes(1_024)
    assert telemetry.incomplete_bundles(3)

    names = {sample["name"] for sample in _samples(exporter)}
    assert {
        "gludd_replay_events_recorded_total",
        "gludd_replay_record_failures_total",
        "gludd_replay_bundles_finalized_total",
        "gludd_replay_bundle_bytes_count",
        "gludd_replay_record_seconds_count",
        "gludd_replay_redactions_total",
        "gludd_replay_truncations_total",
        "gludd_replay_verify_total",
        "gludd_replay_operations_total",
        "gludd_replay_legacy_reads_total",
        "gludd_replay_retention_deleted_total",
        "gludd_replay_store_bytes",
        "gludd_replay_incomplete_bundles",
    } <= names
    assert _sample_value(
        exporter,
        "gludd_replay_events_recorded_total",
        {"event_type": "run.started", "schema": "v1"},
    ) == 1.0


def test_identifiers_payloads_and_secrets_can_never_become_labels() -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)
    run_id = "run-018f-secret"
    todo_id = "todo-private-123"
    user_id = "user-private-456"
    project_id = "project-private-789"
    payload = "prompt text with customer data"
    secret = "Bearer very-secret-token"

    assert telemetry.events_recorded(run_id, payload)
    assert telemetry.record_failure(todo_id)
    assert telemetry.bundle_finalized(project_id, user_id)
    assert telemetry.redactions(secret)
    assert telemetry.truncations(payload)
    assert telemetry.verification(user_id, secret, project_id)
    assert telemetry.operation(todo_id, run_id)
    assert telemetry.retention_deleted(secret)

    rendered = exporter.render_prometheus()
    for forbidden in (run_id, todo_id, user_id, project_id, payload, secret):
        assert forbidden not in rendered
    assert 'event_type="unknown"' in rendered
    assert 'schema="unknown"' in rendered
    assert 'reason="unknown"' in rendered


def test_unbounded_input_collapses_to_one_closed_label_series() -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)

    for index in range(200):
        assert telemetry.events_recorded(
            f"project-{index}",
            f"payload-{index}",
        )

    total_samples = [
        sample
        for sample in _samples(exporter)
        if sample["name"] == "gludd_replay_events_recorded_total"
    ]
    assert len(total_samples) == 1
    assert total_samples[0]["labels"] == {
        "event_type": "unknown",
        "schema": "unknown",
    }
    assert total_samples[0]["value"] == 200.0


def test_concurrent_updates_are_exact_and_deterministic() -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)

    def record_batch() -> list[bool]:
        return [telemetry.events_recorded("v1", "tool.responded") for _ in range(100)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: record_batch(), range(8)))

    assert all(all(batch) for batch in results)
    assert _sample_value(
        exporter,
        "gludd_replay_events_recorded_total",
        {"event_type": "tool.responded", "schema": "v1"},
    ) == 800.0


class _FailingBackend:
    def counter_inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: int = 1,
    ) -> None:
        raise RuntimeError("backend-secret")

    def gauge_set(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        raise RuntimeError("backend-secret")

    def histogram_observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        raise RuntimeError("backend-secret")


def test_disabled_and_failed_backends_are_deterministic_no_ops() -> None:
    disabled = ReplayTelemetry()
    failed = ReplayTelemetry(_FailingBackend())

    disabled_results = (
        disabled.events_recorded("v1", "run.started"),
        disabled.bundle_bytes("v1", 1),
        disabled.store_bytes(1),
    )
    failed_results = (
        failed.events_recorded("v1", "run.started"),
        failed.bundle_bytes("v1", 1),
        failed.store_bytes(1),
    )

    assert disabled.enabled is False
    assert failed.enabled is True
    assert disabled_results == (False, False, False)
    assert failed_results == (False, False, False)


def test_invalid_numeric_observations_are_rejected_without_metrics() -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)

    assert telemetry.events_recorded("v1", "run.started", count=0) is False
    assert telemetry.bundle_bytes("v1", -1) is False
    assert telemetry.record_seconds("run.started", float("nan")) is False
    assert telemetry.store_bytes(-1) is False
    assert telemetry.incomplete_bundles(-1) is False
    assert not any(
        sample["name"].startswith("gludd_replay_") for sample in _samples(exporter)
    )
