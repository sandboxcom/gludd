"""Unit tests for ThanosSource (mocked transport, no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.thanos import KIND, SSRFError, ThanosSource

VECTOR_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "http_requests", "code": "200"},
                "value": [1700000000, "42"],
            }
        ],
    },
}

MATRIX_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"__name__": "cpu", "node": "n1"},
                "values": [[1700000000, "1.0"], [1700000060, "2.0"]],
            }
        ],
    },
}

ERROR_PAYLOAD = {"status": "error", "error": "store unavailable"}
EMPTY_PAYLOAD = {"status": "success", "data": {"resultType": "vector", "result": []}}


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


def _src(transport: FakeTransport, **extra: Any) -> ThanosSource:
    config = {"name": "thanos", "base_url": "https://thanos.example.com", **extra}
    return ThanosSource(config, transport=transport)


def test_kind_attrs() -> None:
    assert KIND == "metrics"
    assert ThanosSource.KIND == "metrics"


def test_name_attr() -> None:
    assert _src(FakeTransport(200, "{}")).name == "thanos"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:9090", "http://10.1.2.3", "http://localhost", "http://[::1]"],
)
def test_private_host_rejected(url: str) -> None:
    with pytest.raises(SSRFError):
        ThanosSource({"base_url": url}, transport=FakeTransport())


def test_private_host_allowed_opt_in() -> None:
    src = ThanosSource(
        {"base_url": "http://10.1.2.3", "allow_private": True},
        transport=FakeTransport(200, "{}"),
    )
    assert src.base_url == "http://10.1.2.3"


def test_query_instant_vector_normalized() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    records = _src(t).query({"query": "http_requests"})
    assert len(records) == 1
    r = records[0]
    assert r["kind"] == "metrics"
    assert r["source"] == "thanos"
    assert r["message"] == "http_requests"
    assert r["value"] == 42.0
    assert r["ts"] == 1700000000.0
    assert r["labels"] == {"code": "200"}
    assert set(r) == {
        "ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"
    }
    assert "/api/v1/query?" in t.calls[0]["url"]


def test_query_range_matrix() -> None:
    t = FakeTransport(200, json.dumps(MATRIX_PAYLOAD))
    records = _src(t).query({"query": "cpu", "start": 1, "end": 2, "step": 60})
    assert [r["value"] for r in records] == [1.0, 2.0]
    assert "/api/v1/query_range?" in t.calls[0]["url"]


def test_dedup_and_partial_response_params_forwarded() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t).query({"query": "cpu", "dedup": True, "partial_response": False})
    url = t.calls[0]["url"]
    assert "dedup=true" in url
    assert "partial_response=false" in url


def test_config_level_dedup_default_applied() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t, dedup=True).query({"query": "cpu"})
    assert "dedup=true" in t.calls[0]["url"]


def test_no_thanos_params_when_unset() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t).query({"query": "cpu"})
    url = t.calls[0]["url"]
    assert "dedup" not in url
    assert "partial_response" not in url


def test_empty_result_returns_empty() -> None:
    t = FakeTransport(200, json.dumps(EMPTY_PAYLOAD))
    assert _src(t).query({"query": "cpu"}) == []


def test_error_payload_returns_empty() -> None:
    t = FakeTransport(200, json.dumps(ERROR_PAYLOAD))
    assert _src(t).query({"query": "cpu"}) == []


def test_http_error_returns_empty() -> None:
    assert _src(FakeTransport(502, "bad gateway")).query({"query": "cpu"}) == []


def test_malformed_json_returns_empty() -> None:
    assert _src(FakeTransport(200, "<<<")).query({"query": "cpu"}) == []


def test_missing_query_no_request() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    assert _src(t).query({}) == []
    assert t.calls == []


def test_transport_exception_returns_empty() -> None:
    assert _src(FakeTransport(raises=RuntimeError("x"))).query({"query": "cpu"}) == []


def test_bearer_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THANOS_TOKEN", "tok-abc-123")
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t, token_env="THANOS_TOKEN").query({"query": "cpu"})
    assert t.calls[0]["headers"].get("Authorization") == "Bearer tok-abc-123"


def test_no_auth_without_token_env() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t).query({"query": "cpu"})
    assert "Authorization" not in t.calls[0]["headers"]


def test_health_ok() -> None:
    assert _src(FakeTransport(200, json.dumps(EMPTY_PAYLOAD))).health()["ok"] is True


def test_health_not_ok() -> None:
    h = _src(FakeTransport(500, "err")).health()
    assert h["ok"] is False
    assert "500" in h["detail"]


def test_health_never_raises() -> None:
    h = _src(FakeTransport(raises=ConnectionError("refused"))).health()
    assert h["ok"] is False
    assert "detail" in h


def test_timeout_passed_through() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t, timeout=3.0).query({"query": "cpu"})
    assert t.calls[0]["timeout"] == 3.0
