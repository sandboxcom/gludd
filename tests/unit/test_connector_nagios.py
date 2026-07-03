"""Unit tests for the Nagios Core monitoring connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from general_ludd.connectors.nagios import NagiosSource

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Contract: http_get(url, params, headers, timeout) -> (status, json)."""

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError("transport called more times than responses queued")
        return self._responses.pop(0)


def servicelist_payload() -> dict[str, Any]:
    return {
        "data": {
            "servicelist": {
                "web01": {
                    "HTTP": {"status": 0, "last_check": 1718000000},
                    "SSL Cert": {"status": 1, "last_check": 1718000010},
                },
                "db01": {
                    "Replication": {"status": 2, "last_check": 1718000020},
                },
            }
        }
    }


def hostlist_payload() -> dict[str, Any]:
    return {
        "data": {
            "hostlist": {
                "web01": {"status": 0, "last_check": 1718000000},
                "db01": {"status": 1, "last_check": 1718000020},
            }
        }
    }


GOOD_URL = "https://nagios.example.com"


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_and_name_and_construction():
    src = NagiosSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    assert NagiosSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_constructs_with_default_transport():
    # No transport injected: the connector falls back to a real stdlib transport
    # so the registry's single-arg ``factory(config)`` build succeeds (the source
    # is NOT silently dropped from ``list_sources()``). No network at construction.
    from general_ludd.connectors.nagios import _default_http_get

    src = NagiosSource({"base_url": GOOD_URL})
    assert src._http_get is _default_http_get
    assert src.name


# ---------------------------------------------------------------------------
# Basic-auth from env
# ---------------------------------------------------------------------------


def test_basic_auth_from_env(monkeypatch):
    monkeypatch.setenv("NAGIOS_USER", "alice")
    monkeypatch.setenv("NAGIOS_PASS", "hunter2")
    t = RecordingTransport([(200, servicelist_payload())])
    src = NagiosSource(
        {
            "base_url": GOOD_URL,
            "username_env": "NAGIOS_USER",
            "password_env": "NAGIOS_PASS",
        },
        http_get=t,
    )
    src.query({"query": "servicelist"})
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
    assert decoded == "alice:hunter2"


def test_missing_basic_auth_env_is_tolerated(monkeypatch):
    monkeypatch.delenv("NAGIOS_USER", raising=False)
    monkeypatch.delenv("NAGIOS_PASS", raising=False)
    t = RecordingTransport([(200, hostlist_payload())])
    src = NagiosSource(
        {
            "base_url": GOOD_URL,
            "username_env": "NAGIOS_USER",
            "password_env": "NAGIOS_PASS",
        },
        http_get=t,
    )
    src.query({"query": "hostlist"})
    assert "Authorization" not in t.calls[0]["headers"]


def test_timeout_is_passed_to_transport():
    t = RecordingTransport([(200, hostlist_payload())])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t, timeout=7.0)
    src.query({"query": "hostlist"})
    assert t.calls[0]["timeout"] == 7.0


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://[::1]",
        "http://169.254.169.254/",
        "http://10.1.2.3",
        "http://192.168.0.1",
        "http://172.16.5.5",
        "http://0.0.0.0",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        NagiosSource({"base_url": bad_url}, http_get=RecordingTransport([]))


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        NagiosSource({"base_url": "ftp://nagios.example.com"}, http_get=RecordingTransport([]))


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/",
        "http://metadata.google.internal/",
        "http://169.254.169.254/",
        "http://100.100.100.200/",  # Alibaba metadata IP — canonical guard catches it
    ],
)
def test_canonical_metadata_hosts_rejected(bad_url):
    # Regression for task #40 (SSRF-guard consolidation): the private/metadata
    # decision delegates to security.ssrf.is_url_blocked, which adds the
    # Alibaba metadata IP the bespoke literal guard used to miss.
    with pytest.raises(ValueError):
        NagiosSource({"base_url": bad_url}, http_get=RecordingTransport([]))


# ---------------------------------------------------------------------------
# query() normalization — servicelist
# ---------------------------------------------------------------------------


def test_query_servicelist_normalizes_states():
    t = RecordingTransport([(200, servicelist_payload())])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"query": "servicelist"})

    assert t.calls[0]["url"] == f"{GOOD_URL}/nagios/cgi-bin/statusjson.cgi"
    assert t.calls[0]["params"]["query"] == "servicelist"
    assert len(records) == 3

    by_msg = {r["message"]: r for r in records}
    http = by_msg["web01/HTTP"]
    assert http["kind"] == "metrics"
    assert http["source"] == src.name
    assert http["level_or_status"] == "OK"
    assert http["value"] == 0.0
    assert http["labels"] == {"host": "web01", "service": "HTTP"}
    assert http["ts"] == 1718000000.0

    assert by_msg["web01/SSL Cert"]["level_or_status"] == "WARNING"
    assert by_msg["web01/SSL Cert"]["value"] == 1.0
    assert by_msg["db01/Replication"]["level_or_status"] == "CRITICAL"
    assert by_msg["db01/Replication"]["value"] == 2.0


def test_query_defaults_to_servicelist():
    t = RecordingTransport([(200, servicelist_payload())])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    src.query({})
    assert t.calls[0]["params"]["query"] == "servicelist"


# ---------------------------------------------------------------------------
# query() normalization — hostlist
# ---------------------------------------------------------------------------


def test_query_hostlist_normalizes_states():
    t = RecordingTransport([(200, hostlist_payload())])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"query": "hostlist"})

    assert len(records) == 2
    by_msg = {r["message"]: r for r in records}
    assert by_msg["web01"]["level_or_status"] == "OK"
    assert by_msg["web01"]["value"] == 0.0
    assert by_msg["web01"]["labels"] == {"host": "web01"}
    assert by_msg["db01"]["level_or_status"] == "DOWN"
    assert by_msg["db01"]["value"] == 1.0


# ---------------------------------------------------------------------------
# query() error handling
# ---------------------------------------------------------------------------


def test_query_unsupported_query_returns_error_record():
    src = NagiosSource({"base_url": GOOD_URL}, http_get=RecordingTransport([]))
    records = src.query({"query": "bogus"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_http_error_status_returns_error_record():
    t = RecordingTransport([(503, {"error": "service unavailable"})])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"query": "hostlist"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


def test_query_transport_exception_yields_error_record_not_raise():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("connection refused")

    src = NagiosSource({"base_url": GOOD_URL}, http_get=boom)
    records = src.query({"query": "hostlist"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok_returns_ok_and_detail():
    t = RecordingTransport([(200, hostlist_payload())])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_error_status():
    t = RecordingTransport([(401, {"error": "unauthorized"})])
    src = NagiosSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("dns/socket failure")

    src = NagiosSource({"base_url": GOOD_URL}, http_get=boom)
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["detail"]
