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


def test_callable_transport_and_query_alias_compatibility() -> None:
    calls: list[tuple[str, str]] = []

    def transport(method: str, url: str, **_: object) -> tuple[int, object]:
        calls.append((method, url))
        return 200, CANNED

    src = GraphiteSource(CONFIG, transport, environ={})

    records = src.query({"query": "servers.web1.cpu"})

    assert len(records) == 3
    assert records[0]["message"] == "servers.web1.cpu"
    assert calls == [("GET", "https://graphite.example.com/render")]


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


def test_nan_inf_datapoint_value_sanitized_to_none() -> None:
    # A datapoint whose value is NaN/Inf must normalize to value is None
    # (unified sanitize_metric_value policy), and must not raise.
    payload = [
        {
            "target": "servers.web1.cpu",
            "datapoints": [
                [float("nan"), 1700000000],
                [float("inf"), 1700000060],
                [float("-inf"), 1700000120],
                [0.0, 1700000180],
            ],
        }
    ]
    src = GraphiteSource(CONFIG, _MockTransport(_Resp(200, payload)), environ={})
    records = src.query({"target": "servers.web1.cpu"})
    assert [r["value"] for r in records[:3]] == [None, None, None]
    # 0.0 is a real sample, never forged to None.
    assert records[3]["value"] == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.10.10.10",
        "http://172.31.0.1",
        "http://192.168.100.1",
        "http://169.254.169.254",
        # Alibaba cloud-metadata endpoint — coverage gained by delegating to the
        # canonical is_url_blocked (the old bespoke guard missed this address).
        "http://100.100.100.200/",
        "file:///etc/passwd",
    ],
)
def test_internal_base_url_rejected(bad: str) -> None:
    with pytest.raises(ConnectorConfigError):
        GraphiteSource({**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={})


@pytest.mark.parametrize(
    "bad",
    [
        "http://metadata.google.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://METADATA.GOOGLE.INTERNAL/",
        "http://metadata/",
        "http://metadata",
    ],
)
def test_metadata_hostname_rejected(bad: str) -> None:
    # DNS name for the cloud metadata endpoint must be refused even though no
    # DNS resolution is performed (the 169.254.169.254 IP literal is covered by
    # test_internal_base_url_rejected).
    with pytest.raises(ConnectorConfigError):
        GraphiteSource({**CONFIG, "base_url": bad}, _MockTransport(_Resp(200, CANNED)), environ={})


def test_metadata_lookalike_hostname_allowed() -> None:
    # Exact-match only: a benign host that merely contains "metadata" is fine.
    src = GraphiteSource(
        {**CONFIG, "base_url": "https://metadata.example.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={},
    )
    assert src.base_url == "https://metadata.example.com"


def test_public_base_url_allowed() -> None:
    src = GraphiteSource(
        {**CONFIG, "base_url": "https://graphite.example.com"},
        _MockTransport(_Resp(200, CANNED)),
        environ={},
    )
    assert src.base_url == "https://graphite.example.com"


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
