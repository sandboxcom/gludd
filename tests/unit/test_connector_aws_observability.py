"""Unit tests for the AWS observability connector.

All AWS clients are MOCKED via an injectable ``client_factory`` — no real AWS
calls, no boto3 required at test time. Each mode (logs/metrics/traces/events)
gets its own fake client returning canned payloads, and we assert the
normalized record shape per mode.
"""

from __future__ import annotations

import builtins
import importlib
from datetime import UTC, datetime
from typing import Any

import pytest

from general_ludd.connectors import aws_observability as mod
from general_ludd.connectors.aws_observability import AwsObservabilitySource

# --------------------------------------------------------------------------- #
# Fake clients — each returns a canned payload for exactly one boto3 method.
# --------------------------------------------------------------------------- #


class FakeLogsClient:
    """Stand-in for boto3 ``logs`` client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "events": [
                {
                    "timestamp": 1700000000000,
                    "message": "boom happened",
                    "logStreamName": "stream-a",
                },
                {
                    "timestamp": 1700000001500,
                    "message": "second line",
                    "logStreamName": "stream-b",
                },
            ]
        }


class FakeMetricsClient:
    """Stand-in for boto3 ``cloudwatch`` client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "MetricDataResults": [
                {
                    "Id": "m1",
                    "Label": "CPUUtilization",
                    "Timestamps": [
                        datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
                        datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC),
                    ],
                    "Values": [12.5, 18.0],
                }
            ]
        }


class FakeXRayClient:
    """Stand-in for boto3 ``xray`` client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_trace_summaries(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "TraceSummaries": [
                {
                    "Id": "1-5759e988-bd862e3fe1be46a994272793",
                    "Duration": 3.2,
                    "HasError": True,
                    "ServiceIds": [{"Name": "checkout-svc"}],
                    "StartTime": datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
                },
                {
                    "Id": "2-aaaaaaaa-bbbbbbbbbbbbbbbbbbbbbbbb",
                    "Duration": 0.5,
                    "HasError": False,
                    "ServiceIds": [],
                    "StartTime": datetime(2023, 11, 14, 22, 14, 20, tzinfo=UTC),
                },
            ]
        }


class FakeCloudTrailClient:
    """Stand-in for boto3 ``cloudtrail`` client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def lookup_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "Events": [
                {
                    "EventName": "ConsoleLogin",
                    "Username": "alice",
                    "EventTime": datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
                    "EventSource": "signin.amazonaws.com",
                    "AwsRegion": "us-east-1",
                    "Resources": [
                        {"ResourceType": "AWS::IAM::User", "ResourceName": "alice"}
                    ],
                }
            ]
        }


def _factory(mapping: dict[str, Any]) -> Any:
    """Build a client_factory that returns the fake registered for a service."""

    def factory(service: str) -> Any:
        if service not in mapping:
            raise AssertionError(f"unexpected service requested: {service}")
        return mapping[service]

    return factory


# --------------------------------------------------------------------------- #
# Contract / construction
# --------------------------------------------------------------------------- #


def test_kind_is_class_attr() -> None:
    assert AwsObservabilitySource.KIND == "aws_observability"


def test_name_attr_defaults_and_overrides() -> None:
    src = AwsObservabilitySource({}, client_factory=_factory({}))
    assert src.name == "aws_observability"
    named = AwsObservabilitySource({"name": "prod-aws"}, client_factory=_factory({}))
    assert named.name == "prod-aws"


def test_region_is_config_driven() -> None:
    src = AwsObservabilitySource(
        {"region": "eu-west-1"}, client_factory=_factory({})
    )
    assert src.region == "eu-west-1"


def test_no_hardcoded_credentials_in_config_handling() -> None:
    # Construction must not require or store any secret material.
    src = AwsObservabilitySource({"region": "us-east-1"}, client_factory=_factory({}))
    blob = repr(vars(src)).lower()
    assert "secret" not in blob
    assert "aws_access_key" not in blob


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #


def test_health_ok_with_factory() -> None:
    # A factory that successfully returns a client for the health probe.
    def ok_factory(service: str) -> Any:
        return object()

    src = AwsObservabilitySource({}, client_factory=ok_factory)
    h = src.health()
    assert h["ok"] is True
    assert isinstance(h["detail"], str)


def test_health_never_raises_on_factory_error() -> None:
    def boom(service: str) -> Any:
        raise RuntimeError("no creds")

    src = AwsObservabilitySource({}, client_factory=boom)
    # health probes a client; even a failing factory must not raise.
    h = src.health()
    assert h["ok"] is False
    assert "no creds" in h["detail"] or "creds" in h["detail"].lower()


def test_health_not_ok_when_boto3_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate boto3 not installed: default factory path reports unavailable."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "boto3" or name.startswith("boto3."):
            raise ImportError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # No injected factory -> falls back to default boto3-importing factory.
    src = AwsObservabilitySource({"region": "us-east-1"})
    h = src.health()
    assert h["ok"] is False
    assert "boto3" in h["detail"].lower()


def test_module_imports_without_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module must import cleanly even if boto3 is absent."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "boto3" or name.startswith("boto3."):
            raise ImportError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    reloaded = importlib.reload(mod)
    assert hasattr(reloaded, "AwsObservabilitySource")
    # restore for other tests
    importlib.reload(mod)


# --------------------------------------------------------------------------- #
# query() — per-mode normalization
# --------------------------------------------------------------------------- #


def _assert_normalized(rec: dict[str, Any]) -> None:
    for key in (
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    ):
        assert key in rec, f"missing normalized key: {key}"
    assert isinstance(rec["labels"], dict)


def test_query_logs_mode() -> None:
    logs = FakeLogsClient()
    src = AwsObservabilitySource(
        {"name": "n", "region": "us-east-1"},
        client_factory=_factory({"logs": logs}),
    )
    recs = src.query(
        {
            "mode": "logs",
            "logGroupName": "/aws/lambda/foo",
            "filterPattern": "ERROR",
            "startTime": 1700000000000,
        }
    )
    assert len(recs) == 2
    for r in recs:
        _assert_normalized(r)
        assert r["kind"] == "logs"
        assert r["source"] == "n"
    first = recs[0]
    assert first["ts"] == 1700000000000 / 1000
    assert first["message"] == "boom happened"
    assert first["labels"]["logGroup"] == "/aws/lambda/foo"
    assert first["labels"]["logStream"] == "stream-a"
    # call wiring
    assert logs.calls[0]["logGroupName"] == "/aws/lambda/foo"
    assert logs.calls[0]["filterPattern"] == "ERROR"
    assert logs.calls[0]["startTime"] == 1700000000000


def test_query_metrics_mode() -> None:
    cw = FakeMetricsClient()
    src = AwsObservabilitySource(
        {"region": "us-east-1"}, client_factory=_factory({"cloudwatch": cw})
    )
    recs = src.query(
        {
            "mode": "metrics",
            "namespace": "AWS/EC2",
            "metricName": "CPUUtilization",
            "MetricDataQueries": [{"Id": "m1"}],
            "StartTime": datetime(2023, 11, 14, tzinfo=UTC),
            "EndTime": datetime(2023, 11, 15, tzinfo=UTC),
        }
    )
    assert len(recs) == 2
    for r in recs:
        _assert_normalized(r)
        assert r["kind"] == "metrics"
    assert recs[0]["value"] == 12.5
    assert recs[1]["value"] == 18.0
    assert recs[0]["labels"]["namespace"] == "AWS/EC2"
    assert recs[0]["labels"]["metricName"] == "CPUUtilization"
    assert "dimensions" in recs[0]["labels"]
    # ts derived from the per-point timestamp
    assert isinstance(recs[0]["ts"], float)


def test_query_traces_mode() -> None:
    xray = FakeXRayClient()
    src = AwsObservabilitySource(
        {"region": "us-east-1"}, client_factory=_factory({"xray": xray})
    )
    recs = src.query(
        {
            "mode": "traces",
            "StartTime": datetime(2023, 11, 14, tzinfo=UTC),
            "EndTime": datetime(2023, 11, 15, tzinfo=UTC),
            "FilterExpression": "service('checkout-svc')",
        }
    )
    assert len(recs) == 2
    for r in recs:
        _assert_normalized(r)
        assert r["kind"] == "traces"
    err = recs[0]
    assert err["value"] == 3.2
    assert err["level_or_status"] == "error"
    assert err["labels"]["trace_id"] == "1-5759e988-bd862e3fe1be46a994272793"
    assert err["labels"]["service"] == "checkout-svc"
    ok = recs[1]
    assert ok["level_or_status"] != "error"
    # call wiring
    assert xray.calls[0]["FilterExpression"] == "service('checkout-svc')"


def test_query_events_mode() -> None:
    ct = FakeCloudTrailClient()
    src = AwsObservabilitySource(
        {"region": "us-east-1"}, client_factory=_factory({"cloudtrail": ct})
    )
    recs = src.query(
        {
            "mode": "events",
            "LookupAttributes": [
                {"AttributeKey": "EventName", "AttributeValue": "ConsoleLogin"}
            ],
            "StartTime": datetime(2023, 11, 14, tzinfo=UTC),
        }
    )
    assert len(recs) == 1
    rec = recs[0]
    _assert_normalized(rec)
    assert rec["kind"] == "logs"
    assert rec["level_or_status"] == "audit"
    assert "ConsoleLogin" in rec["message"]
    assert "alice" in rec["message"]
    assert rec["labels"]["EventSource"] == "signin.amazonaws.com"
    assert rec["labels"]["awsRegion"] == "us-east-1"
    assert "resources" in rec["labels"]


def test_query_unknown_mode_raises() -> None:
    src = AwsObservabilitySource({}, client_factory=_factory({}))
    with pytest.raises(ValueError):
        src.query({"mode": "nope"})


def test_query_missing_mode_raises() -> None:
    src = AwsObservabilitySource({}, client_factory=_factory({}))
    with pytest.raises(ValueError):
        src.query({})
