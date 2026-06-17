"""Unit tests for GraphiteSource — mocked transport, no network."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.graphite import ConnectorConfigError, GraphiteSource


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _MockTransport:
    def __init__(self, response: _Resp | None = None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        return self.response


CANNED = [
    {
        "target": "servers.web1.cpu",
        "datapoints": [
            [0.5, 1700000000],
            [0.7, 1700000060],
            [None, 1700000120],
        ],
    }
]

CONFIG = {"name": "gr", "base_url": "https://graphite.example.com"}


def test_kind_is_metrics() -> None:
    assert GraphiteSource.KIND == "metrics"


def test_render_request_shape() -> None:
    t = _MockTransport(_Resp(200, CANNED))
    src = GraphiteSource(CONFIG, t, environ={})
    src.query({"target": "servers.web1.cpu", "from": "-1h"})
    call = t.calls[-1]
    assert call["method"] == "GET"
    assert call["url"].endswith("/render")
    assert call["params"]["target"] == "servers.web1.cpu"
    assert call["params"]["format"] == "json"
    assert call["params"]["from"] == "-1h"


def test_optional_auth_header_from_env() -> None:
    t = _MockTransport(_Resp(200, CANNED))
    src = GraphiteSource({**CONFIG, "token_env": "GRAPHITE_TOKEN"}, t, environ={"GRAPHITE_TOKEN": "abc"})
    src.query({"target": "x"})
    assert t.calls[-1]["headers"]["Authorization"] == "Bearer abc"


def test_configured_token_env_must_resolve() -> None:
    with pytest.raises(ConnectorConfigError):
        GraphiteSource({**CONFIG, "token_env": "GRAPHITE_TOKEN"}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_missing_base_url_rejected() -> None:
    with pytest.raises(ConnectorConfigError):
        GraphiteSource({"name": "gr"}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_missing_target_rejected() -> None:
    src = GraphiteSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={})
    with pytest.raises(ConnectorConfigError):
        src.query({})


def test_normalization_each_datapoint() -> None:
    src = GraphiteSource(CONFIG, _MockTransport(_Resp(200, CANNED)), environ={})
    records = src.query({"target": "servers.web1.cpu"})
    assert len(records) == 3
    first = records[0]
    assert first["kind"] == "metrics"
    assert first["value"] == 0.5
    assert first["ts"] == 1700000000
    assert first["message"] == "servers.web1.cpu"
    assert first["labels"] == {"target": "servers.web1.cpu"}
    assert first["source"] == "gr"
    # null datapoint value passes through as None
    assert records[2]["value"] is None
    assert records[2]["ts"] == 1700000120


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.10.10.10",
        "http://172.31.0.1",
        "http://192.168.100.1",
        "http://169.254.169.254",
        "file:///etc/passwd",
    ],
)
def test_internal_base_url_rejected(bad: str) -> None:
    with pytest.raises(ConnectorConfigError):
        GraphiteSource({**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_health_ok() -> None:
    src = GraphiteSource(CONFIG, _MockTransport(_Resp(200, [])), environ={})
    assert src.health()["ok"] is True


def test_health_not_ok() -> None:
    src = GraphiteSource(CONFIG, _MockTransport(_Resp(500, [])), environ={})
    assert src.health()["ok"] is False


def test_health_never_raises() -> None:
    src = GraphiteSource(CONFIG, _MockTransport(exc=OSError("net")), environ={})
    h = src.health()
    assert h["ok"] is False
    assert "net" in h["detail"]
