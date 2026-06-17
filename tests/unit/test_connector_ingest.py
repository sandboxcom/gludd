"""Unit tests for the push-side ingest normalizer (``connectors/ingest.py``).

The normalizer turns a raw pushed payload (OTLP/JSON logs+spans, a generic
webhook JSON body, or an RFC5424/3164 syslog line) into a list of normalized
records matching the connector record schema
(``ts, source, kind, level_or_status, message, value, labels, raw``).

Contract under test:

* ``normalize(fmt, payload, headers) -> list[dict]`` never raises — a malformed
  payload returns ``[]``.
* OTLP logs+spans -> records with ``kind`` ``logs``/``traces`` and labels
  carrying ``trace_id``/``span_id``/``service``.
* webhook -> records sniffed to the right shape (GitHub event / Alertmanager
  alert / generic) with the relevant fields surfaced into labels.
* syslog -> ``kind='logs'`` records with ``host``/``severity`` labels.
* payload size is bounded (an oversized payload returns ``[]``).
"""

from __future__ import annotations

import json

from general_ludd.connectors.ingest import MAX_PAYLOAD_BYTES, normalize

_RECORD_KEYS = {
    "ts",
    "source",
    "kind",
    "level_or_status",
    "message",
    "value",
    "labels",
    "raw",
}


def _assert_record_shape(rec: dict) -> None:
    assert isinstance(rec, dict)
    assert set(rec) >= _RECORD_KEYS, f"missing keys: {_RECORD_KEYS - set(rec)}"
    assert isinstance(rec["labels"], dict)
    assert rec["kind"] in {"logs", "metrics", "traces", "pipeline", "alerts", "deploys", "issues"}


# --------------------------------------------------------------------------- #
# OTLP
# --------------------------------------------------------------------------- #
def test_otlp_logs_produce_log_records_with_service_label() -> None:
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}}
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1700000000000000000",
                                "severityText": "ERROR",
                                "body": {"stringValue": "boom"},
                                "traceId": "abc123",
                                "spanId": "def456",
                            }
                        ]
                    }
                ],
            }
        ]
    }
    recs = normalize("otlp", json.dumps(payload).encode(), {})
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "logs"
    assert rec["message"] == "boom"
    assert rec["level_or_status"].lower() == "error"
    assert rec["labels"]["trace_id"] == "abc123"
    assert rec["labels"]["span_id"] == "def456"
    assert rec["labels"]["service"] == "checkout"
    # 1700000000000000000 ns -> 1700000000.0 s
    assert rec["ts"] == 1700000000.0


def test_otlp_spans_produce_trace_records_with_trace_id() -> None:
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "api"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "t-1",
                                "spanId": "s-1",
                                "name": "GET /",
                                "startTimeUnixNano": "1700000000000000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }
    recs = normalize("otlp", json.dumps(payload).encode(), {})
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "traces"
    assert rec["labels"]["trace_id"] == "t-1"
    assert rec["labels"]["span_id"] == "s-1"
    assert rec["labels"]["service"] == "api"
    assert rec["message"] == "GET /"


def test_otlp_logs_and_spans_together() -> None:
    payload = {
        "resourceLogs": [
            {"scopeLogs": [{"logRecords": [{"body": {"stringValue": "l1"}}]}]}
        ],
        "resourceSpans": [
            {"scopeSpans": [{"spans": [{"traceId": "x", "name": "sp"}]}]}
        ],
    }
    recs = normalize("otlp", json.dumps(payload).encode(), {})
    kinds = sorted(r["kind"] for r in recs)
    assert kinds == ["logs", "traces"]


def test_otlp_malformed_returns_empty_no_raise() -> None:
    assert normalize("otlp", b"not json at all {{{", {}) == []
    assert normalize("otlp", b"", {}) == []
    # valid json, wrong shape -> still no records, no raise
    assert normalize("otlp", b"[1, 2, 3]", {}) == []


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
def test_webhook_github_event_sniffed_to_deploy() -> None:
    payload = {
        "deployment": {"sha": "deadbeef", "environment": "prod"},
        "repository": {"full_name": "acme/widget"},
    }
    headers = {"X-GitHub-Event": "deployment"}
    recs = normalize("webhook", json.dumps(payload).encode(), headers)
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "deploys"
    assert rec["labels"].get("event") == "deployment"
    assert rec["labels"].get("repo") == "acme/widget"


def test_webhook_github_issue_event() -> None:
    payload = {
        "action": "opened",
        "issue": {"number": 7, "title": "bug"},
        "repository": {"full_name": "acme/widget"},
    }
    headers = {"X-GitHub-Event": "issues"}
    recs = normalize("webhook", json.dumps(payload).encode(), headers)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "issues"
    assert rec["labels"].get("event") == "issues"


def test_webhook_alertmanager_alerts_become_alert_records() -> None:
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighCPU", "severity": "critical"},
                "annotations": {"summary": "cpu hot"},
                "startsAt": "2023-11-14T00:00:00Z",
            },
            {
                "status": "resolved",
                "labels": {"alertname": "DiskLow"},
                "annotations": {},
            },
        ]
    }
    recs = normalize("webhook", json.dumps(payload).encode(), {})
    assert len(recs) == 2
    for rec in recs:
        _assert_record_shape(rec)
        assert rec["kind"] == "alerts"
    first = recs[0]
    assert first["labels"].get("alertname") == "HighCPU"
    assert first["labels"].get("severity") == "critical"
    assert first["level_or_status"] == "firing"


def test_webhook_generic_json_falls_back_to_log() -> None:
    payload = {"hello": "world", "n": 3}
    recs = normalize("webhook", json.dumps(payload).encode(), {})
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "logs"
    assert rec["raw"] == payload


def test_webhook_malformed_returns_empty_no_raise() -> None:
    assert normalize("webhook", b"<<not json>>", {}) == []
    assert normalize("webhook", b"", {}) == []


# --------------------------------------------------------------------------- #
# Syslog
# --------------------------------------------------------------------------- #
def test_syslog_rfc5424_parsed_to_log_with_host_and_severity() -> None:
    # PRI 34 -> facility 4 (auth), severity 2 (crit)
    line = (
        "<34>1 2023-11-14T00:00:00Z myhost myapp 1234 ID47 - "
        "'su root' failed for lonvick"
    )
    recs = normalize("syslog", line.encode(), {})
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "logs"
    assert rec["labels"]["host"] == "myhost"
    assert rec["labels"]["severity"] == 2
    assert "su root" in rec["message"]


def test_syslog_rfc3164_parsed_to_log() -> None:
    # PRI 13 -> severity 5 (notice)
    line = "<13>Oct 11 22:14:15 host1 sshd[1234]: login ok"
    recs = normalize("syslog", line.encode(), {})
    assert len(recs) == 1
    rec = recs[0]
    _assert_record_shape(rec)
    assert rec["kind"] == "logs"
    assert rec["labels"]["host"] == "host1"
    assert rec["labels"]["severity"] == 5


def test_syslog_multiple_lines_each_become_a_record() -> None:
    body = b"<13>host one msg-a\n<14>host two msg-b\n"
    recs = normalize("syslog", body, {})
    assert len(recs) == 2
    assert all(r["kind"] == "logs" for r in recs)


def test_syslog_malformed_returns_empty_no_raise() -> None:
    # no PRI, no recognizable structure -> no records, no raise
    assert normalize("syslog", b"", {}) == []
    assert normalize("syslog", b"\x00\x01\x02", {}) == []


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #
def test_unknown_format_returns_empty() -> None:
    assert normalize("mystery", b"{}", {}) == []


def test_oversized_payload_is_bounded() -> None:
    huge = b"x" * (MAX_PAYLOAD_BYTES + 1)
    assert normalize("otlp", huge, {}) == []
    assert normalize("webhook", huge, {}) == []
    assert normalize("syslog", huge, {}) == []


def test_normalize_never_raises_on_garbage_for_every_format() -> None:
    for fmt in ("otlp", "webhook", "syslog"):
        # bytes that are not valid utf-8 / not json / not syslog
        assert normalize(fmt, b"\xff\xfe\xfd", {}) == []


def test_source_field_is_set_on_records() -> None:
    recs = normalize("syslog", b"<13>host1 hi there", {})
    assert recs
    assert isinstance(recs[0]["source"], str)
    assert recs[0]["source"]
