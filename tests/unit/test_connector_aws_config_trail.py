"""Unit tests for AwsConfigTrailSource (cloud infra-state connector).

boto3 is never imported for real here — a fake client_factory is injected. Tests
cover: Config-mode normalization (list_discovered_resources +
get_resource_config_history), CloudTrail-mode normalization (lookup_events ->
records), and the boto3-unavailable health path (factory is None / raises).
"""

from __future__ import annotations

from typing import Any

from general_ludd.connectors.aws_config_trail import AwsConfigTrailSource


class FakeConfigClient:
    """Stand-in for a boto3 'config' client."""

    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []
        self.history_calls: list[dict[str, Any]] = []

    def list_discovered_resources(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {
            "resourceIdentifiers": [
                {
                    "resourceType": "AWS::EC2::Instance",
                    "resourceId": "i-0abc123",
                    "resourceName": "web-1",
                },
                {
                    "resourceType": "AWS::EC2::Instance",
                    "resourceId": "i-0def456",
                    "resourceName": "web-2",
                },
            ]
        }

    def get_resource_config_history(self, **kwargs: Any) -> dict[str, Any]:
        self.history_calls.append(kwargs)
        rid = kwargs.get("resourceId", "")
        return {
            "configurationItems": [
                {
                    "resourceId": rid,
                    "resourceType": kwargs.get("resourceType", "AWS::EC2::Instance"),
                    "configurationItemStatus": "OK",
                    "configurationStateId": "state-1",
                    "configurationItemCaptureTime": "2026-06-10T01:02:03Z",
                    "awsRegion": "us-east-1",
                    "availabilityZone": "us-east-1a",
                }
            ]
        }


class FakeCloudTrailClient:
    """Stand-in for a boto3 'cloudtrail' client."""

    def __init__(self) -> None:
        self.lookup_calls: list[dict[str, Any]] = []

    def lookup_events(self, **kwargs: Any) -> dict[str, Any]:
        self.lookup_calls.append(kwargs)
        return {
            "Events": [
                {
                    "EventId": "evt-1",
                    "EventName": "RunInstances",
                    "EventTime": "2026-06-10T03:04:05Z",
                    "Username": "alice",
                    "EventSource": "ec2.amazonaws.com",
                    "AwsRegion": "us-east-1",
                    "CloudTrailEvent": '{"awsRegion":"us-east-1"}',
                },
                {
                    "EventId": "evt-2",
                    "EventName": "DeleteBucket",
                    "EventTime": "2026-06-10T03:05:00Z",
                    "Username": "bob",
                    "EventSource": "s3.amazonaws.com",
                    "AwsRegion": "us-west-2",
                },
            ]
        }


def factory_for(clients: dict[str, Any]) -> Any:
    """Return a client_factory(service_name, **kw) -> fake client."""

    def _factory(service_name: str, **_kw: Any) -> Any:
        if service_name not in clients:
            raise KeyError(service_name)
        return clients[service_name]

    return _factory


def make_source(**overrides: Any) -> AwsConfigTrailSource:
    config: dict[str, Any] = {"region": "us-east-1", "timeout": 5.0}
    config.update(overrides)
    return AwsConfigTrailSource(config)


class TestContract:
    def test_aws_client_callback_compatibility(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def aws_client(method: str, **kwargs: object) -> tuple[int, object]:
            calls.append((method, kwargs))
            return 200, {
                "Events": [
                    {
                        "EventId": "evt-1",
                        "EventName": "RunInstances",
                        "EventTime": "2026-06-10T03:04:05Z",
                    }
                ]
            }

        src = AwsConfigTrailSource(
            {"region": "us-east-1"},
            aws_client=aws_client,
        )

        rows = src.query({"mode": "cloudtrail"})

        assert len(rows) == 1
        assert rows[0]["kind"] == "infra"
        assert rows[0]["message"] == "RunInstances"
        assert calls[0][0] == "lookup_events"
        assert calls[0][1]["service_name"] == "cloudtrail"

    def test_kind_and_name(self) -> None:
        src = make_source(client_factory=factory_for({}))
        assert src.KIND == "infra"
        assert isinstance(src.name, str) and src.name

    def test_health_ok_when_factory_present(self) -> None:
        cfg = FakeConfigClient()
        src = make_source(client_factory=factory_for({"config": cfg}))
        h = src.health()
        assert set(h) >= {"ok", "detail"}
        assert h["ok"] is True

    def test_health_boto3_unavailable_when_factory_none(self) -> None:
        src = make_source(client_factory=None)
        h = src.health()
        assert h["ok"] is False
        assert "boto3 unavailable" in h["detail"].lower()

    def test_health_never_raises_on_factory_error(self) -> None:
        def boom(_service: str, **_kw: Any) -> Any:
            raise RuntimeError("no creds")

        src = make_source(client_factory=boom)
        h = src.health()  # must not raise
        assert h["ok"] is False


class TestConfigMode:
    def test_normalizes_discovered_resources(self) -> None:
        cfg = FakeConfigClient()
        src = make_source(client_factory=factory_for({"config": cfg}))
        rows = src.query({"mode": "config", "resourceType": "AWS::EC2::Instance"})

        assert isinstance(rows, list) and len(rows) == 2
        r = rows[0]
        assert set(r) >= {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert r["kind"] == "infra"
        assert r["ts"] == "2026-06-10T01:02:03Z"
        assert r["level_or_status"] == "OK"
        assert "i-0abc123" in r["message"]
        assert r["labels"]["awsRegion"] == "us-east-1"
        assert r["labels"]["resourceType"] == "AWS::EC2::Instance"
        # history was consulted per discovered resource
        assert len(cfg.history_calls) == 2


class TestCloudTrailMode:
    def test_normalizes_lookup_events(self) -> None:
        ct = FakeCloudTrailClient()
        src = make_source(client_factory=factory_for({"cloudtrail": ct}))
        rows = src.query({"mode": "cloudtrail"})

        assert isinstance(rows, list) and len(rows) == 2
        r = rows[0]
        assert r["kind"] == "infra"
        assert r["level_or_status"] == "audit"
        assert r["ts"] == "2026-06-10T03:04:05Z"
        assert "RunInstances" in r["message"]
        assert "alice" in r["message"]
        assert r["labels"]["EventSource"] == "ec2.amazonaws.com"
        assert r["labels"]["awsRegion"] == "us-east-1"
        assert r["raw"]["EventId"] == "evt-1"

    def test_second_event_region_from_event(self) -> None:
        ct = FakeCloudTrailClient()
        src = make_source(client_factory=factory_for({"cloudtrail": ct}))
        rows = src.query({"mode": "cloudtrail"})
        assert rows[1]["labels"]["awsRegion"] == "us-west-2"
        assert "DeleteBucket" in rows[1]["message"]


class TestModeSelection:
    def test_default_mode_is_config(self) -> None:
        cfg = FakeConfigClient()
        src = make_source(client_factory=factory_for({"config": cfg}))
        rows = src.query({})  # no mode -> config default
        assert len(rows) == 2
        assert cfg.list_calls, "config path not taken by default"

    def test_unknown_mode_returns_empty(self) -> None:
        src = make_source(client_factory=factory_for({}))
        rows = src.query({"mode": "nope"})
        assert rows == []

    def test_query_without_factory_returns_empty(self) -> None:
        src = make_source(client_factory=None)
        assert src.query({"mode": "config"}) == []
        assert src.query({"mode": "cloudtrail"}) == []
