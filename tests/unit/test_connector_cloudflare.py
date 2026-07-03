"""Unit tests for the self-contained Cloudflare audit-log connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.cloudflare import CloudflareSource

ACCOUNT = "acct-123"
TOKEN_ENV = "CF_TEST_TOKEN"


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"result": []}

    def json(self) -> Any:
        return self._body


class RecordingTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, {"result": []})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise TimeoutError("slow")


def _body(records: list[dict[str, Any]], total_pages: int = 1) -> dict[str, Any]:
    return {
        "success": True,
        "result": records,
        "result_info": {"page": 1, "per_page": 100, "total_pages": total_pages},
    }


CANNED = [
    {
        "id": "log-1",
        "when": "2026-06-12T09:00:00Z",
        "action": {"type": "rule.create", "result": "success"},
        "actor": {"email": "admin@example.com", "ip": "198.51.100.4"},
        "resource": {"type": "firewall_rule", "id": "fw-1"},
        "zone": {"name": "example.com"},
    },
    {
        "id": "log-2",
        "timestamp": "2026-06-12T09:01:00Z",
        "action": {"type": "rule.delete", "result": "failure"},
        "actor": {"email": "ops@example.com"},
        "actor_ip": "198.51.100.9",
        "resource": {"type": "firewall_rule"},
        "zone": "fallback-zone",
    },
]


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "cf-secret-token")
    return "cf-secret-token"


def _src(transport: Any, **extra: Any) -> CloudflareSource:
    cfg: dict[str, Any] = {"account_id": ACCOUNT, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return CloudflareSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert CloudflareSource.KIND == "events"
    src = CloudflareSource(
        {"account_id": ACCOUNT, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "cloudflare"


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        CloudflareSource({"account_id": ACCOUNT}, transport=lambda *a, **k: None)


def test_requires_account_or_base_url() -> None:
    with pytest.raises(ValueError, match="account_id"):
        CloudflareSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)


def test_default_endpoint_built_from_account(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport).query({})
    url = transport.calls[0]["url"]
    assert url.endswith(f"/accounts/{ACCOUNT}/audit_logs")
    assert url.startswith("https://api.cloudflare.com/client/v4")


def test_base_url_override_for_zone_logs(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    src = CloudflareSource(
        {
            "base_url": "https://api.cloudflare.com/client/v4/zones/z1/audit_logs",
            "token_env": TOKEN_ENV,
        },
        transport=transport,
    )
    src.query({})
    assert "/zones/z1/audit_logs" in transport.calls[0]["url"]


# -- normalization --------------------------------------------------------


def test_query_normalizes_records(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body(CANNED))])
    rows = _src(transport).query({})
    assert len(rows) == 2

    a = rows[0]
    assert a["ts"] == "2026-06-12T09:00:00Z"
    assert a["source"] == "cloudflare"
    assert a["kind"] == "events"
    assert a["level_or_status"] == "success"
    assert a["message"] == "rule.create"
    assert a["value"] == 1
    assert a["labels"] == {
        "actor_email": "admin@example.com",
        "resource_type": "firewall_rule",
        "zone": "example.com",
        "client_ip": "198.51.100.4",
    }
    assert a["raw"]["id"] == "log-1"

    b = rows[1]
    assert b["ts"] == "2026-06-12T09:01:00Z"  # falls back to `timestamp`
    assert b["level_or_status"] == "failure"
    assert b["labels"]["zone"] == "fallback-zone"  # string zone fallback
    assert b["labels"]["client_ip"] == "198.51.100.9"  # actor_ip fallback


# -- auth from env --------------------------------------------------------


def test_auth_header_from_env(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport).query({})
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_token_not_leaked_into_request(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body(CANNED))])
    _src(transport).query({})
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["params"])


# -- pagination (result_info.total_pages) ---------------------------------


def test_pagination_across_total_pages(token: str) -> None:
    p1 = FakeResponse(200, _body([CANNED[0]], total_pages=2))
    p2 = FakeResponse(200, _body([CANNED[1]], total_pages=2))
    transport = RecordingTransport([p1, p2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert transport.calls[0]["params"]["page"] == "1"
    assert transport.calls[1]["params"]["page"] == "2"


def test_pagination_bounded_by_max_pages(token: str) -> None:
    pages = [FakeResponse(200, _body([CANNED[0]], total_pages=99)) for _ in range(99)]
    transport = RecordingTransport(pages)
    rows = _src(transport, max_pages=2).query({})
    assert len(transport.calls) == 2
    assert len(rows) == 2


def test_single_page_stops(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, _body(CANNED, total_pages=1))]
    )
    _src(transport).query({})
    assert len(transport.calls) == 1


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://127.0.0.1/v4/accounts/a/audit_logs",
        "https://localhost/audit_logs",
        "http://10.1.2.3/audit_logs",
        "http://169.254.169.254/latest",
        # Alibaba cloud-metadata endpoint — coverage gained by delegating to the
        # canonical is_url_blocked (the old bespoke guard missed this address).
        "http://100.100.100.200/latest",
        "http://[::1]/audit_logs",
    ],
)
def test_ssrf_rejects_private_base_url(bad: str) -> None:
    with pytest.raises(ValueError, match=r"private|loopback"):
        CloudflareSource(
            {"base_url": bad, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    src = CloudflareSource(
        {
            "base_url": "http://127.0.0.1/audit_logs",
            "token_env": TOKEN_ENV,
            "allow_private": True,
        },
        transport=transport,
    )
    assert src.query({}) == []


@pytest.mark.parametrize(
    "bad",
    [
        "http://metadata.google.internal/audit_logs",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://METADATA.GOOGLE.INTERNAL/audit_logs",
        "http://metadata/audit_logs",
        "http://metadata",
    ],
)
def test_ssrf_rejects_metadata_hostname(bad: str) -> None:
    # DNS name for the cloud metadata endpoint must be refused (the
    # 169.254.169.254 IP literal is covered by test_ssrf_rejects_private_base_url).
    with pytest.raises(ValueError, match=r"private|loopback"):
        CloudflareSource(
            {"base_url": bad, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_ssrf_metadata_lookalike_hostname_allowed() -> None:
    # Exact-match only: a benign host that merely contains "metadata" is fine.
    src = CloudflareSource(
        {"base_url": "https://metadata.example.com/audit_logs", "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.base_url == "https://metadata.example.com/audit_logs"


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    h = _src(transport).health()
    assert h["ok"] is True


def test_health_not_ok_on_5xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(503, {})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert "503" in h["detail"]


def test_health_never_raises(token: str) -> None:
    h = _src(_raising_transport).health()
    assert h["ok"] is False
    assert "TimeoutError" in h["detail"]


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(401, {})])
    with pytest.raises(RuntimeError, match="HTTP 401"):
        _src(transport).query({})


def test_timeout_is_bounded(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport, timeout=7.5).query({})
    assert transport.calls[0]["timeout"] == 7.5
