"""Unit tests for the AWS pipeline observability connector.

All AWS interaction is mocked: a fake boto3-style client factory is injected via
config, so no network or credentials are ever touched. Tests assert the
normalization contract for CodePipeline executions and CloudWatch Logs events,
plus fail-closed health() behaviour when boto3 is unavailable.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from general_ludd.connectors.aws_pipeline import AwsPipelineSource


class FakeCodePipelineClient:
    """Stand-in for a boto3 'codepipeline' client."""

    def __init__(self, executions: list[dict[str, Any]]) -> None:
        self._executions = executions
        self.calls: list[dict[str, Any]] = []

    def list_pipeline_executions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"pipelineExecutionSummaries": list(self._executions)}


class FakeLogsClient:
    """Stand-in for a boto3 'logs' (CloudWatch Logs) client."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"events": list(self._events)}


def _factory(mapping: dict[str, Any]) -> Any:
    """Build a client_factory(service)->client backed by a service->client map."""

    def factory(service: str) -> Any:
        try:
            return mapping[service]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected service requested: {service}") from exc

    return factory


CANNED_EXECUTIONS = [
    {
        "pipelineExecutionId": "exec-1",
        "status": "Succeeded",
        "lastUpdateTime": 1_700_000_000,
        "trigger": {"triggerType": "Webhook"},
    },
    {
        "pipelineExecutionId": "exec-2",
        "status": "Failed",
        "lastUpdateTime": 1_700_000_100,
        "trigger": {"triggerType": "StartPipelineExecution"},
    },
    {
        "pipelineExecutionId": "exec-3",
        "status": "InProgress",
        "lastUpdateTime": 1_700_000_200,
    },
]

CANNED_LOG_EVENTS = [
    {
        "timestamp": 1_700_000_000_000,
        "message": "ERROR something broke in the build",
        "logStreamName": "stream-a",
    },
    {
        "timestamp": 1_700_000_005_000,
        "message": "plain informational line",
        "logStreamName": "stream-b",
    },
]


class TestContract:
    def test_kind_and_name(self) -> None:
        src = AwsPipelineSource(
            {
                "region": "us-east-1",
                "pipeline": "my-pipeline",
                "client_factory": _factory({"codepipeline": FakeCodePipelineClient([])}),
            }
        )
        assert src.KIND == "pipeline"
        assert AwsPipelineSource.KIND == "pipeline"
        assert isinstance(src.name, str)
        assert src.name

    def test_health_ok_with_injected_client(self) -> None:
        fake = FakeCodePipelineClient([])
        src = AwsPipelineSource(
            {
                "region": "eu-west-2",
                "pipeline": "p",
                "client_factory": _factory({"codepipeline": fake}),
            }
        )
        health = src.health()
        assert isinstance(health, dict)
        assert health["ok"] is True
        assert health["kind"] == "pipeline"
        assert health["region"] == "eu-west-2"

    def test_health_never_raises(self) -> None:
        def exploding_factory(service: str) -> Any:
            raise RuntimeError("boom")

        src = AwsPipelineSource(
            {"region": "us-east-1", "pipeline": "p", "client_factory": exploding_factory}
        )
        health = src.health()  # must not raise
        assert health["ok"] is False
        # Raw exception text must not leak; a static marker is returned instead.
        assert "boom" not in health["detail"]
        assert health["detail"] == "boto3 unavailable"


class TestQueryNormalization:
    def _source(self, fake: FakeCodePipelineClient, **extra: Any) -> AwsPipelineSource:
        cfg: dict[str, Any] = {
            "region": "us-east-1",
            "pipeline": "build-pipeline",
            "client_factory": _factory({"codepipeline": fake}),
        }
        cfg.update(extra)
        return AwsPipelineSource(cfg)

    def test_query_normalizes_executions(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        src = self._source(fake)
        records = src.query({})
        assert isinstance(records, list)
        assert len(records) == 3

        rec = records[0]
        assert set(rec.keys()) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert rec["kind"] == "pipeline"
        assert rec["source"] == src.name
        assert rec["ts"] == 1_700_000_000
        assert rec["level_or_status"] == "Succeeded"
        assert "build-pipeline" in rec["message"]
        assert "exec-1" in rec["message"]
        assert rec["value"] is None
        assert rec["labels"]["executionId"] == "exec-1"
        assert rec["labels"]["trigger"] == "Webhook"
        assert rec["raw"] == CANNED_EXECUTIONS[0]

    def test_query_passes_pipeline_name_to_client(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        src = self._source(fake)
        src.query({})
        assert fake.calls
        assert fake.calls[0]["pipelineName"] == "build-pipeline"

    def test_aws_client_callback_and_name_alias(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def aws_client(method: str, **kwargs: Any) -> tuple[int, object]:
            calls.append((method, kwargs))
            return 200, {"pipelineExecutionSummaries": CANNED_EXECUTIONS}

        src = AwsPipelineSource(
            {"region": "us-east-1", "name": "build-pipeline"},
            aws_client=aws_client,
        )

        records = src.query({})

        assert len(records) == 3
        assert records[0]["kind"] == "pipeline"
        assert calls == [
            ("list_pipeline_executions", {"pipelineName": "build-pipeline"})
        ]

    def test_query_trigger_missing_is_none_label(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        src = self._source(fake)
        records = src.query({})
        # exec-3 has no trigger
        assert records[2]["labels"]["trigger"] is None

    def test_query_limit_filter(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        src = self._source(fake)
        records = src.query({"limit": 2})
        assert len(records) == 2
        assert records[0]["labels"]["executionId"] == "exec-1"

    def test_query_status_filter(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        src = self._source(fake)
        records = src.query({"status": "Failed"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "Failed"
        assert records[0]["labels"]["executionId"] == "exec-2"

    def test_query_requires_pipeline(self) -> None:
        fake = FakeCodePipelineClient(CANNED_EXECUTIONS)
        cfg = {
            "region": "us-east-1",
            "client_factory": _factory({"codepipeline": fake}),
        }
        src = AwsPipelineSource(cfg)
        with pytest.raises(ValueError, match="pipeline"):
            src.query({})


class TestLogBridging:
    def _source(self, fake: FakeLogsClient, **extra: Any) -> AwsPipelineSource:
        cfg: dict[str, Any] = {
            "region": "us-east-1",
            "log_group": "/aws/codebuild/my-project",
            "client_factory": _factory({"logs": fake}),
        }
        cfg.update(extra)
        return AwsPipelineSource(cfg)

    def test_fetch_logs_normalizes_events(self) -> None:
        fake = FakeLogsClient(CANNED_LOG_EVENTS)
        src = self._source(fake)
        records = src.fetch_logs("/aws/codebuild/my-project", since=1_700_000_000_000)
        assert len(records) == 2

        rec = records[0]
        assert set(rec.keys()) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        # kind-bridging: logs records carry kind='logs', not 'pipeline'
        assert rec["kind"] == "logs"
        assert rec["ts"] == 1_700_000_000  # timestamp/1000
        assert rec["level_or_status"] == "ERROR"
        assert rec["message"] == "ERROR something broke in the build"
        assert rec["value"] is None
        assert rec["labels"]["logStreamName"] == "stream-a"
        assert rec["raw"] == CANNED_LOG_EVENTS[0]

    def test_fetch_logs_unparsable_level_is_none(self) -> None:
        fake = FakeLogsClient(CANNED_LOG_EVENTS)
        src = self._source(fake)
        records = src.fetch_logs("/aws/codebuild/my-project", since=0)
        assert records[1]["level_or_status"] is None

    def test_fetch_logs_passes_log_group_and_since(self) -> None:
        fake = FakeLogsClient(CANNED_LOG_EVENTS)
        src = self._source(fake)
        src.fetch_logs("/aws/codebuild/my-project", since=1_700_000_000_000)
        assert fake.calls
        call = fake.calls[0]
        assert call["logGroupName"] == "/aws/codebuild/my-project"
        assert call["startTime"] == 1_700_000_000_000

    def test_fetch_logs_uses_config_log_group_default(self) -> None:
        fake = FakeLogsClient(CANNED_LOG_EVENTS)
        src = self._source(fake)
        # explicit empty -> falls back to config log_group
        src.fetch_logs(None, since=0)
        assert fake.calls[0]["logGroupName"] == "/aws/codebuild/my-project"


class TestBoto3Unavailable:
    def test_module_imports_without_boto3(self) -> None:
        # Module-level import already succeeded (top of file), proving the boto3
        # import is guarded. Re-affirm the class is usable.
        assert AwsPipelineSource.KIND == "pipeline"

    def test_health_not_ok_when_boto3_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No client_factory injected -> default path tries to import boto3.
        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "boto3" or name.startswith("boto3."):
                raise ImportError("No module named 'boto3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        src = AwsPipelineSource({"region": "us-east-1", "pipeline": "p"})
        health = src.health()
        assert health["ok"] is False
        assert "boto3 unavailable" in health["detail"]

    def test_query_raises_clearly_when_boto3_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "boto3" or name.startswith("boto3."):
                raise ImportError("No module named 'boto3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        src = AwsPipelineSource({"region": "us-east-1", "pipeline": "p"})
        with pytest.raises(RuntimeError, match="boto3"):
            src.query({})
