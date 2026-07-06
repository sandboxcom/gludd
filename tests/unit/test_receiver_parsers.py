"""Unit tests for the receiver parsers (OTLP/JSON + syslog), fail-soft + capped."""

from __future__ import annotations

import json
from typing import Any, cast

from general_ludd.connectors.normalize import normalize_join_keys
from general_ludd.receiver.parsers import (
    MAX_PAYLOAD_BYTES,
    parse_otlp_logs,
    parse_otlp_metrics,
    parse_otlp_traces,
    parse_syslog,
)


def _otlp_logs_payload() -> bytes:
    doc = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                        {"key": "host.name", "value": {"stringValue": "web-01"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1700000000000000000",
                                "severityNumber": 17,
                                "severityText": "ERROR",
                                "body": {"stringValue": "disk full"},
                                "traceId": "abc123",
                                "attributes": [
                                    {"key": "code", "value": {"intValue": "500"}}
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    return json.dumps(doc).encode()


class TestOTLPLogs:
    def test_canned_otlp_logs_to_normalized(self) -> None:
        records = parse_otlp_logs(_otlp_logs_payload())
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "log"
        assert rec["message"] == "disk full"
        assert rec["level_or_status"] == "ERROR"
        assert rec["source"] == "checkout"
        assert rec["labels"]["trace_id"] == "abc123"
        assert rec["labels"]["code"] == 500
        assert rec["ts"] == 1700000000.0

    def test_normalize_join_keys_applies_cleanly(self) -> None:
        rec = parse_otlp_logs(_otlp_logs_payload())[0]
        joined = normalize_join_keys(rec)
        assert joined["join"]["trace_id"] == "abc123"
        assert joined["join"]["service"] == "checkout"
        assert joined["join"]["severity"] == "error"

    def test_severity_number_only_maps_to_band(self) -> None:
        doc = {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {"logRecords": [{"severityNumber": 5, "body": {"stringValue": "x"}}]}
                    ]
                }
            ]
        }
        rec = parse_otlp_logs(json.dumps(doc).encode())[0]
        assert rec["level_or_status"] == "debug"

    def test_malformed_json_fails_soft(self) -> None:
        assert parse_otlp_logs(b"\x00not json{") == []

    def test_empty_payload_fails_soft(self) -> None:
        assert parse_otlp_logs(b"") == []

    def test_non_bytes_fails_soft(self) -> None:
        assert cast(Any, parse_otlp_logs)("a string") == []

    def test_oversized_payload_rejected(self) -> None:
        assert parse_otlp_logs(b"{" + b"a" * (MAX_PAYLOAD_BYTES + 1)) == []

    def test_top_level_non_dict_fails_soft(self) -> None:
        assert parse_otlp_logs(json.dumps([1, 2, 3]).encode()) == []

    def test_protobuf_content_type_without_dep_fails_soft(self) -> None:
        # No raw protobuf bytes available in the test env -> guarded -> [].
        assert parse_otlp_logs(b"\x0a\x00", content_type="application/x-protobuf") == []


class TestOTLPMetrics:
    def test_canned_gauge_metric(self) -> None:
        doc = {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}}
                        ]
                    },
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "cpu.usage",
                                    "gauge": {
                                        "dataPoints": [
                                            {
                                                "timeUnixNano": "1700000000000000000",
                                                "asDouble": 0.42,
                                                "attributes": [
                                                    {
                                                        "key": "core",
                                                        "value": {"stringValue": "0"},
                                                    }
                                                ],
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        records = parse_otlp_metrics(json.dumps(doc).encode())
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "metric"
        assert rec["value"] == 0.42
        assert rec["labels"]["metric"] == "cpu.usage"
        assert rec["labels"]["core"] == "0"

    def test_sum_int_point(self) -> None:
        doc = {
            "resourceMetrics": [
                {
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "reqs",
                                    "sum": {"dataPoints": [{"asInt": "7"}]},
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        rec = parse_otlp_metrics(json.dumps(doc).encode())[0]
        assert rec["value"] == 7

    def test_malformed_fails_soft(self) -> None:
        assert parse_otlp_metrics(b"nope") == []
        assert parse_otlp_metrics(b"") == []


class TestOTLPTraces:
    def test_canned_span(self) -> None:
        doc = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "gw"}}
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
                                    "status": {"code": 2},
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        records = parse_otlp_traces(json.dumps(doc).encode())
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "trace"
        assert rec["message"] == "GET /"
        assert rec["labels"]["trace_id"] == "t-1"
        assert rec["labels"]["span_id"] == "s-1"
        assert rec["level_or_status"] == 2

    def test_malformed_fails_soft(self) -> None:
        assert parse_otlp_traces(b"\xff\xfe") == []
        assert parse_otlp_traces(b"") == []


class TestSyslog:
    def test_rfc5424_line(self) -> None:
        line = (
            "<34>1 2003-10-11T22:14:15.003Z mymachine.example.com su 1234 "
            "ID47 - 'su root' failed for user"
        )
        records = parse_syslog(line.encode())
        assert len(records) == 1
        rec = records[0]
        assert rec["level_or_status"] == "critical"  # PRI 34 -> severity 2
        assert rec["labels"]["host"] == "mymachine.example.com"
        assert rec["labels"]["app"] == "su"
        assert rec["labels"]["facility"] == 4
        assert rec["message"].endswith("failed for user")

    def test_rfc5424_strips_structured_data(self) -> None:
        line = (
            '<165>1 2003-10-11T22:14:15.003Z host evntslog - ID47 '
            '[exampleSDID@32473 iut="3"] real message here'
        )
        rec = parse_syslog(line.encode())[0]
        assert rec["message"] == "real message here"

    def test_rfc3164_line(self) -> None:
        line = "<13>Aug 24 05:14:15 myhost myapp[42]: hello from app"
        records = parse_syslog(line)
        assert len(records) == 1
        rec = records[0]
        assert rec["labels"]["host"] == "myhost"
        assert rec["labels"]["tag"] == "myapp"
        assert rec["message"] == "hello from app"
        assert rec["level_or_status"] == "notice"  # PRI 13 -> severity 5

    def test_multiple_lines(self) -> None:
        text = "<13>Aug 24 05:14:15 h app: one\n<13>Aug 24 05:14:16 h app: two"
        records = parse_syslog(text)
        assert len(records) == 2
        assert records[0]["message"] == "one"
        assert records[1]["message"] == "two"

    def test_unstructured_line_kept_not_dropped(self) -> None:
        rec = parse_syslog("just a plain line, no PRI")[0]
        assert rec["message"] == "just a plain line, no PRI"
        assert rec["source"] == "syslog"

    def test_empty_and_blank_fail_soft(self) -> None:
        assert parse_syslog(b"") == []
        assert parse_syslog("   \n  ") == []

    def test_non_bytes_str_fails_soft(self) -> None:
        assert cast(Any, parse_syslog)(12345) == []

    def test_oversized_rejected(self) -> None:
        big = "<13>" + "a" * (MAX_PAYLOAD_BYTES + 1)
        assert parse_syslog(big) == []

    def test_normalize_join_keys_from_syslog(self) -> None:
        line = "<11>Aug 24 05:14:15 web-01 nginx: upstream error"
        rec = parse_syslog(line)[0]
        joined = normalize_join_keys(rec)
        assert joined["join"]["host"] == "web-01"
        assert joined["join"]["severity"] == "error"  # PRI 11 -> severity 3
