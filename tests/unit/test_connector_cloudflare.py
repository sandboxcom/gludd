"""Structural tests for the Cloudflare audit-log connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.cloudflare import (
    _DEFAULT_API,
    CloudflareSource,
)


class _Resp:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body or {}


@pytest.fixture
def minimal_config() -> dict[str, Any]:
    return {"token_env": "CLOUDFLARE_TOKEN", "account_id": "abc123"}


class TestCloudflareSourceInit:
    def test_init_with_token_env_and_account_id(self, minimal_config: dict[str, Any]) -> None:
        src = CloudflareSource(minimal_config)
        assert src.name == "cloudflare"
        assert src.KIND == "events"
        assert src.token_env == "CLOUDFLARE_TOKEN"
        assert src.allow_private is False
        assert src.max_pages == 10
        assert src.per_page == 100
        assert src.timeout == 30.0
        assert "accounts/abc123/audit_logs" in src.base_url

    def test_init_with_base_url(self) -> None:
        src = CloudflareSource(
            {"token_env": "TOKEN", "base_url": "https://api.example.com/v1/logs"}
        )
        assert src.base_url == "https://api.example.com/v1/logs"

    def test_init_missing_token_env_raises(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            CloudflareSource({})

    def test_init_missing_account_id_raises(self) -> None:
        with pytest.raises(ValueError, match="account_id"):
            CloudflareSource({"token_env": "TOKEN"})

    def test_init_custom_api_url(self) -> None:
        src = CloudflareSource(
            {
                "token_env": "TOKEN",
                "account_id": "xyz",
                "api_url": "https://gateway.example.com/v4",
            }
        )
        assert "gateway.example.com" in src.base_url

    def test_init_defaults(self) -> None:
        src = CloudflareSource({"token_env": "T", "account_id": "a"})
        assert src.max_pages == 10
        assert src.per_page == 100
        assert src.timeout == 30.0
        assert src.allow_private is False

    def test_init_custom_max_pages_and_per_page(self) -> None:
        src = CloudflareSource(
            {"token_env": "T", "account_id": "a", "max_pages": 5, "per_page": 50}
        )
        assert src.max_pages == 5
        assert src.per_page == 50

    def test_init_allow_private(self) -> None:
        src = CloudflareSource(
            {"token_env": "T", "account_id": "a", "allow_private": True}
        )
        assert src.allow_private is True

    def test_init_invalid_url_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported URL scheme"):
            CloudflareSource(
                {"token_env": "T", "base_url": "ftp://127.0.0.1/logs"}
            )

    def test_init_empty_host_raises(self) -> None:
        with pytest.raises(ValueError, match="no host"):
            CloudflareSource({"token_env": "T", "base_url": "https:///logs"})

    def test_init_ssrf_loopback_blocked(self) -> None:
        with pytest.raises(ValueError, match="refusing private/loopback"):
            CloudflareSource(
                {"token_env": "T", "base_url": "http://127.0.0.1/logs"}
            )

    def test_init_transport_injection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("T", "fake-token")
        calls: list[dict[str, Any]] = []

        def fake(method: str, url: str, **kwargs: Any) -> _Resp:
            calls.append({"method": method, "url": url})
            return _Resp(200, {})

        src = CloudflareSource({"token_env": "T", "account_id": "a"}, transport=fake)
        src.health()
        assert len(calls) == 1
        assert calls[0]["method"] == "GET"


class TestNormalize:
    def test_normalize_basic(self, minimal_config: dict[str, Any]) -> None:
        src = CloudflareSource(minimal_config)
        record = {
            "when": "2025-01-01T00:00:00Z",
            "action": {"result": "success", "type": "login"},
            "actor": {"email": "user@example.com", "ip": "1.2.3.4"},
            "resource": {"type": "zone"},
            "zone": {"name": "example.com"},
        }
        result = src._normalize(record)
        assert result["ts"] == "2025-01-01T00:00:00Z"
        assert result["source"] == "cloudflare"
        assert result["kind"] == "events"
        assert result["level_or_status"] == "success"
        assert result["message"] == "login"
        assert result["value"] == 1
        assert result["labels"]["actor_email"] == "user@example.com"
        assert result["labels"]["resource_type"] == "zone"
        assert result["labels"]["zone"] == "example.com"
        assert result["labels"]["client_ip"] == "1.2.3.4"
        assert result["raw"] is record

    def test_normalize_missing_fields(self, minimal_config: dict[str, Any]) -> None:
        src = CloudflareSource(minimal_config)
        result = src._normalize({})
        assert result["ts"] is None
        assert result["level_or_status"] is None
        assert result["labels"]["actor_email"] is None

    def test_normalize_actor_ip_fallback(self, minimal_config: dict[str, Any]) -> None:
        src = CloudflareSource(minimal_config)
        record = {"action": {}, "actor": {"ip": "10.0.0.1"}, "resource": {}}
        result = src._normalize(record)
        assert result["labels"]["client_ip"] == "10.0.0.1"


class TestResultRecords:
    def test_valid_list(self) -> None:
        records = CloudflareSource._result_records(
            {"result": [{"id": 1}, {"id": 2}]}
        )
        assert len(records) == 2

    def test_non_dict_body(self) -> None:
        assert CloudflareSource._result_records("not a dict") == []
        assert CloudflareSource._result_records(None) == []
        assert CloudflareSource._result_records([]) == []

    def test_result_not_a_list(self) -> None:
        assert CloudflareSource._result_records({"result": "string"}) == []

    def test_filters_non_dict_entries(self) -> None:
        records = CloudflareSource._result_records(
            {"result": [{"id": 1}, "string", None, {"id": 2}]}
        )
        assert len(records) == 2


class TestTotalPages:
    def test_valid_total_pages(self) -> None:
        assert CloudflareSource._total_pages(
            {"result_info": {"total_pages": 5}}
        ) == 5

    def test_default_no_result_info(self) -> None:
        assert CloudflareSource._total_pages({}) == 1
        assert CloudflareSource._total_pages("not dict") == 1
        assert CloudflareSource._total_pages([]) == 1

    def test_result_info_not_dict(self) -> None:
        assert CloudflareSource._total_pages({"result_info": "string"}) == 1

    def test_invalid_total_pages(self) -> None:
        assert CloudflareSource._total_pages(
            {"result_info": {"total_pages": "invalid"}}
        ) == 1

    def test_zero_total_pages_clamped_to_1(self) -> None:
        assert CloudflareSource._total_pages(
            {"result_info": {"total_pages": 0}}
        ) == 1


class TestHealth:
    def test_tuple_transport_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLOUDFLARE_TOKEN", "fake-token")

        def fake(
            method: str, url: str, **kwargs: Any
        ) -> tuple[int, object]:
            return 200, {
                "result": [
                    {
                        "when": "2025-01-01T00:00:00Z",
                        "action": {"type": "login", "result": "success"},
                    }
                ]
            }

        src = CloudflareSource(
            {"token_env": "CLOUDFLARE_TOKEN", "account_id": "a"},
            transport=fake,
        )

        records = src.query()

        assert len(records) == 1
        assert records[0]["message"] == "login"
        assert records[0]["level_or_status"] == "success"

    def test_health_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUDFLARE_TOKEN", "fake-token")

        def fake(method: str, url: str, **kwargs: Any) -> _Resp:
            return _Resp(200, {})

        src = CloudflareSource(
            {"token_env": "CLOUDFLARE_TOKEN", "account_id": "a"}, transport=fake
        )
        result = src.health()
        assert result["ok"] is True
        assert "HTTP 200" in result["detail"]

    def test_health_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUDFLARE_TOKEN", "fake-token")

        def fake(method: str, url: str, **kwargs: Any) -> _Resp:
            return _Resp(500, {})

        src = CloudflareSource(
            {"token_env": "CLOUDFLARE_TOKEN", "account_id": "a"}, transport=fake
        )
        result = src.health()
        assert result["ok"] is False
        assert "500" in result["detail"]

    def test_health_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUDFLARE_TOKEN", "fake-token")

        def fake(method: str, url: str, **kwargs: Any) -> _Resp:
            raise RuntimeError("boom")

        src = CloudflareSource(
            {"token_env": "CLOUDFLARE_TOKEN", "account_id": "a"}, transport=fake
        )
        result = src.health()
        assert result["ok"] is False
        assert "RuntimeError" in result["detail"]


class TestConstants:
    def test_default_api(self) -> None:
        assert _DEFAULT_API == "https://api.cloudflare.com/client/v4"
