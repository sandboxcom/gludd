"""Unit tests for the self-contained Grafana Loki observability connector.

Transport is fully mocked: no real network I/O. We assert
- query() normalization (ns timestamp -> s, stream labels, detected level),
- health() ok / not-ok dicts (never raises),
- SSRF literal-host blocking on internal/loopback/private/metadata hosts.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.grafana_loki import GrafanaLokiSource


class _StubTransport:
    """Injectable HTTP transport double recording calls + queued responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: list[tuple[int, Any]] = []
        self.raise_exc: Exception | None = None

    def queue(self, status_code: int, json_body: Any) -> None:
        self._responses.append((status_code, json_body))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        if not self._responses:
            return (200, {})
        return self._responses.pop(0)


def _loki_payload() -> dict[str, Any]:
    """A canned Loki query_range payload with one stream and two entries."""
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "app": "checkout",
                        "level": "error",
                        "namespace": "prod",
                    },
                    "values": [
                        ["1700000000000000000", "payment gateway timeout"],
                        ["1700000001500000000", "retrying charge"],
                    ],
                }
            ],
        },
    }


@pytest.fixture()
def source() -> tuple[GrafanaLokiSource, _StubTransport]:
    transport = _StubTransport()
    src = GrafanaLokiSource(
        config={"base_url": "https://loki.example.com", "token_env": "LOKI_TOKEN"},
        transport=transport,
    )
    return src, transport


def test_kind_and_name(source: tuple[GrafanaLokiSource, _StubTransport]) -> None:
    src, _ = source
    assert src.KIND == "logs"
    assert src.name == "grafana_loki"


def test_query_normalizes_stream_entries(
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[GrafanaLokiSource, _StubTransport],
) -> None:
    src, transport = source
    monkeypatch.setenv("LOKI_TOKEN", "secret-token")
    transport.queue(200, _loki_payload())

    records = src.query(
        {"query": '{app="checkout"}', "start": 1_700_000_000, "end": 1_700_000_010}
    )

    assert isinstance(records, list)
    assert len(records) == 2

    first = records[0]
    # ns -> s conversion.
    assert first["ts"] == pytest.approx(1_700_000_000.0)
    assert first["source"] == "grafana_loki"
    assert first["kind"] == "logs"
    # detected level from stream labels.
    assert first["level_or_status"] == "error"
    # log line is the message.
    assert first["message"] == "payment gateway timeout"
    # stream labels propagated.
    assert first["labels"]["app"] == "checkout"
    assert first["labels"]["namespace"] == "prod"
    # raw is the original [ts, line] entry.
    assert first["raw"][1] == "payment gateway timeout"
    # value present (None or numeric is acceptable; key must exist).
    assert "value" in first

    second = records[1]
    assert second["ts"] == pytest.approx(1_700_000_001.5)
    assert second["message"] == "retrying charge"


def test_query_hits_range_endpoint_with_params_and_auth(
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[GrafanaLokiSource, _StubTransport],
) -> None:
    src, transport = source
    monkeypatch.setenv("LOKI_TOKEN", "secret-token")
    transport.queue(200, _loki_payload())

    src.query(
        {"query": '{app="checkout"}', "start": 1_700_000_000, "end": 1_700_000_010, "limit": 50}
    )

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/loki/api/v1/query_range")
    params = call["params"] or {}
    assert params["query"] == '{app="checkout"}'
    assert "start" in params and "end" in params
    assert str(params["limit"]) == "50"
    # auth from env.
    auth_values = " ".join(call["headers"].values())
    assert "secret-token" in auth_values
    assert call["timeout"] is not None


def test_health_ok(
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[GrafanaLokiSource, _StubTransport],
) -> None:
    src, transport = source
    monkeypatch.setenv("LOKI_TOKEN", "secret-token")
    transport.queue(200, "ready")

    h = src.health()
    assert h["ok"] is True
    assert h["source"] == "grafana_loki"


def test_health_not_ok_on_bad_status(
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[GrafanaLokiSource, _StubTransport],
) -> None:
    src, transport = source
    monkeypatch.setenv("LOKI_TOKEN", "secret-token")
    transport.queue(500, "not ready")

    h = src.health()
    assert h["ok"] is False


def test_health_never_raises(
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[GrafanaLokiSource, _StubTransport],
) -> None:
    src, transport = source
    monkeypatch.setenv("LOKI_TOKEN", "secret-token")
    transport.raise_exc = TimeoutError("slow")

    h = src.health()  # must not raise
    assert h["ok"] is False
    assert "slow" in str(h.get("error", ""))


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1:3100",
        "http://localhost:3100",
        "http://0.0.0.0:3100",
        "http://[::1]:3100",
        "http://10.1.2.3",
        "http://192.168.0.1",
        "http://172.31.255.1",
        "http://169.254.169.254",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    transport = _StubTransport()
    with pytest.raises(ValueError):
        GrafanaLokiSource(
            config={"base_url": bad_url, "token_env": "LOKI_TOKEN"},
            transport=transport,
        )


def test_public_base_url_accepted() -> None:
    transport = _StubTransport()
    src = GrafanaLokiSource(
        config={"base_url": "https://loki.acme.io", "token_env": "X"},
        transport=transport,
    )
    assert src.name == "grafana_loki"
