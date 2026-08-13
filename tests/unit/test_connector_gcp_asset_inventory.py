"""Unit tests for GcpAssetInventorySource (cloud infra-state connector).

All HTTP is mocked via an injectable transport; no network, no DNS. Tests cover:
normalization of a canned searchAllResources payload, Bearer-token auth wiring,
SSRF literal-host rejection (internal/metadata hosts in the scope), and a
health() that never raises.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from general_ludd.connectors.gcp_asset_inventory import (
    GcpAssetInventorySource,
    _CallbackResponse,
    _coerce_transport,
    _host_is_internal,
)


class FakeResponse:
    """Minimal httpx-Response-like stand-in for the injected transport."""

    def __init__(self, status_code: int, json_body: Any, *, url: str = "") -> None:
        self.status_code = status_code
        self._json = json_body
        self.url = url
        self.text = str(json_body)

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingTransport:
    """Captures the request and returns a canned response.

    Mimics the small slice of an httpx.Client the connector uses: a `.get`
    accepting url/headers/params/timeout and returning a response object.
    """

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "headers": headers or {}, "params": params or {}, "timeout": timeout}
        )
        return self.response


CANNED_SEARCH = {
    "results": [
        {
            "name": "//compute.googleapis.com/projects/p1/zones/us-central1-a/instances/vm-1",
            "assetType": "compute.googleapis.com/Instance",
            "project": "projects/123456789",
            "location": "us-central1-a",
            "state": "RUNNING",
            "updateTime": "2026-06-10T12:00:00Z",
            "displayName": "vm-1",
        },
        {
            "name": "//storage.googleapis.com/my-bucket",
            "assetType": "storage.googleapis.com/Bucket",
            "project": "projects/123456789",
            "location": "us",
            "updateTime": "2026-06-09T08:30:00Z",
        },
    ]
}


def make_source(transport: Any, **overrides: Any) -> GcpAssetInventorySource:
    config: dict[str, Any] = {
        "scope": "projects/123456789",
        "token_env": "GCP_TEST_TOKEN",
        "transport": transport,
        "timeout": 5.0,
    }
    config.update(overrides)
    return GcpAssetInventorySource(config)


class TestContract:
    def test_project_id_alias_builds_project_scope(self) -> None:
        src = GcpAssetInventorySource(
            {"project_id": "my-project"},
            transport=RecordingTransport(FakeResponse(200, {"results": []})),
        )

        assert src._scope == "projects/my-project"

    def test_kind_and_name(self) -> None:
        src = make_source(RecordingTransport(FakeResponse(200, CANNED_SEARCH)))
        assert src.KIND == "infra"
        assert isinstance(src.name, str) and src.name

    def test_health_never_raises_returns_ok_detail(self) -> None:
        src = make_source(RecordingTransport(FakeResponse(200, {"results": []})))
        h = src.health()
        assert set(h) >= {"ok", "detail"}
        assert isinstance(h["ok"], bool)


class TestCompatibilityHelpers:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("", True),
            ("[::1]", True),
            ("internal", True),
            ("metadata.google.internal", True),
            ("cloudasset.googleapis.com", False),
            ("8.8.8.8", False),
        ],
    )
    def test_host_is_internal(self, host: str, expected: bool) -> None:
        assert _host_is_internal(host) is expected

    def test_callback_response_raises_for_error_status(self) -> None:
        response = _CallbackResponse(503, {"error": "unavailable"})

        with pytest.raises(RuntimeError, match="HTTP 503"):
            response.raise_for_status()

    def test_transport_coercion_handles_none_and_invalid_values(self) -> None:
        assert _coerce_transport(None) is None
        with pytest.raises(TypeError, match="transport"):
            _coerce_transport(object())


class TestNormalization:
    def test_keyword_tuple_transport_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-abc")
        calls: list[tuple[str, str]] = []

        def transport(method: str, url: str, **_: object) -> tuple[int, object]:
            calls.append((method, url))
            return 200, CANNED_SEARCH

        src = GcpAssetInventorySource(
            {
                "scope": "projects/123456789",
                "token_env": "GCP_TEST_TOKEN",
            },
            transport=transport,
        )

        rows = src.query({})

        assert len(rows) == 2
        assert calls == [
            (
                "GET",
                "https://cloudasset.googleapis.com/v1/"
                "projects/123456789:searchAllResources",
            )
        ]

    def test_query_normalizes_resources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-abc")
        transport = RecordingTransport(FakeResponse(200, CANNED_SEARCH))
        src = make_source(transport)

        rows = src.query({})

        assert isinstance(rows, list) and len(rows) == 2
        first = rows[0]
        # required normalized keys
        assert set(first) >= {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert first["kind"] == "infra"
        assert first["ts"] == "2026-06-10T12:00:00Z"
        assert first["level_or_status"] == "RUNNING"
        assert "vm-1" in first["message"] or "instances/vm-1" in first["message"]
        assert "compute.googleapis.com/Instance" in first["message"]
        assert first["labels"]["assetType"] == "compute.googleapis.com/Instance"
        assert first["labels"]["location"] == "us-central1-a"
        assert first["labels"]["project"] == "projects/123456789"
        assert first["raw"]["name"].endswith("instances/vm-1")

    def test_query_handles_missing_optional_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-abc")
        transport = RecordingTransport(FakeResponse(200, CANNED_SEARCH))
        src = make_source(transport)
        rows = src.query({})
        bucket = rows[1]
        # no 'state' -> level_or_status falls back, does not crash
        assert bucket["level_or_status"] is not None
        assert bucket["labels"]["assetType"] == "storage.googleapis.com/Bucket"


class TestAuthAndTarget:
    def test_bearer_token_from_env_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-xyz")
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport)
        src.query({})
        assert transport.calls, "transport.get was not called"
        auth = transport.calls[0]["headers"].get("Authorization", "")
        assert auth == "Bearer tok-xyz"

    def test_url_targets_searchallresources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-xyz")
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport)
        src.query({})
        url = transport.calls[0]["url"]
        assert url.startswith("https://cloudasset.googleapis.com/v1/")
        assert ":searchAllResources" in url
        assert "projects/123456789" in url

    def test_timeout_is_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCP_TEST_TOKEN", "tok-xyz")
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport, timeout=7.5)
        src.query({})
        assert transport.calls[0]["timeout"] == 7.5

    def test_missing_token_env_health_not_ok(self) -> None:
        # ensure env var absent
        os.environ.pop("GCP_TEST_TOKEN", None)
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport)
        h = src.health()
        assert h["ok"] is False
        assert "token" in h["detail"].lower()


class TestSsrf:
    @pytest.mark.parametrize(
        "bad_host",
        [
            "169.254.169.254",
            "metadata.google.internal",
            "localhost",
            "127.0.0.1",
            "10.0.0.5",
            "[::1]",
            "192.168.1.1",
        ],
    )
    def test_internal_host_in_endpoint_override_rejected(self, bad_host: str) -> None:
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        with pytest.raises(ValueError):
            make_source(transport, endpoint=f"https://{bad_host}/v1")

    def test_non_https_endpoint_rejected(self) -> None:
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        with pytest.raises(ValueError):
            make_source(transport, endpoint="http://cloudasset.googleapis.com/v1")

    def test_default_endpoint_accepted(self) -> None:
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport)  # default endpoint, no raise
        assert src.KIND == "infra"

    # Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
    # the shared general_ludd.security.ssrf.is_url_blocked guarantees. This
    # connector is https-only, so the canonical hosts are exercised over https.
    @pytest.mark.parametrize(
        "bad_endpoint",
        [
            "https://localhost/",
            "https://metadata.google.internal/",
            "https://169.254.169.254/",
            "https://100.100.100.200/",
        ],
    )
    def test_canonical_ssrf_endpoints_rejected(self, bad_endpoint: str) -> None:
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        with pytest.raises(ValueError):
            make_source(transport, endpoint=bad_endpoint)

    def test_public_endpoint_constructs_after_consolidation(self) -> None:
        transport = RecordingTransport(FakeResponse(200, {"results": []}))
        src = make_source(transport, endpoint="https://cloudasset.googleapis.com/v1")
        assert src.KIND == "infra"
