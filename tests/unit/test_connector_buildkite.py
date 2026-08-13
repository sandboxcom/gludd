"""Unit tests for the Buildkite pipeline connector (fully mocked transport)."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from general_ludd.connectors.buildkite import BuildkiteSource, ConnectorConfigError

CANNED_BUILDS = [
    {
        "number": 42,
        "state": "passed",
        "branch": "main",
        "commit": "abcdef1234567890",
        "created_at": "2026-06-01T10:00:00Z",
        "finished_at": "2026-06-01T10:05:00Z",
        "web_url": "https://buildkite.com/acme/api/builds/42",
    },
    {
        "number": 43,
        "state": "failed",
        "branch": "feature/x",
        "commit": "",
        "created_at": "2026-06-02T11:00:00Z",
        "finished_at": None,
        "web_url": "https://buildkite.com/acme/api/builds/43",
    },
]


class _FakeTransport:
    """Records the last call and returns a canned (status, body)."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def __call__(self, method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), timeout))
        return self.status, self.body


def _config(**overrides: object) -> dict[str, object]:
    base = {"org": "acme", "pipeline": "api", "token_env": "BK_TEST_TOKEN"}
    base.update(overrides)
    return base


def test_query_normalizes_builds() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_BUILDS).encode())
    src = BuildkiteSource(_config(), transport=transport)

    events = src.query()

    assert len(events) == 2
    first = events[0]
    assert first["source"] == "buildkite"
    assert first["kind"] == "pipeline"
    assert first["level_or_status"] == "passed"
    assert first["ts"] == "2026-06-01T10:05:00Z"  # finished_at preferred
    assert first["message"] == "main@abcdef123456"
    assert first["labels"] == {"number": 42, "web_url": "https://buildkite.com/acme/api/builds/42"}
    assert first["raw"]["number"] == 42
    assert set(first) == {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_http_get_compatibility_accepts_decoded_json() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def http_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        calls.append((url, headers))
        return 200, CANNED_BUILDS

    src = BuildkiteSource(_config(), http_get=http_get)

    events = src.query()

    assert len(events) == 2
    assert events[0]["level_or_status"] == "passed"
    assert calls == [
        (
            "https://api.buildkite.com/v2/organizations/acme/pipelines/api/builds",
            {"Accept": "application/json"},
        )
    ]


def test_transport_and_http_get_are_mutually_exclusive() -> None:
    transport = _FakeTransport(200, b"[]")

    with pytest.raises(ValueError, match=r"transport.*http_get"):
        BuildkiteSource(
            _config(),
            transport=transport,
            http_get=lambda _url, _headers: (200, []),
        )


def test_query_ts_falls_back_to_created_at() -> None:
    transport = _FakeTransport(200, json.dumps(CANNED_BUILDS).encode())
    src = BuildkiteSource(_config(), transport=transport)
    events = src.query()
    # second build has finished_at=None -> falls back to created_at
    assert events[1]["ts"] == "2026-06-02T11:00:00Z"
    assert events[1]["message"] == "feature/x"  # no commit


def test_auth_header_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BK_TEST_TOKEN", "s3cr3t")
    transport = _FakeTransport(200, b"[]")
    src = BuildkiteSource(_config(), transport=transport)
    src.query()
    _, _, headers, timeout = transport.calls[-1]
    assert headers["Authorization"] == "Bearer s3cr3t"
    assert timeout == 10.0


def test_no_auth_header_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BK_TEST_TOKEN", raising=False)
    transport = _FakeTransport(200, b"[]")
    src = BuildkiteSource(_config(), transport=transport)
    src.query()
    _, _, headers, _ = transport.calls[-1]
    assert "Authorization" not in headers


def test_token_never_hardcoded() -> None:
    # With no env var set, there is no token material baked into the object.
    src = BuildkiteSource(_config(token_env="DEFINITELY_UNSET_VAR"))
    assert src._token == ""


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "http://10.0.0.5:8080",
        "http://169.254.169.254",
        "http://[::1]",
        "http://192.168.1.10",
        # Loopback / metadata by *name* (not IP literal) must also be rejected.
        "http://localhost",
        "https://ip6-localhost",
        "http://metadata.google.internal",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ConnectorConfigError):
        BuildkiteSource(_config(base_url=bad_url))


def test_public_base_url_accepted() -> None:
    src = BuildkiteSource(_config(base_url="https://api.buildkite.com/"))
    assert src.base_url == "https://api.buildkite.com"  # trailing slash stripped


def test_health_ok() -> None:
    src = BuildkiteSource(_config(), transport=_FakeTransport(200, b"[]"))
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_http_error() -> None:
    src = BuildkiteSource(_config(), transport=_FakeTransport(503, b""))
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "HTTP 503"


def test_health_never_raises() -> None:
    def boom(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
        raise RuntimeError("network down")

    src = BuildkiteSource(_config(), transport=boom)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "health check failed"


def test_query_raises_on_http_error() -> None:
    src = BuildkiteSource(_config(), transport=_FakeTransport(500, b""))
    with pytest.raises(ConnectorConfigError):
        src.query()


def test_fetch_log_returns_content() -> None:
    transport = _FakeTransport(200, json.dumps({"content": "line1\nline2"}).encode())
    src = BuildkiteSource(_config(), transport=transport)
    assert src.fetch_log("job-123") == "line1\nline2"
    assert transport.calls[-1][1].endswith("/builds/jobs/job-123/log")


def test_kind_and_name() -> None:
    src = BuildkiteSource(_config(name="bk-prod"))
    assert src.KIND == "pipeline"
    assert src.name == "bk-prod"
