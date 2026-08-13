"""Unit tests for the Honeycomb observability connector.

All transport is MOCKED via an injectable callable — no real network, no DNS.
The connector is self-contained: it imports nothing from sibling connector
modules and defines no shared base here.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from general_ludd.connectors.honeycomb import HoneycombSource


# --------------------------------------------------------------------------- #
# Mock transport
# --------------------------------------------------------------------------- #
class _MockResponse:
    """Minimal stand-in for an HTTP response the connector consumes."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self) -> Any:
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class _RecordingTransport:
    """Injectable transport that records calls and replays canned responses.

    Signature matches what the connector calls:
        transport(method, url, headers=..., json=..., timeout=...)
    Responses are matched by (method, url-suffix) or popped in order.
    """

    def __init__(self, routes: dict[tuple[str, str], _MockResponse] | None = None,
                 sequence: list[_MockResponse] | None = None) -> None:
        self.routes = routes or {}
        self.sequence = list(sequence or [])
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                 json: Any = None, timeout: float | None = None) -> _MockResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {},
             "json": json, "timeout": timeout}
        )
        for (m, suffix), resp in self.routes.items():
            if method == m and url.endswith(suffix):
                return resp
        if self.sequence:
            return self.sequence.pop(0)
        raise AssertionError(f"no canned response for {method} {url}")


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #
QUERY_CREATE_PAYLOAD = {"id": "query-abc"}
QUERY_RESULT_CREATE_PAYLOAD = {"id": "result-xyz", "complete": False}

# A completed query-result with two breakdown rows; calculation is P95 duration.
QUERY_RESULT_DATA_PAYLOAD = {
    "id": "result-xyz",
    "complete": True,
    "data": {
        "results": [
            {
                "time": "2026-06-16T00:00:00Z",
                "data": {
                    "service.name": "checkout",
                    "http.status_code": 200,
                    "P95(duration_ms)": 412.5,
                },
            },
            {
                "time": "2026-06-16T00:01:00Z",
                "data": {
                    "service.name": "payments",
                    "http.status_code": 500,
                    "P95(duration_ms)": 1980.0,
                },
            },
        ]
    },
}

AUTH_OK_PAYLOAD = {
    "team": {"name": "Acme Observability", "slug": "acme"},
    "environment": {"name": "production", "slug": "prod"},
}

QUERY_SPEC = {
    "query_spec": {
        "breakdowns": ["service.name", "http.status_code"],
        "calculations": [{"op": "P95", "column": "duration_ms"}],
        "time_range": 3600,
        "filters": [{"column": "service.name", "op": "exists"}],
    }
}


def _make_source(transport: _RecordingTransport, **overrides: Any) -> HoneycombSource:
    config: dict[str, Any] = {
        "dataset": "my-dataset",
        "api_key_env": "HNY_TEST_KEY",
        "transport": transport,
    }
    config.update(overrides)
    return HoneycombSource(config)


# --------------------------------------------------------------------------- #
# Contract: class attributes / identity
# --------------------------------------------------------------------------- #
class TestContract:
    def test_kind_class_attr_is_traces(self) -> None:
        assert HoneycombSource.KIND == "traces"

    def test_has_name_attr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(_RecordingTransport())
        assert isinstance(src.name, str)
        assert src.name

    def test_default_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(_RecordingTransport())
        assert src.base_url == "https://api.honeycomb.io"

    def test_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(_RecordingTransport(), base_url="https://eu1.honeycomb.io")
        assert src.base_url == "https://eu1.honeycomb.io"

    def test_api_key_never_hardcoded_read_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "env-supplied-key")
        transport = _RecordingTransport(
            routes={("GET", "/1/auth"): _MockResponse(200, AUTH_OK_PAYLOAD)}
        )
        src = _make_source(transport)
        src.health()
        # The key must come from the env var, surfaced in the X-Honeycomb-Team header.
        assert transport.calls
        assert transport.calls[0]["headers"]["X-Honeycomb-Team"] == "env-supplied-key"


# --------------------------------------------------------------------------- #
# SSRF / internal-host protection (literal, no DNS)
# --------------------------------------------------------------------------- #
class TestSSRF:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://169.254.169.254",  # cloud metadata
            "http://10.0.0.5",
            "http://192.168.1.1",
            "http://172.16.0.1",
            "http://[::1]",
            "http://metadata.google.internal",
        ],
    )
    def test_internal_base_rejected_at_construction(
        self, monkeypatch: pytest.MonkeyPatch, bad_url: str
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        with pytest.raises(ValueError):
            _make_source(_RecordingTransport(), base_url=bad_url)

    def test_public_base_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(_RecordingTransport(), base_url="https://api.honeycomb.io")
        assert src.base_url == "https://api.honeycomb.io"


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #
class TestHealth:
    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = _RecordingTransport(
            routes={("GET", "/1/auth"): _MockResponse(200, AUTH_OK_PAYLOAD)}
        )
        src = _make_source(transport)
        result = src.health()
        assert result["ok"] is True
        assert "Acme Observability" in result["detail"]
        # Hit the auth endpoint.
        assert transport.calls[-1]["url"].endswith("/1/auth")
        assert transport.calls[-1]["method"] == "GET"

    def test_health_not_ok_on_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = _RecordingTransport(
            routes={("GET", "/1/auth"): _MockResponse(401, {"error": "unauthorized"})}
        )
        src = _make_source(transport)
        result = src.health()
        assert result["ok"] is False
        assert result["detail"]

    def test_health_never_raises_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")

        def boom(*args: Any, **kwargs: Any) -> _MockResponse:
            raise ConnectionError("network down")

        src = _make_source(_RecordingTransport())
        # Replace transport with one that raises.
        cast(Any, src)._transport = boom
        result = src.health()
        assert result["ok"] is False
        assert result["detail"] == "health check failed"

    def test_health_missing_env_key_is_not_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HNY_TEST_KEY", raising=False)
        transport = _RecordingTransport(
            routes={("GET", "/1/auth"): _MockResponse(200, AUTH_OK_PAYLOAD)}
        )
        src = _make_source(transport)
        result = src.health()
        assert result["ok"] is False

    def test_tuple_transport_keyword_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        calls: list[dict[str, Any]] = []

        def transport(
            method: str,
            url: str,
            **kwargs: Any,
        ) -> tuple[int, object]:
            calls.append({"method": method, "url": url, **kwargs})
            return 200, AUTH_OK_PAYLOAD

        src = HoneycombSource(
            {"dataset": "my-dataset", "api_key_env": "HNY_TEST_KEY"},
            transport=transport,
        )

        result = src.health()

        assert result["ok"] is True
        assert calls[0]["method"] == "GET"


# --------------------------------------------------------------------------- #
# query() — traces / high-cardinality query result
# --------------------------------------------------------------------------- #
class TestQueryTraces:
    def _full_flow_transport(self) -> _RecordingTransport:
        return _RecordingTransport(
            routes={
                ("POST", "/1/queries/my-dataset"): _MockResponse(
                    201, QUERY_CREATE_PAYLOAD
                ),
                ("POST", "/1/query_results/my-dataset"): _MockResponse(
                    201, QUERY_RESULT_CREATE_PAYLOAD
                ),
                ("GET", "/1/query_results/my-dataset/result-xyz"): _MockResponse(
                    200, QUERY_RESULT_DATA_PAYLOAD
                ),
            }
        )

    def test_query_returns_normalized_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = self._full_flow_transport()
        src = _make_source(transport)
        records = src.query(QUERY_SPEC)
        assert isinstance(records, list)
        assert len(records) == 2
        for rec in records:
            assert set(rec.keys()) >= {
                "ts", "source", "kind", "level_or_status",
                "message", "value", "labels", "raw",
            }

    def test_query_normalizes_calculation_into_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        # First calculation value (P95 duration) becomes the record value.
        assert records[0]["value"] == 412.5
        assert records[1]["value"] == 1980.0

    def test_query_normalizes_breakdowns_into_labels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        labels0 = records[0]["labels"]
        assert labels0["service.name"] == "checkout"
        assert labels0["http.status_code"] == 200

    def test_query_sets_kind_and_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        assert records[0]["kind"] == "traces"
        assert records[0]["source"] == src.name

    def test_query_sets_ts_from_row_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        assert records[0]["ts"] == "2026-06-16T00:00:00Z"

    def test_query_message_contains_breakdown_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        assert "checkout" in records[0]["message"]

    def test_query_raw_preserves_full_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        src = _make_source(self._full_flow_transport())
        records = src.query(QUERY_SPEC)
        assert records[0]["raw"]["data"]["P95(duration_ms)"] == 412.5

    def test_query_sends_team_header_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "header-key-123")
        transport = self._full_flow_transport()
        src = _make_source(transport)
        src.query(QUERY_SPEC)
        for call in transport.calls:
            assert call["headers"]["X-Honeycomb-Team"] == "header-key-123"

    def test_query_posts_query_spec_to_queries_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = self._full_flow_transport()
        src = _make_source(transport)
        src.query(QUERY_SPEC)
        create_call = transport.calls[0]
        assert create_call["method"] == "POST"
        assert create_call["url"].endswith("/1/queries/my-dataset")
        # The user's query_spec body is forwarded.
        assert create_call["json"]["breakdowns"] == [
            "service.name", "http.status_code",
        ]

    def test_query_is_time_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = self._full_flow_transport()
        src = _make_source(transport)
        src.query(QUERY_SPEC)
        for call in transport.calls:
            assert call["timeout"] is not None
            assert call["timeout"] > 0

    def test_query_with_canned_single_shot_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-resolved result id / single-shot GET path also normalizes."""
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = _RecordingTransport(
            routes={
                ("POST", "/1/queries/my-dataset"): _MockResponse(
                    201, QUERY_CREATE_PAYLOAD
                ),
                ("POST", "/1/query_results/my-dataset"): _MockResponse(
                    # Already complete in the create response (single-shot).
                    201, QUERY_RESULT_DATA_PAYLOAD
                ),
            }
        )
        src = _make_source(transport)
        records = src.query(QUERY_SPEC)
        assert len(records) == 2


# --------------------------------------------------------------------------- #
# query() — events style
# --------------------------------------------------------------------------- #
EVENTS_RESULT_PAYLOAD = {
    "id": "result-evt",
    "complete": True,
    "data": {
        "results": [
            {
                "time": "2026-06-16T00:00:05Z",
                "data": {
                    "name": "request.error",
                    "level": "error",
                    "message": "upstream timeout",
                    "COUNT": 7,
                },
            }
        ]
    },
}


class TestQueryEvents:
    def test_events_style_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HNY_TEST_KEY", "secret")
        transport = _RecordingTransport(
            routes={
                ("POST", "/1/queries/my-dataset"): _MockResponse(
                    201, QUERY_CREATE_PAYLOAD
                ),
                ("POST", "/1/query_results/my-dataset"): _MockResponse(
                    201, EVENTS_RESULT_PAYLOAD
                ),
            }
        )
        src = _make_source(transport)
        spec = dict(QUERY_SPEC)
        spec["style"] = "events"
        records = src.query(spec)
        assert len(records) == 1
        rec = records[0]
        assert rec["level_or_status"] == "error"
        assert "upstream timeout" in rec["message"]
        assert rec["value"] == 7
