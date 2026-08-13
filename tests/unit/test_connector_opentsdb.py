"""Unit tests for OpenTsdbSource (mocked transport, no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.opentsdb import KIND, OpenTsdbSource, SSRFError

# OpenTSDB success: a JSON array of result objects, each with a dps map.
DPS_PAYLOAD = [
    {
        "metric": "sys.cpu.user",
        "tags": {"host": "web01"},
        "aggregateTags": [],
        "dps": {"1700000000": 12.5, "1700000060": 13.0, "1700000120": 11.0},
    },
    {
        "metric": "sys.cpu.user",
        "tags": {"host": "web02"},
        "aggregateTags": [],
        "dps": {"1700000000": 5.0},
    },
]

ERROR_PAYLOAD = {"error": {"code": 400, "message": "No such name for 'metrics'"}}


class FakeTransport:
    def __init__(self, status: int = 200, body: str = "", raises: BaseException | None = None):
        self.status = status
        self.body = body
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, str]:
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {}, "body": body, "timeout": timeout}
        )
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def _src(transport: FakeTransport, **extra: Any) -> OpenTsdbSource:
    config = {"name": "otsdb", "base_url": "https://tsdb.example.com", **extra}
    return OpenTsdbSource(config, transport=transport)


def _body_json(transport: FakeTransport) -> dict[str, Any]:
    raw = transport.calls[-1]["body"]
    assert raw is not None
    return json.loads(raw.decode("utf-8"))


def test_kind_attrs() -> None:
    assert KIND == "metrics"
    assert OpenTsdbSource.KIND == "metrics"


def test_name_attr() -> None:
    assert _src(FakeTransport(200, "[]")).name == "otsdb"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:4242",
        "http://172.16.0.1",
        "http://localhost:4242",
        "http://[::1]",
        "http://169.254.169.254/",  # cloud metadata IP (regression)
        # Metadata NAME coverage added by routing through the canonical guard.
        "http://metadata.google.internal/",
    ],
)
def test_private_host_rejected(url: str) -> None:
    with pytest.raises(SSRFError):
        OpenTsdbSource({"base_url": url}, transport=FakeTransport())


def test_private_host_allowed_opt_in() -> None:
    src = OpenTsdbSource(
        {"base_url": "http://127.0.0.1:4242", "allow_private": True},
        transport=FakeTransport(200, "[]"),
    )
    assert src.base_url == "http://127.0.0.1:4242"


def test_query_dps_one_record_per_point() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    records = _src(t).query(
        {"start": "1h-ago", "metric": "sys.cpu.user", "tags": {"host": "*"}}
    )
    # 3 points for web01 + 1 for web02
    assert len(records) == 4
    r = records[0]
    assert set(r) == {
        "ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"
    }
    assert r["kind"] == "metrics"
    assert r["source"] == "otsdb"
    assert r["message"] == "sys.cpu.user"
    assert r["labels"] == {"host": "web01"}
    assert r["value"] == 12.5
    assert r["ts"] == 1700000000.0
    # second series carries its own tags
    web02 = [x for x in records if x["labels"] == {"host": "web02"}]
    assert len(web02) == 1
    assert web02[0]["value"] == 5.0


def test_callable_tuple_transport_compatibility() -> None:
    calls: list[tuple[str, str, object]] = []

    def transport(
        method: str, url: str, **kwargs: object
    ) -> tuple[int, object]:
        calls.append((method, url, kwargs.get("json")))
        return 200, DPS_PAYLOAD

    src = OpenTsdbSource(
        {"base_url": "https://tsdb.example.com"},
        transport=transport,
    )

    records = src.query({"start": "1h-ago", "metric": "sys.cpu.user"})

    assert len(records) == 4
    assert calls[0][0:2] == (
        "POST",
        "https://tsdb.example.com/api/query",
    )
    assert calls[0][2] == {
        "start": "1h-ago",
        "queries": [{"metric": "sys.cpu.user", "aggregator": "sum"}],
    }


def test_query_posts_to_api_query_with_body() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    _src(t).query({"start": "1h-ago", "end": "now", "metric": "sys.cpu.user", "tags": {"host": "web01"}})
    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/query")
    body = _body_json(t)
    assert body["start"] == "1h-ago"
    assert body["end"] == "now"
    assert body["queries"] == [
        {"metric": "sys.cpu.user", "aggregator": "sum", "tags": {"host": "web01"}}
    ]
    assert call["headers"].get("Content-Type") == "application/json"


def test_explicit_queries_list_passed_through() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    queries = [{"metric": "m1", "aggregator": "avg"}, {"metric": "m2", "aggregator": "max"}]
    _src(t).query({"start": 1700000000, "queries": queries})
    assert _body_json(t)["queries"] == queries


def test_custom_default_aggregator() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    _src(t, aggregator="avg").query({"start": "1h-ago", "metric": "m"})
    assert _body_json(t)["queries"][0]["aggregator"] == "avg"


def test_empty_results_returns_empty() -> None:
    assert _src(FakeTransport(200, "[]")).query({"start": "1h-ago", "metric": "m"}) == []


def test_error_object_returns_empty() -> None:
    t = FakeTransport(200, json.dumps(ERROR_PAYLOAD))
    assert _src(t).query({"start": "1h-ago", "metric": "m"}) == []


def test_http_error_returns_empty() -> None:
    assert _src(FakeTransport(500, "err")).query({"start": "1h-ago", "metric": "m"}) == []


def test_malformed_json_returns_empty() -> None:
    assert _src(FakeTransport(200, "}{")).query({"start": "1h-ago", "metric": "m"}) == []


def test_missing_start_and_queries_no_request() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    assert _src(t).query({"metric": "m"}) == []
    assert t.calls == []


def test_transport_exception_returns_empty() -> None:
    t = FakeTransport(raises=RuntimeError("boom"))
    assert _src(t).query({"start": "1h-ago", "metric": "m"}) == []


def test_basic_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TSDB_USER", "bob")
    monkeypatch.setenv("TSDB_PASS", "hunter2")
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    _src(t, username_env="TSDB_USER", password_env="TSDB_PASS").query(
        {"start": "1h-ago", "metric": "m"}
    )
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth.startswith("Basic ")
    assert "hunter2" not in auth


def test_no_auth_without_env() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    _src(t).query({"start": "1h-ago", "metric": "m"})
    assert "Authorization" not in t.calls[0]["headers"]


def test_health_ok() -> None:
    t = FakeTransport(200, '{"version": "2.4.0"}')
    h = _src(t).health()
    assert h["ok"] is True
    assert t.calls[0]["url"].endswith("/api/version")


def test_health_not_ok() -> None:
    h = _src(FakeTransport(503, "down")).health()
    assert h["ok"] is False
    assert "http status 503" in h["detail"]


def test_health_never_raises() -> None:
    h = _src(FakeTransport(raises=ConnectionError("refused"))).health()
    assert h["ok"] is False
    assert "detail" in h


def test_timeout_passed_through() -> None:
    t = FakeTransport(200, json.dumps(DPS_PAYLOAD))
    _src(t, timeout=7.0).query({"start": "1h-ago", "metric": "m"})
    assert t.calls[0]["timeout"] == 7.0
