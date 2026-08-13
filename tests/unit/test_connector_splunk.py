"""Unit tests for the self-contained Splunk observability connector.

All HTTP is mocked -- no network, no real Splunk. We exercise:

* normalization of a canned oneshot results payload (``_time`` parse, ``_raw`` ->
  message, host/index/source labels, kind=logs, raw passthrough),
* health() OK on 200 and not-ok on 401 (and that it never raises),
* SSRF: internal/loopback/metadata base_url rejected at construction,
* auth header carries the Bearer token sourced from the named env var,
* query() sends a oneshot search POST with output_mode=json.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from general_ludd.connectors.splunk import SplunkSource, SSRFError


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeTransport:
    """Records calls and returns pre-seeded responses keyed by URL suffix."""

    def __init__(
        self,
        *,
        get_response: FakeResponse | None = None,
        post_response: FakeResponse | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.get_calls.append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        assert self._get_response is not None, "unexpected GET"
        return self._get_response

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.post_calls.append(
            {"url": url, "headers": headers, "data": data, "timeout": timeout}
        )
        assert self._post_response is not None, "unexpected POST"
        return self._post_response


# Canned oneshot search payload, in the shape Splunk's REST API returns.
ONESHOT_PAYLOAD: dict[str, Any] = {
    "results": [
        {
            "_time": "2026-06-12T08:30:00.000+00:00",
            "_raw": "user=alice action=login result=success",
            "level": "INFO",
            "index": "main",
            "host": "web-01",
            "source": "/var/log/app.log",
            "sourcetype": "app:json",
        },
        {
            # epoch-seconds string variant of _time, message under a different key
            "_time": "1749717000",
            "message": "disk usage high",
            "severity": "WARN",
            "index": "infra",
            "host": "db-02",
            "source": "/var/log/sys.log",
            "value": "92",
        },
    ],
    "preview": False,
}


BASE_URL = "https://splunk.example.com:8089"
GOOD_CONFIG = {"base_url": BASE_URL, "token_env": "SPLUNK_TOKEN"}
ENV = {"SPLUNK_TOKEN": "s3cr3t-hec-token"}


def _make_source(
    *,
    get_response: FakeResponse | None = None,
    post_response: FakeResponse | None = None,
    config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[SplunkSource, FakeTransport]:
    transport = FakeTransport(get_response=get_response, post_response=post_response)
    src = SplunkSource(
        config or GOOD_CONFIG,
        transport=transport,
        env=env if env is not None else dict(ENV),
    )
    return src, transport


# --------------------------------------------------------------------------- #
# Contract / construction
# --------------------------------------------------------------------------- #
def test_kind_is_logs() -> None:
    src, _ = _make_source()
    assert src.kind == "logs"
    assert SplunkSource.kind == "logs"


def test_name_defaults_to_host_and_is_overridable() -> None:
    src, _ = _make_source()
    assert src.name == "splunk.example.com"

    transport = FakeTransport()
    named = SplunkSource(GOOD_CONFIG, transport=transport, name="prod-splunk", env=dict(ENV))
    assert named.name == "prod-splunk"


def test_missing_config_fields_raise() -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        SplunkSource({"token_env": "X"}, transport=transport, env=dict(ENV))
    with pytest.raises(ValueError):
        SplunkSource({"base_url": BASE_URL}, transport=transport, env=dict(ENV))


# --------------------------------------------------------------------------- #
# SSRF: literal-host blocking, no DNS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost:8089",
        "http://127.0.0.1:8089",
        "https://10.0.0.5",
        "https://192.168.1.10:8089",
        "https://172.16.4.4",
        "http://169.254.169.254",  # cloud metadata
        "http://metadata.google.internal",
        "https://[::1]:8089",  # ipv6 loopback
        "https://[fd00::1]",  # ipv6 ULA private
        "http://0.0.0.0",
    ],
)
def test_ssrf_internal_base_url_rejected(bad_url: str) -> None:
    transport = FakeTransport()
    with pytest.raises(SSRFError):
        SplunkSource(
            {"base_url": bad_url, "token_env": "SPLUNK_TOKEN"},
            transport=transport,
            env=dict(ENV),
        )


def test_ssrf_rejects_non_http_scheme() -> None:
    transport = FakeTransport()
    with pytest.raises(SSRFError):
        SplunkSource(
            {"base_url": "ftp://splunk.example.com", "token_env": "SPLUNK_TOKEN"},
            transport=transport,
            env=dict(ENV),
        )


def test_ssrf_allows_public_host() -> None:
    # Public hostname must construct fine.
    src, _ = _make_source(config={"base_url": "https://splunk.example.com", "token_env": "SPLUNK_TOKEN"})
    assert src.name == "splunk.example.com"


def test_ssrf_does_not_resolve_dns() -> None:
    # A public-looking name that would resolve to a private IP must still be
    # ALLOWED -- we only inspect the literal host, never DNS. (Asserting we do
    # not over-block on names.)
    src, _ = _make_source(
        config={"base_url": "https://internal-looking.example.org", "token_env": "SPLUNK_TOKEN"}
    )
    assert src.name == "internal-looking.example.org"


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
def test_health_ok_on_200() -> None:
    src, transport = _make_source(get_response=FakeResponse(200, {"generator": {}}))
    result = src.health()
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["kind"] == "logs"
    # hits the server/info endpoint
    assert transport.get_calls[0]["url"].endswith("/services/server/info")


def test_health_not_ok_on_401_and_never_raises() -> None:
    src, _ = _make_source(get_response=FakeResponse(401, None))
    result = src.health()
    assert result["ok"] is False
    assert result["status_code"] == 401
    assert "auth" in result["error"].lower()


def test_health_never_raises_on_transport_error() -> None:
    class BoomTransport(FakeTransport):
        def get(self, *a: Any, **k: Any) -> FakeResponse:
            raise ConnectionError("network down")

    src = SplunkSource(GOOD_CONFIG, transport=BoomTransport(), env=dict(ENV))
    result = src.health()  # must not raise
    assert result["ok"] is False
    assert "error" in result


def test_health_uses_bearer_token_from_env() -> None:
    src, transport = _make_source(get_response=FakeResponse(200, {}))
    src.health()
    auth = transport.get_calls[0]["headers"]["Authorization"]
    assert auth == "Bearer s3cr3t-hec-token"


def test_missing_env_token_makes_health_not_ok() -> None:
    src = SplunkSource(GOOD_CONFIG, transport=FakeTransport(), env={})
    result = src.health()
    assert result["ok"] is False
    assert "error" in result


# --------------------------------------------------------------------------- #
# query() + normalization
# --------------------------------------------------------------------------- #
def test_query_sends_oneshot_search_post() -> None:
    src, transport = _make_source(post_response=FakeResponse(200, ONESHOT_PAYLOAD))
    src.query({"search": "search index=main", "earliest": "-15m", "latest": "now", "count": 100})
    call = transport.post_calls[0]
    assert call["url"].endswith("/services/search/jobs")
    assert call["data"]["output_mode"] == "json"
    assert call["data"]["exec_mode"] == "oneshot"
    assert call["data"]["search"] == "search index=main"
    assert call["data"]["earliest"] == "-15m"
    assert call["data"]["latest"] == "now"
    assert call["data"]["count"] == 100
    assert call["headers"]["Authorization"] == "Bearer s3cr3t-hec-token"


def test_callable_transport_compatibility() -> None:
    calls: list[dict[str, Any]] = []

    def transport(
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, object]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, ONESHOT_PAYLOAD

    src = SplunkSource(
        GOOD_CONFIG,
        transport=transport,
        env=dict(ENV),
    )

    records = src.query({"search": "search index=main"})

    assert len(records) == 2
    assert calls[0]["method"] == "POST"
    assert calls[0]["data"]["exec_mode"] == "oneshot"


def test_query_requires_search() -> None:
    src, _ = _make_source(post_response=FakeResponse(200, ONESHOT_PAYLOAD))
    with pytest.raises(ValueError):
        src.query({"earliest": "-15m"})


def test_query_normalizes_first_row() -> None:
    src, _ = _make_source(post_response=FakeResponse(200, ONESHOT_PAYLOAD))
    records = src.query({"search": "search index=main"})
    assert len(records) == 2

    rec = records[0]
    assert rec["kind"] == "logs"
    assert rec["source"] == "splunk.example.com"
    assert rec["level_or_status"] == "INFO"
    assert rec["message"] == "user=alice action=login result=success"
    assert rec["labels"]["index"] == "main"
    assert rec["labels"]["host"] == "web-01"
    assert rec["labels"]["source"] == "/var/log/app.log"
    # raw passthrough preserves the original row
    assert rec["raw"]["sourcetype"] == "app:json"

    # _time parsed to ISO-8601 UTC
    parsed = dt.datetime.fromisoformat(rec["ts"])
    assert parsed == dt.datetime(2026, 6, 12, 8, 30, tzinfo=dt.UTC)


def test_query_normalizes_epoch_time_and_alt_keys() -> None:
    src, _ = _make_source(post_response=FakeResponse(200, ONESHOT_PAYLOAD))
    records = src.query({"search": "search index=infra"})
    rec = records[1]
    # epoch-seconds string parsed
    parsed = dt.datetime.fromisoformat(rec["ts"])
    assert parsed == dt.datetime.fromtimestamp(1749717000, tz=dt.UTC)
    # message falls back to `message` key when `_raw` absent
    assert rec["message"] == "disk usage high"
    # level falls back to `severity`
    assert rec["level_or_status"] == "WARN"
    assert rec["value"] == "92"
    assert rec["labels"]["host"] == "db-02"
    assert rec["labels"]["index"] == "infra"


def test_query_handles_empty_results() -> None:
    src, _ = _make_source(post_response=FakeResponse(200, {"results": []}))
    assert src.query({"search": "search index=void"}) == []


def test_query_raises_on_non_200() -> None:
    src, _ = _make_source(post_response=FakeResponse(503, None))
    with pytest.raises(RuntimeError):
        src.query({"search": "search index=main"})


def test_normalized_record_has_all_contract_keys() -> None:
    src, _ = _make_source(post_response=FakeResponse(200, ONESHOT_PAYLOAD))
    rec = src.query({"search": "search index=main"})[0]
    for key in ("ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"):
        assert key in rec
