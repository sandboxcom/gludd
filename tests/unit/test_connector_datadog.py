"""Unit tests for the self-contained Datadog observability connector.

The connector's HTTP transport is *injected*, so every test here drives it with
canned payloads — no real network, no DNS, no shell. We assert:

* class contract (KIND class attr, ``name`` attr, config-driven ``__init__``);
* secrets are read from the environment (DD-API-KEY / DD-APPLICATION-KEY) via
  ``api_key_env`` / ``app_key_env`` and never hardcoded;
* SSRF literal-host blocking on ``site`` (loopback / metadata) at construction;
* logs mode normalization (POST search -> per-event records);
* metrics mode normalization (GET query -> per-series-point records);
* ``health()`` returns ok / not-ok and never raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.datadog import DatadogSource


class RecordingTransport:
    """Mock transport capturing every call and returning canned responses.

    Signature matches the connector's injected ``http_request``:
    ``(method, url, *, params, json, headers, timeout) -> (status, payload)``.
    """

    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "json": json,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            raise AssertionError("transport called more times than responses given")
        return self._responses.pop(0)


class BoomTransport:
    """Transport that always raises — to prove health()/query() never raise."""

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[int, Any]:
        raise RuntimeError("network is down")


# -- canned payloads -------------------------------------------------------


def logs_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "evt-1",
                "type": "log",
                "attributes": {
                    "timestamp": 1718000000000,
                    "status": "error",
                    "message": "boom happened",
                    "service": "checkout",
                    "host": "web-01",
                    "tags": ["env:prod", "team:payments"],
                    "attributes": {"http.status_code": 500},
                },
            },
            {
                "id": "evt-2",
                "type": "log",
                "attributes": {
                    "timestamp": 1718000005000,
                    "status": "info",
                    "message": "ok",
                    "service": "checkout",
                    "host": "web-02",
                    "tags": ["env:prod"],
                },
            },
        ],
        "meta": {"page": {"after": "cursor-abc"}},
    }


def metrics_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "series": [
            {
                "metric": "system.cpu.user",
                "scope": "host:web-01",
                "tag_set": ["host:web-01", "env:prod"],
                "pointlist": [
                    [1718000000000.0, 12.5],
                    [1718000060000.0, 18.0],
                ],
            },
            {
                "metric": "system.cpu.user",
                "scope": "host:web-02",
                "tag_set": ["host:web-02"],
                "pointlist": [
                    [1718000000000.0, 7.0],
                ],
            },
        ],
    }


# -- construction / contract ----------------------------------------------


def test_kind_class_attr() -> None:
    assert DatadogSource.KIND == "logs"


def test_name_defaults_from_site_and_is_overridable() -> None:
    src = DatadogSource(
        {"site": "https://api.datadoghq.com"}, http_request=RecordingTransport([])
    )
    assert isinstance(src.name, str)
    assert "datadog" in src.name

    named = DatadogSource(
        {"site": "https://api.datadoghq.com", "name": "dd-prod"},
        http_request=RecordingTransport([]),
    )
    assert named.name == "dd-prod"


def test_site_defaults_to_public_datadog() -> None:
    t = RecordingTransport([(200, {})])
    src = DatadogSource({}, http_request=t)
    src.health()
    assert t.calls[0]["url"].startswith("https://api.datadoghq.com")


def test_constructs_with_default_transport() -> None:
    # No transport injected: the connector falls back to a real stdlib transport
    # (so the registry's single-arg ``factory(config)`` build succeeds and the
    # source is NOT silently dropped from ``list_sources()``). No network is
    # touched at construction time.
    from general_ludd.connectors.datadog import _default_http_request

    src = DatadogSource({"site": "https://api.datadoghq.com"})
    assert src._http_request is _default_http_request
    assert src.name


def test_no_hardcoded_secrets_in_source() -> None:
    import inspect

    from general_ludd.connectors import datadog

    text = inspect.getsource(datadog)
    # The header NAME is allowed to appear; an actual 32-hex key literal is not.
    assert "DD-API-KEY" in text
    import re

    assert not re.search(r"['\"][0-9a-f]{32}['\"]", text)


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "site",
    [
        "https://localhost",
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://metadata.google.internal",
        "http://[::1]",
        "http://10.0.0.5",
    ],
)
def test_internal_site_is_rejected(site: str) -> None:
    with pytest.raises(ValueError):
        DatadogSource({"site": site}, http_request=RecordingTransport([]))


def test_bad_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        DatadogSource(
            {"site": "ftp://api.datadoghq.com"}, http_request=RecordingTransport([])
        )


# -- auth headers from env -------------------------------------------------


def test_headers_read_keys_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_DD_API", "api-secret-123")
    monkeypatch.setenv("MY_DD_APP", "app-secret-456")
    t = RecordingTransport([(200, {})])
    src = DatadogSource(
        {
            "site": "https://api.datadoghq.com",
            "api_key_env": "MY_DD_API",
            "app_key_env": "MY_DD_APP",
        },
        http_request=t,
    )
    src.health()
    headers = t.calls[0]["headers"]
    assert headers["DD-API-KEY"] == "api-secret-123"
    assert headers["DD-APPLICATION-KEY"] == "app-secret-456"


def test_missing_env_keys_are_omitted_not_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_DD_API", raising=False)
    monkeypatch.delenv("MY_DD_APP", raising=False)
    t = RecordingTransport([(200, {})])
    src = DatadogSource(
        {
            "site": "https://api.datadoghq.com",
            "api_key_env": "MY_DD_API",
            "app_key_env": "MY_DD_APP",
        },
        http_request=t,
    )
    src.health()
    headers = t.calls[0]["headers"]
    assert "DD-API-KEY" not in headers
    assert "DD-APPLICATION-KEY" not in headers


# -- logs mode -------------------------------------------------------------


def test_logs_query_posts_to_search_endpoint() -> None:
    t = RecordingTransport([(200, logs_payload())])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    src.query({"mode": "logs", "query": "status:error", "from": "now-15m", "to": "now"})

    call = t.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.datadoghq.com/api/v2/logs/events/search"
    assert call["json"]["filter"]["query"] == "status:error"
    assert call["json"]["filter"]["from"] == "now-15m"
    assert call["json"]["filter"]["to"] == "now"
    assert "page" in call["json"]


def test_logs_normalization_maps_attributes() -> None:
    t = RecordingTransport([(200, logs_payload())])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    records = src.query({"mode": "logs", "query": "*"})

    assert len(records) == 2
    rec = records[0]
    assert rec["kind"] == "logs"
    assert rec["source"] == src.name
    assert rec["ts"] == 1718000000000
    assert rec["level_or_status"] == "error"
    assert rec["message"] == "boom happened"
    assert rec["labels"]["service"] == "checkout"
    assert rec["labels"]["host"] == "web-01"
    assert rec["labels"]["tags"] == ["env:prod", "team:payments"]
    assert rec["raw"]["id"] == "evt-1"
    assert "value" in rec  # normalized shape always carries value


def test_logs_empty_data_is_empty_list() -> None:
    t = RecordingTransport([(200, {"data": []})])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    assert src.query({"mode": "logs", "query": "*"}) == []


# -- metrics mode ----------------------------------------------------------


def test_metrics_query_gets_v1_query_endpoint() -> None:
    t = RecordingTransport([(200, metrics_payload())])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    src.query(
        {
            "mode": "metrics",
            "query": "avg:system.cpu.user{*}",
            "from": 1718000000,
            "to": 1718000600,
        }
    )

    call = t.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.datadoghq.com/api/v1/query"
    assert call["params"]["query"] == "avg:system.cpu.user{*}"
    assert call["params"]["from"] == 1718000000
    assert call["params"]["to"] == 1718000600


def test_metrics_normalization_maps_points() -> None:
    t = RecordingTransport([(200, metrics_payload())])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    records = src.query({"mode": "metrics", "query": "avg:system.cpu.user{*}"})

    # 2 points in series-1 + 1 point in series-2 = 3 records
    assert len(records) == 3
    rec = records[0]
    assert rec["kind"] == "metrics"
    assert rec["source"] == src.name
    assert rec["ts"] == 1718000000000.0
    assert rec["value"] == 12.5
    assert rec["message"] == "system.cpu.user"
    assert rec["labels"]["metric"] == "system.cpu.user"
    assert rec["labels"]["scope"] == "host:web-01"
    assert "host:web-01" in rec["labels"]["tags"]
    assert "env:prod" in rec["labels"]["tags"]

    # second point of first series
    assert records[1]["value"] == 18.0
    assert records[1]["ts"] == 1718000060000.0

    # only point of second series
    assert records[2]["value"] == 7.0
    assert records[2]["labels"]["scope"] == "host:web-02"


def test_metrics_empty_series_is_empty_list() -> None:
    t = RecordingTransport([(200, {"status": "ok", "series": []})])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    assert src.query({"mode": "metrics", "query": "avg:x{*}"}) == []


def test_metrics_nan_point_value_becomes_none() -> None:
    payload = {
        "status": "ok",
        "series": [
            {
                "metric": "system.cpu.user",
                "scope": "host:web-01",
                "tag_set": ["host:web-01"],
                "pointlist": [[1718000000000.0, float("nan")]],
            }
        ],
    }
    t = RecordingTransport([(200, payload)])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    records = src.query({"mode": "metrics", "query": "x"})
    assert len(records) == 1
    assert records[0]["value"] is None


# -- mode dispatch / errors ------------------------------------------------


def test_unknown_mode_returns_error_record_not_raise() -> None:
    t = RecordingTransport([])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    records = src.query({"mode": "traces", "query": "*"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert t.calls == []  # never reached the transport


def test_query_transport_failure_becomes_error_record() -> None:
    src = DatadogSource(
        {"site": "https://api.datadoghq.com"}, http_request=BoomTransport()
    )
    records = src.query({"mode": "logs", "query": "*"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert records[0]["message"] == "transport error"


def test_non_2xx_logs_status_becomes_error_record() -> None:
    t = RecordingTransport([(403, {"errors": ["Forbidden"]})])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    records = src.query({"mode": "logs", "query": "*"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"


# -- health ----------------------------------------------------------------


def test_health_ok_hits_validate_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_API", "k")
    t = RecordingTransport([(200, {"valid": True})])
    src = DatadogSource(
        {"site": "https://api.datadoghq.com", "api_key_env": "DD_API"},
        http_request=t,
    )
    out = src.health()
    assert t.calls[0]["method"] == "GET"
    assert t.calls[0]["url"] == "https://api.datadoghq.com/api/v1/validate"
    assert out["ok"] is True
    assert "detail" in out


def test_health_not_ok_on_invalid() -> None:
    t = RecordingTransport([(403, {"valid": False})])
    src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
    out = src.health()
    assert out["ok"] is False
    assert "detail" in out


def test_health_never_raises_on_transport_error() -> None:
    src = DatadogSource(
        {"site": "https://api.datadoghq.com"}, http_request=BoomTransport()
    )
    out = src.health()
    assert out["ok"] is False
    assert "detail" in out
