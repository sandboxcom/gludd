"""Unit tests for VictoriaMetricsSource (mocked transport, no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.victoriametrics import (
    KIND,
    SSRFError,
    VictoriaMetricsSource,
)

# --- canned Prometheus-API payloads ---------------------------------------

VECTOR_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "up", "job": "node", "instance": "h1:9100"},
                "value": [1700000000, "1"],
            },
            {
                "metric": {"__name__": "up", "job": "node", "instance": "h2:9100"},
                "value": [1700000000, "0"],
            },
        ],
    },
}

MATRIX_PAYLOAD = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"__name__": "rate_x", "job": "api"},
                "values": [
                    [1700000000, "0.5"],
                    [1700000060, "0.7"],
                    [1700000120, "0.9"],
                ],
            }
        ],
    },
}

ERROR_PAYLOAD = {"status": "error", "errorType": "bad_data", "error": "parse error"}
EMPTY_PAYLOAD = {"status": "success", "data": {"resultType": "vector", "result": []}}


class FakeTransport:
    """Records the last request and returns a scripted (status, body)."""

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


def _src(transport: FakeTransport, **extra: Any) -> VictoriaMetricsSource:
    config = {"name": "vm", "base_url": "https://vm.example.com", **extra}
    return VictoriaMetricsSource(config, transport=transport)


# --- contract / metadata ---------------------------------------------------

def test_kind_class_and_module_attr() -> None:
    assert KIND == "metrics"
    assert VictoriaMetricsSource.KIND == "metrics"


def test_name_attribute() -> None:
    src = _src(FakeTransport(200, "{}"))
    assert src.name == "vm"


def test_base_url_trailing_slash_stripped() -> None:
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com/"}, transport=FakeTransport(200, "{}")
    )
    assert src.base_url == "https://vm.example.com"


# --- SSRF guard ------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8428",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://169.254.169.254",  # cloud metadata
        "http://localhost:8428",
        "http://[::1]:8428",
    ],
)
def test_private_host_rejected_by_default(url: str) -> None:
    with pytest.raises(SSRFError):
        VictoriaMetricsSource({"base_url": url}, transport=FakeTransport())


def test_private_host_allowed_when_opt_in() -> None:
    src = VictoriaMetricsSource(
        {"base_url": "http://127.0.0.1:8428", "allow_private": True},
        transport=FakeTransport(200, "{}"),
    )
    assert src.base_url == "http://127.0.0.1:8428"


def test_bad_scheme_rejected() -> None:
    with pytest.raises(SSRFError):
        VictoriaMetricsSource({"base_url": "file:///etc/passwd"}, transport=FakeTransport())


# --- query: instant vector -------------------------------------------------

def test_query_instant_vector_normalizes() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    src = _src(t)
    records = src.query({"query": "up"})
    assert len(records) == 2
    first = records[0]
    assert first["kind"] == "metrics"
    assert first["source"] == "vm"
    assert first["message"] == "up"
    assert first["value"] == 1.0
    assert first["ts"] == 1700000000.0
    assert first["labels"] == {"job": "node", "instance": "h1:9100"}
    assert "__name__" not in first["labels"]
    assert set(first) == {
        "ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"
    }


def test_query_instant_hits_query_endpoint() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t).query({"query": "up"})
    assert "/api/v1/query?" in t.calls[0]["url"]
    assert "query_range" not in t.calls[0]["url"]
    assert "query=up" in t.calls[0]["url"]


# --- query: range matrix ---------------------------------------------------

def test_query_range_matrix_one_record_per_point() -> None:
    t = FakeTransport(200, json.dumps(MATRIX_PAYLOAD))
    src = _src(t)
    records = src.query({"query": "rate_x", "start": 1700000000, "end": 1700000120, "step": 60})
    assert len(records) == 3
    assert [r["value"] for r in records] == [0.5, 0.7, 0.9]
    assert all(r["message"] == "rate_x" for r in records)
    assert "/api/v1/query_range?" in t.calls[0]["url"]


# --- empty / error / transport failures ------------------------------------

def test_empty_result_returns_empty_list() -> None:
    t = FakeTransport(200, json.dumps(EMPTY_PAYLOAD))
    assert _src(t).query({"query": "up"}) == []


def test_error_status_payload_returns_empty() -> None:
    t = FakeTransport(200, json.dumps(ERROR_PAYLOAD))
    assert _src(t).query({"query": "up"}) == []


def test_http_error_status_returns_empty() -> None:
    t = FakeTransport(500, "internal error")
    assert _src(t).query({"query": "up"}) == []


def test_malformed_json_returns_empty() -> None:
    t = FakeTransport(200, "{not json")
    assert _src(t).query({"query": "up"}) == []


def test_missing_query_returns_empty_without_request() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    assert _src(t).query({}) == []
    assert t.calls == []


def test_transport_exception_query_returns_empty() -> None:
    t = FakeTransport(raises=RuntimeError("boom"))
    assert _src(t).query({"query": "up"}) == []


# --- auth from env ---------------------------------------------------------

def test_basic_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VM_USER", "alice")
    monkeypatch.setenv("VM_PASS", "s3cret")
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    src = _src(t, username_env="VM_USER", password_env="VM_PASS")
    src.query({"query": "up"})
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth.startswith("Basic ")
    # secret value is base64, not plaintext, and never the env var name
    assert "s3cret" not in auth
    assert "VM_PASS" not in auth


def test_no_auth_header_without_env() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    _src(t).query({"query": "up"})
    assert "Authorization" not in t.calls[0]["headers"]


# --- health: ok / not-ok / never-raises ------------------------------------

def test_health_ok() -> None:
    t = FakeTransport(200, json.dumps(EMPTY_PAYLOAD))
    h = _src(t).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_bad_status() -> None:
    t = FakeTransport(503, "down")
    h = _src(t).health()
    assert h["ok"] is False
    assert "503" in h["detail"]


def test_health_never_raises_on_transport_error() -> None:
    t = FakeTransport(raises=ConnectionError("refused"))
    h = _src(t).health()
    assert h["ok"] is False
    assert "detail" in h


def test_timeout_is_passed_through() -> None:
    t = FakeTransport(200, json.dumps(VECTOR_PAYLOAD))
    src = _src(t, timeout=5.0)
    src.query({"query": "up"})
    assert t.calls[0]["timeout"] == 5.0
