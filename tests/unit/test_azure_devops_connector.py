"""Unit tests for the self-contained AzureDevOpsSource connector.

A fake transport returns canned payloads so no real network is touched.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest

from general_ludd.connectors.azure_devops import AzureDevOpsSource

_BUILDS_BODY: dict[str, Any] = {
    "count": 2,
    "value": [
        {
            "id": 100,
            "buildNumber": "20260610.1",
            "status": "completed",
            "result": "succeeded",
            "queueTime": "2026-06-10T07:59:00.000Z",
            "finishTime": "2026-06-10T08:10:00.123456Z",
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "abc123",
            "definition": {"id": 5, "name": "CI"},
        },
        {
            "id": 101,
            "buildNumber": "20260611.2",
            "status": "completed",
            "result": "failed",
            "queueTime": "2026-06-11T09:00:00Z",
            "sourceBranch": "refs/heads/feature",
            "sourceVersion": "def456",
            "definition": {"id": 5, "name": "CI"},
        },
    ],
}


class _FakeTransport:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.url: str | None = None
        self.headers: dict[str, str] | None = None

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any]:
        self.url = url
        self.headers = headers
        return self.status, self.body


def _make(transport: _FakeTransport, **cfg: Any) -> AzureDevOpsSource:
    config = {
        "org": "myorg",
        "project": "myproj",
        "base_url": "https://dev.azure.com",
    }
    config.update(cfg)
    return AzureDevOpsSource(config, http_get=transport)


def test_kind_and_name() -> None:
    src = _make(_FakeTransport(200, _BUILDS_BODY))
    assert src.KIND == "pipeline"
    assert src.name == "azure-devops:myorg/myproj"


def test_missing_org_or_project_raises() -> None:
    with pytest.raises(ValueError):
        AzureDevOpsSource({"project": "p"}, http_get=_FakeTransport(200, {}))
    with pytest.raises(ValueError):
        AzureDevOpsSource({"org": "o"}, http_get=_FakeTransport(200, {}))


def test_query_normalizes_records() -> None:
    src = _make(_FakeTransport(200, _BUILDS_BODY))
    records = src.query({})
    assert len(records) == 2

    first = records[0]
    assert first["kind"] == "pipeline"
    assert first["source"] == "azure-devops:myorg/myproj"
    assert first["level_or_status"] == "succeeded"
    assert first["message"] == "CI #20260610.1"
    assert first["value"] is None
    assert first["labels"] == {
        "id": 100,
        "sourceBranch": "refs/heads/main",
        "sourceVersion": "abc123",
    }
    assert first["raw"] is _BUILDS_BODY["value"][0]

    expected = datetime(2026, 6, 10, 8, 10, 0, 123456, tzinfo=UTC).timestamp()
    assert first["ts"] == expected

    assert records[1]["level_or_status"] == "failed"


def test_status_fallback_when_no_result() -> None:
    body = {"value": [{"id": 1, "status": "inProgress", "buildNumber": "9"}]}
    src = _make(_FakeTransport(200, body))
    rec = src.query({})[0]
    assert rec["level_or_status"] == "inProgress"


def test_ts_falls_back_to_queue_time() -> None:
    body = {
        "value": [
            {"id": 1, "queueTime": "2026-01-01T00:00:00Z", "buildNumber": "1"}
        ]
    }
    src = _make(_FakeTransport(200, body))
    rec = src.query({})[0]
    expected = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    assert rec["ts"] == expected


def test_basic_auth_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "tok123")
    t = _FakeTransport(200, _BUILDS_BODY)
    _make(t).query({})
    assert t.headers is not None
    expected = base64.b64encode(b":tok123").decode("ascii")
    assert t.headers["Authorization"] == f"Basic {expected}"


def test_no_auth_header_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    t = _FakeTransport(200, _BUILDS_BODY)
    _make(t).query({})
    assert t.headers is not None
    assert "Authorization" not in t.headers


def test_query_url_includes_api_version_and_filters() -> None:
    t = _FakeTransport(200, _BUILDS_BODY)
    _make(t).query({"statusFilter": "completed", "resultFilter": "failed"})
    assert t.url is not None
    assert "/myorg/myproj/_apis/build/builds" in t.url
    assert "api-version=7.0" in t.url
    assert "statusFilter=completed" in t.url
    assert "resultFilter=failed" in t.url


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost",
        "http://127.0.0.1:8080",
        "http://169.254.169.254/metadata",
        "http://192.168.1.10",
        "gopher://dev.azure.com",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        AzureDevOpsSource(
            {"org": "o", "project": "p", "base_url": bad_url},
            http_get=_FakeTransport(200, {}),
        )


def test_health_ok() -> None:
    src = _make(_FakeTransport(200, _BUILDS_BODY))
    assert src.health() == {"ok": True, "detail": "HTTP 200"}


def test_health_not_ok() -> None:
    src = _make(_FakeTransport(401, {"message": "unauthorized"}))
    h = src.health()
    assert h["ok"] is False
    assert "401" in h["detail"]


def test_health_never_raises() -> None:
    def boom(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        raise OSError("connection refused")

    src = _make(boom)  # type: ignore[arg-type]  # test stub
    h = src.health()
    assert h["ok"] is False
    assert "transport error" in h["detail"]


def test_query_empty_on_non_dict_body() -> None:
    src = _make(_FakeTransport(200, ["not", "a", "dict"]))
    assert src.query({}) == []


def test_fetch_logs_returns_logs() -> None:
    body = {"count": 1, "value": [{"id": 1, "type": "Container"}]}
    t = _FakeTransport(200, body)
    out = _make(t).fetch_logs(100)
    assert out == body["value"]
    assert t.url is not None
    assert "/builds/100/logs" in t.url
