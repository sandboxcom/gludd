"""Unit tests for the Travis CI pipeline connector (fully mocked transport)."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from general_ludd.connectors.travis import ConnectorConfigError, TravisSource

CANNED_RESPONSE = {
    "builds": [
        {
            "id": 1001,
            "number": "57",
            "state": "passed",
            "finished_at": "2026-06-03T09:30:00Z",
            "branch": {"name": "main"},
            "commit": {"sha": "deadbeefcafebabe"},
        },
        {
            "id": 1002,
            "number": "58",
            "state": "errored",
            "finished_at": None,
            "branch": {"name": "dev"},
            "commit": {"sha": ""},
        },
    ]
}


class _FakeTransport:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base = {"slug": "acme/api", "token_env": "TRAVIS_TEST_TOKEN"}
    base.update(overrides)
    return base


def test_query_normalizes_builds() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_RESPONSE).encode())
    src = TravisSource(_config(), transport=transport)

    events = src.query()

    assert len(events) == 2
    first = events[0]
    assert first["source"] == "travis"
    assert first["kind"] == "pipeline"
    assert first["level_or_status"] == "passed"
    assert first["ts"] == "2026-06-03T09:30:00Z"
    assert first["message"] == "main@deadbeefcafe"
    assert first["labels"] == {"id": 1001, "number": "57"}
    assert first["raw"]["id"] == 1001
    assert set(first) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_http_get_and_repository_alias_accept_decoded_json() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def http_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        calls.append((url, headers))
        return 200, CANNED_RESPONSE

    src = TravisSource(
        {"repository": "acme/api", "token_env": "TRAVIS_TEST_TOKEN"},
        http_get=http_get,
    )

    events = src.query()

    assert len(events) == 2
    assert calls[0][0].endswith("/repo/acme%2Fapi/builds")


def test_transport_and_http_get_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match=r"transport.*http_get"):
        TravisSource(
            _config(),
            transport=_FakeTransport(200, b"{}"),
            http_get=lambda _url, _headers: (200, {}),
        )


def test_query_handles_missing_commit() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_RESPONSE).encode())
    src = TravisSource(_config(), transport=transport)
    events = src.query()
    assert events[1]["message"] == "dev"  # empty commit sha
    assert events[1]["ts"] is None


def test_builds_url_encodes_slug() -> None:
    transport = _FakeTransport(200, b'{"builds": []}')
    src = TravisSource(_config(), transport=transport)
    src.query()
    assert transport.calls[-1][1].endswith("/repo/acme%2Fapi/builds")


def test_auth_and_api_version_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRAVIS_TEST_TOKEN", "tk-123")
    transport = _FakeTransport(200, b'{"builds": []}')
    src = TravisSource(_config(), transport=transport)
    src.query()
    _, _, headers, _ = transport.calls[-1]
    assert headers["Authorization"] == "token tk-123"
    assert headers["Travis-API-Version"] == "3"


def test_no_auth_header_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRAVIS_TEST_TOKEN", raising=False)
    transport = _FakeTransport(200, b'{"builds": []}')
    src = TravisSource(_config(), transport=transport)
    src.query()
    _, _, headers, _ = transport.calls[-1]
    assert "Authorization" not in headers
    assert headers["Travis-API-Version"] == "3"


def test_token_never_hardcoded() -> None:
    src = TravisSource(_config(token_env="DEFINITELY_UNSET_VAR"))
    assert src._token == ""


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "http://10.1.2.3",
        "http://169.254.0.1",
        "http://[fe80::1]",
        # Loopback / metadata by *name* (not IP literal) must also be rejected.
        "http://localhost",
        "https://ip6-localhost",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ConnectorConfigError):
        TravisSource(_config(base_url=bad_url))


def test_default_base_url() -> None:
    src = TravisSource(_config())
    assert src.base_url == "https://api.travis-ci.com"


# Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
# the shared general_ludd.security.ssrf.is_url_blocked guarantees.
_CANONICAL_SSRF_URLS = [
    "http://localhost/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/",
    "http://100.100.100.200/",
]


@pytest.mark.parametrize("bad_url", _CANONICAL_SSRF_URLS)
def test_canonical_ssrf_urls_rejected(bad_url: str) -> None:
    with pytest.raises(ConnectorConfigError):
        TravisSource(_config(base_url=bad_url))


def test_public_base_url_constructs_after_consolidation() -> None:
    src = TravisSource(_config(base_url="https://api.example.com"))
    assert src.base_url == "https://api.example.com"


def test_health_ok() -> None:
    src = TravisSource(_config(), transport=_FakeTransport(200, b'{"builds": []}'))
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok() -> None:
    src = TravisSource(_config(), transport=_FakeTransport(401, b""))
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "HTTP 401"


def test_health_never_raises() -> None:
    def boom(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise OSError("connection refused")

    src = TravisSource(_config(), transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"


def test_fetch_log() -> None:
    transport = _FakeTransport(200, json.dumps({"content": "build log"}).encode())
    src = TravisSource(_config(), transport=transport)
    assert src.fetch_log("99") == "build log"
    assert transport.calls[-1][1].endswith("/job/99/log")


def test_kind_and_name() -> None:
    src = TravisSource(_config(name="travis-ci"))
    assert src.KIND == "pipeline"
    assert src.name == "travis-ci"
