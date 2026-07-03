"""Unit tests for the self-contained Pyroscope continuous-profiling connector.

Drives an *injected* mock transport — no real HTTP, no DNS, no shell.

The connector must:
- GET ``{base_url}/render?query=&from=&until=&format=json`` and decode the
  Pyroscope *flamebearer* JSON;
- emit one normalized record per frame, ``value`` = self/total samples, with
  ``message`` = frame name and ``labels`` = {app, profile_type};
- read its bearer secret from the env var *named* by ``token_env``;
- block literal internal/private ``base_url`` hosts unless ``allow_private``;
- expose ``health()`` returning ``{'ok', 'detail'}`` that never raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.pyroscope import PyroscopeSource

# --------------------------------------------------------------------------- #
# Mock transport
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _MockTransport:
    """Injectable transport: ``get(url, *, params, headers, timeout)``."""

    def __init__(self, response: _Resp | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _Resp:
        self.calls.append(
            {
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


# A canned Pyroscope flamebearer payload.
#   flamebearer.names  — frame name table
#   flamebearer.levels — flattened [..., self, total, name_index, ...] quads
#   metadata           — app + profile units
_CANNED_FLAMEBEARER: dict[str, Any] = {
    "flamebearer": {
        "names": [
            "total",
            "main.run",
            "encoding/json.Marshal",
            "runtime.mallocgc",
        ],
        # levels[i] = [offset, total, self, name_index, ...]
        "levels": [
            [0, 300, 0, 0],
            [0, 300, 50, 1],
            [0, 180, 80, 2, 0, 70, 70, 3],
        ],
        "numTicks": 300,
        "maxSelf": 80,
    },
    "metadata": {
        "appName": "checkout-service.cpu",
        "name": "checkout-service.cpu",
        "units": "samples",
        "sampleRate": 100,
    },
}


def _source(transport: _MockTransport, **overrides: Any) -> PyroscopeSource:
    config: dict[str, Any] = {
        "name": "pyroscope-prod",
        "base_url": "https://pyroscope.example.com",
        "token_env": "PYROSCOPE_TOKEN",
        "query": "checkout-service.cpu",
    }
    config.update(overrides)
    return PyroscopeSource(config, transport=transport)


# --------------------------------------------------------------------------- #
# Contract / identity
# --------------------------------------------------------------------------- #


def test_kind_is_traces_class_attr() -> None:
    assert PyroscopeSource.KIND == "traces"


def test_name_from_config() -> None:
    src = _source(_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)))
    assert src.name == "pyroscope-prod"


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_query_normalizes_records() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    records = src.query({"from": 1718000000.0, "until": 1718000100.0})

    assert isinstance(records, list)
    assert records, "expected at least one frame record"
    rec = records[0]
    for key in (
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    ):
        assert key in rec
    assert rec["source"] == "pyroscope-prod"
    assert rec["kind"] == "traces"


def test_value_is_self_samples_per_frame() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    records = src.query({})

    by_name = {r["message"]: r for r in records}
    # self samples from the levels table.
    assert by_name["main.run"]["value"] == 50.0
    assert by_name["encoding/json.Marshal"]["value"] == 80.0
    assert by_name["runtime.mallocgc"]["value"] == 70.0
    # The synthetic "total" root (self=0) should not be emitted.
    assert "total" not in by_name


def test_message_is_frame_name_and_labels_populated() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    records = src.query({})

    top = max(records, key=lambda r: r["value"])
    assert top["message"] == "encoding/json.Marshal"
    assert top["labels"]["app"] == "checkout-service.cpu"
    assert top["labels"]["profile_type"] == "checkout-service.cpu"


def test_raw_preserved() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    records = src.query({})
    assert records[0]["raw"] is not None


def test_query_hits_render_endpoint_with_params() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    src.query({"from": 111, "until": 222})

    call = transport.calls[0]
    assert call["url"].endswith("/render")
    assert call["url"].startswith("https://pyroscope.example.com")
    assert call["params"]["query"] == "checkout-service.cpu"
    assert call["params"]["format"] == "json"
    assert call["params"]["from"] == 111
    assert call["params"]["until"] == 222


def test_query_is_time_bound() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport, timeout=9.0)
    src.query({})
    assert transport.calls[0]["timeout"] == 9.0


def test_empty_flamebearer_yields_no_records() -> None:
    payload = {"flamebearer": {"names": ["total"], "levels": [[0, 0, 0, 0]]}, "metadata": {}}
    transport = _MockTransport(_Resp(200, payload))
    src = _source(transport)
    assert src.query({}) == []


# --------------------------------------------------------------------------- #
# Auth from env
# --------------------------------------------------------------------------- #


def test_bearer_token_read_from_named_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYROSCOPE_TOKEN", "tok-123")
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    src.query({})
    assert transport.calls[0]["headers"].get("Authorization") == "Bearer tok-123"


def test_no_auth_header_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYROSCOPE_TOKEN", raising=False)
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    src.query({})
    assert "Authorization" not in transport.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #


def test_internal_base_url_rejected() -> None:
    with pytest.raises(ValueError):
        PyroscopeSource(
            {"name": "p", "base_url": "http://192.168.1.10:4040", "query": "x"},
            transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
        )


def test_localhost_rejected() -> None:
    with pytest.raises(ValueError):
        PyroscopeSource(
            {"name": "p", "base_url": "http://localhost:4040", "query": "x"},
            transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
        )


def test_metadata_ip_rejected() -> None:
    with pytest.raises(ValueError):
        PyroscopeSource(
            {"name": "p", "base_url": "http://169.254.169.254/", "query": "x"},
            transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
        )


def test_private_allowed_with_opt_in() -> None:
    src = PyroscopeSource(
        {
            "name": "p",
            "base_url": "http://192.168.1.10:4040",
            "query": "x",
            "allow_private": True,
        },
        transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
    )
    assert src.name == "p"


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        PyroscopeSource(
            {"name": "p", "base_url": "ftp://host/x", "query": "x", "allow_private": True},
            transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
        )


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",  # Alibaba/Azure metadata (canonical guard catches it)
        "http://metadata.goog/",  # GCP alias missed by the old per-connector blocklist
        "http://foo.localhost/",  # RFC 6761 .localhost TLD missed by the old guard
    ],
)
def test_canonical_guard_refuses_internal_hosts(bad_url: str) -> None:
    """Default config refuses every loopback/metadata alias via is_url_blocked."""
    with pytest.raises(ValueError):
        PyroscopeSource(
            {"name": "p", "base_url": bad_url, "query": "x"},
            transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
        )


def test_public_base_url_constructs() -> None:
    src = PyroscopeSource(
        {"name": "p", "base_url": "https://pyroscope.example.com", "query": "x"},
        transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
    )
    assert src.name == "p"


def test_localhost_allowed_with_opt_in() -> None:
    src = PyroscopeSource(
        {"name": "p", "base_url": "http://localhost:4040", "query": "x", "allow_private": True},
        transport=_MockTransport(_Resp(200, _CANNED_FLAMEBEARER)),
    )
    assert src.name == "p"


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #


def test_health_ok() -> None:
    transport = _MockTransport(_Resp(200, _CANNED_FLAMEBEARER))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_http_error() -> None:
    transport = _MockTransport(_Resp(500, {"error": "down"}))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception() -> None:
    transport = _MockTransport(RuntimeError("dns failure"))
    src = _source(transport)
    h = src.health()
    assert h["ok"] is False
    assert "dns failure" in h["detail"]


def test_query_returns_empty_on_transport_exception() -> None:
    transport = _MockTransport(RuntimeError("boom"))
    src = _source(transport)
    assert src.query({}) == []
