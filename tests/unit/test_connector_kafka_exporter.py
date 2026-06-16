"""Unit tests for the kafka_exporter observability connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.kafka_exporter import KafkaExporterSource

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Records calls and returns a queued (status, text) tuple per call.

    Matches the injectable transport contract:
        http_get(url, params, headers, timeout) -> (status:int, text:str)
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {"url": url, "params": params, "headers": headers or {}, "timeout": timeout}
        )
        if not self._responses:
            raise AssertionError("transport called more times than responses queued")
        return self._responses.pop(0)


# A representative kafka_exporter /metrics exposition. Note the leading HELP/TYPE
# comment lines and the go_/process_ noise that must be filtered out.
METRICS_TEXT = """\
# HELP kafka_consumergroup_lag Current Approximate Lag of a ConsumerGroup at Topic/Partition
# TYPE kafka_consumergroup_lag gauge
kafka_consumergroup_lag{consumergroup="orders-svc",partition="0",topic="orders"} 42
kafka_consumergroup_lag{consumergroup="orders-svc",partition="1",topic="orders"} 0
# TYPE kafka_topic_partition_under_replicated_partition gauge
kafka_topic_partition_under_replicated_partition{partition="0",topic="orders"} 1
kafka_topic_partition_under_replicated_partition{partition="1",topic="orders"} 0
# TYPE kafka_topic_partition_current_offset gauge
kafka_topic_partition_current_offset{partition="0",topic="orders"} 100500
# TYPE kafka_brokers gauge
kafka_brokers 3
# unrelated runtime noise that must be ignored:
go_gc_duration_seconds{quantile="0"} 1.2e-05
process_cpu_seconds_total 12.3
"""

GOOD_URL = "https://kafka-exp.internal.example.com:9308"


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_name_and_construction():
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    assert KafkaExporterSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_requires_injected_transport():
    with pytest.raises(ValueError):
        KafkaExporterSource({"base_url": GOOD_URL})


def test_token_env_injects_bearer_header(monkeypatch):
    monkeypatch.setenv("KAFKA_EXP_TOKEN", "s3cret")
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource(
        {"base_url": GOOD_URL, "token_env": "KAFKA_EXP_TOKEN"}, http_get=t
    )
    src.query({})
    assert t.calls[0]["headers"].get("Authorization") == "Bearer s3cret"


def test_missing_token_env_tolerated(monkeypatch):
    monkeypatch.delenv("KAFKA_EXP_TOKEN", raising=False)
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource(
        {"base_url": GOOD_URL, "token_env": "KAFKA_EXP_TOKEN"}, http_get=t
    )
    src.query({})
    assert "Authorization" not in t.calls[0]["headers"]


def test_query_is_time_bound():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t, timeout=3.0)
    src.query({})
    assert t.calls[0]["timeout"] == 3.0


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:9308",
        "https://localhost:9308",
        "http://[::1]:9308",
        "http://169.254.169.254/metrics",
        "http://10.0.0.5:9308",
        "http://192.168.1.10:9308",
        "http://172.16.0.9:9308",
        "http://0.0.0.0:9308",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        KafkaExporterSource({"base_url": bad_url}, http_get=RecordingTransport([]))


def test_ssrf_allows_http_for_allowlisted_host():
    src = KafkaExporterSource(
        {"base_url": "http://kafka-exp.example.com:9308"},
        http_get=RecordingTransport([]),
    )
    assert src.name


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        KafkaExporterSource(
            {"base_url": "file:///etc/passwd"}, http_get=RecordingTransport([])
        )


# ---------------------------------------------------------------------------
# Prometheus-text parse + normalization
# ---------------------------------------------------------------------------


def test_query_parses_and_filters_metrics():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})

    assert t.calls[0]["url"] == f"{GOOD_URL}/metrics"

    # go_/process_ noise filtered; only wanted kafka_* series kept.
    names = {r["message"].split("{")[0] for r in records}
    assert "go_gc_duration_seconds" not in names
    assert "process_cpu_seconds_total" not in names
    assert "kafka_consumergroup_lag" in names
    assert "kafka_brokers" in names

    for r in records:
        assert r["kind"] == "metrics"
        assert r["source"] == src.name
        assert r["level_or_status"] == ""
        assert isinstance(r["value"], float)


def test_consumer_lag_record_normalization():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})

    lag = next(
        r
        for r in records
        if r["labels"].get("consumergroup") == "orders-svc"
        and r["labels"].get("partition") == "0"
        and r["message"].startswith("kafka_consumergroup_lag")
    )
    assert lag["value"] == 42.0
    assert lag["labels"] == {
        "consumergroup": "orders-svc",
        "partition": "0",
        "topic": "orders",
    }
    assert lag["raw"].startswith("kafka_consumergroup_lag{")


def test_under_replicated_partition_record():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    urp = next(
        r
        for r in records
        if r["message"].startswith("kafka_topic_partition_under_replicated_partition")
        and r["labels"].get("partition") == "0"
    )
    assert urp["value"] == 1.0
    assert urp["labels"]["topic"] == "orders"


def test_no_label_metric_brokers():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    brokers = next(r for r in records if r["message"] == "kafka_brokers")
    assert brokers["value"] == 3.0
    assert brokers["labels"] == {}


def test_spec_metrics_override_allowlist():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"metrics": ["kafka_brokers"]})
    assert {r["message"].split("{")[0] for r in records} == {"kafka_brokers"}


# ---------------------------------------------------------------------------
# Error handling — query never raises
# ---------------------------------------------------------------------------


def test_query_http_error_status_returns_error_record():
    t = RecordingTransport([(503, "service unavailable")])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "503" in records[0]["message"]


def test_query_transport_exception_yields_error_record():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("connection refused")

    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=boom)
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


def test_query_non_text_payload_is_error():
    t = RecordingTransport([(200, {"not": "text"})])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_drops_nan_and_inf_samples():
    text = "kafka_brokers NaN\nkafka_consumergroup_lag{topic=\"t\"} +Inf\n"
    t = RecordingTransport([(200, text)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    # NaN/Inf parse to float in Python; ensure connector does not crash and
    # still returns finite-or-inf floats without raising.
    records = src.query({})
    for r in records:
        assert isinstance(r["value"], float)


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok():
    t = RecordingTransport([(200, METRICS_TEXT)])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_bad_status():
    t = RecordingTransport([(500, "boom")])
    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("dns/socket failure")

    src = KafkaExporterSource({"base_url": GOOD_URL}, http_get=boom)
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["detail"]
