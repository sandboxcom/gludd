"""Unit tests for the VictoriaMetrics connector (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.victoriametrics import (
    KIND,
    SSRFError,
    VictoriaMetricsSource,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def _instant_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "instance": "web01:9090", "job": "node"},
                    "value": [1700000000, "1"],
                },
                {
                    "metric": {"__name__": "up", "instance": "web02:9090", "job": "node"},
                    "value": [1700000000, "0"],
                },
            ],
        },
    }


def _range_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "cpu", "instance": "web01"},
                    "values": [[1700000000, "0.5"], [1700000060, "0.7"]],
                }
            ],
        },
    }


def test_kind_and_name() -> None:
    src = VictoriaMetricsSource({"name": "vm-prod", "base_url": "https://vm.example.com"})
    assert src.KIND == "metrics"
    assert KIND == "metrics"
    assert src.name == "vm-prod"


def test_instant_query_normalized() -> None:
    transport = RecordingTransport(FakeResponse(200, _instant_payload()))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com"}, transport=transport
    )
    records = src.query({"query": "up"})

    assert len(records) == 2
    expected_keys = {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    for rec in records:
        assert set(rec) == expected_keys
        assert rec["kind"] == "metrics"
        assert rec["message"] == "up"  # __name__ becomes message
        assert "__name__" not in rec["labels"]

    assert records[0]["value"] == 1.0
    assert records[0]["ts"] == 1700000000.0
    assert records[0]["labels"]["instance"] == "web01:9090"
    assert records[1]["value"] == 0.0

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://vm.example.com/api/v1/query"
    assert call["params"]["query"] == "up"


def test_tuple_transport_compatibility() -> None:
    calls: list[tuple[str, str]] = []

    def transport(method: str, url: str, **_: object) -> tuple[int, object]:
        calls.append((method, url))
        return 200, _instant_payload()

    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com"},
        transport=transport,
    )

    records = src.query({"query": "up"})

    assert len(records) == 2
    assert records[0]["message"] == "up"
    assert calls == [("GET", "https://vm.example.com/api/v1/query")]


def test_range_query_uses_query_range_endpoint() -> None:
    transport = RecordingTransport(FakeResponse(200, _range_payload()))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com"}, transport=transport
    )
    records = src.query(
        {"query": "cpu", "start": 1700000000, "end": 1700000060, "step": "60s"}
    )
    assert len(records) == 2  # one series, two samples flattened
    assert records[0]["value"] == 0.5
    assert records[1]["value"] == 0.7
    assert records[0]["message"] == "cpu"

    call = transport.calls[0]
    assert call["url"] == "https://vm.example.com/api/v1/query_range"
    assert call["params"]["step"] == "60s"


def test_auth_token_from_env() -> None:
    transport = RecordingTransport(FakeResponse(200, _instant_payload()))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com", "token_env": "VM_TOKEN"},
        transport=transport,
        environ={"VM_TOKEN": "vmsecret"},
    )
    src.query({"query": "up"})
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer vmsecret"


def test_no_auth_when_env_missing() -> None:
    transport = RecordingTransport(FakeResponse(200, _instant_payload()))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com", "token_env": "MISSING"},
        transport=transport,
        environ={},
    )
    src.query({"query": "up"})
    assert "Authorization" not in transport.calls[0]["headers"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:8428",
        "https://10.0.0.1",
        "http://169.254.169.254",
        "https://192.168.0.10",
        "http://[::1]",
        # Loopback / metadata by *name* (not IP literal) must also be rejected.
        "http://localhost:8428",
        "https://ip6-localhost",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(SSRFError):
        VictoriaMetricsSource({"base_url": bad_url})


def test_public_host_allowed() -> None:
    VictoriaMetricsSource({"base_url": "https://vm.example.com"})


def test_health_ok() -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com"}, transport=transport
    )
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result
    assert transport.calls[0]["url"] == "https://vm.example.com/health"


def test_health_not_ok_on_bad_status() -> None:
    transport = RecordingTransport(FakeResponse(500, {}))
    src = VictoriaMetricsSource(
        {"base_url": "https://vm.example.com"}, transport=transport
    )
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "HTTP 500"


def test_health_never_raises_on_transport_error() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise OSError("socket error")

    src = VictoriaMetricsSource({"base_url": "https://vm.example.com"}, transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert "OSError" in result["detail"]
