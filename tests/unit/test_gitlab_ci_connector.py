"""Unit tests for the self-contained GitlabCiSource connector.

A fake transport returns canned payloads so no real network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from general_ludd.connectors.gitlab_ci import GitlabCiSource

_PIPELINES: list[dict[str, Any]] = [
    {
        "id": 47,
        "iid": 12,
        "project_id": 1,
        "sha": "a91957a8",
        "ref": "main",
        "status": "success",
        "source": "push",
        "created_at": "2026-06-10T08:00:00.000Z",
        "updated_at": "2026-06-10T08:05:00.000Z",
        "web_url": "https://gitlab.com/g/p/-/pipelines/47",
    },
    {
        "id": 48,
        "ref": "feature/x",
        "sha": "deadbeef",
        "status": "failed",
        "source": "merge_request_event",
        "updated_at": "2026-06-11T09:00:00Z",
        "web_url": "https://gitlab.com/g/p/-/pipelines/48",
    },
]


class _FakeTransport:
    """Records the last (url, headers) and returns a canned (status, body)."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        self.url: str | None = None
        self.headers: dict[str, str] | None = None

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any]:
        self.url = url
        self.headers = headers
        return self.status, self.body


def _make(transport: _FakeTransport, **cfg: Any) -> GitlabCiSource:
    config = {"project_id": "1"}
    config.update(cfg)
    return GitlabCiSource(config, http_get=transport)


def test_kind_and_name() -> None:
    src = _make(_FakeTransport(200, _PIPELINES))
    assert src.KIND == "pipeline"
    assert src.name == "gitlab-ci:1"


def test_default_base_url_is_gitlab_com() -> None:
    src = _make(_FakeTransport(200, _PIPELINES))
    assert src.base_url == "https://gitlab.com"


def test_missing_project_id_raises() -> None:
    with pytest.raises(ValueError):
        GitlabCiSource({}, http_get=_FakeTransport(200, []))


def test_query_normalizes_records() -> None:
    t = _FakeTransport(200, _PIPELINES)
    src = _make(t)
    records = src.query({})
    assert len(records) == 2

    first = records[0]
    assert first["kind"] == "pipeline"
    assert first["source"] == "gitlab-ci:1"
    assert first["level_or_status"] == "success"
    assert first["message"] == "main @ a91957a8"
    assert first["value"] is None
    assert first["labels"] == {
        "id": 47,
        "web_url": "https://gitlab.com/g/p/-/pipelines/47",
        "source": "push",
    }
    assert first["raw"] is _PIPELINES[0]

    expected_ts = datetime(2026, 6, 10, 8, 5, tzinfo=UTC).timestamp()
    assert first["ts"] == expected_ts

    assert records[1]["level_or_status"] == "failed"
    assert records[1]["message"] == "feature/x @ deadbeef"


def test_status_mapping_preserves_running() -> None:
    payload = [{"id": 1, "ref": "main", "sha": "x", "status": "running"}]
    src = _make(_FakeTransport(200, payload))
    assert src.query({})[0]["level_or_status"] == "running"


def test_auth_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_PRIVATE_TOKEN", "glpat-secret")
    t = _FakeTransport(200, _PIPELINES)
    _make(t).query({})
    assert t.headers is not None
    assert t.headers["PRIVATE-TOKEN"] == "glpat-secret"


def test_no_auth_header_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_PRIVATE_TOKEN", raising=False)
    t = _FakeTransport(200, _PIPELINES)
    _make(t).query({})
    assert t.headers is not None
    assert "PRIVATE-TOKEN" not in t.headers


def test_custom_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_GL_TOKEN", "abc")
    t = _FakeTransport(200, _PIPELINES)
    _make(t, token_env="MY_GL_TOKEN").query({})
    assert t.headers is not None
    assert t.headers["PRIVATE-TOKEN"] == "abc"


def test_query_url_includes_filters() -> None:
    t = _FakeTransport(200, _PIPELINES)
    _make(t).query({"ref": "main", "status": "failed", "per_page": 5})
    assert t.url is not None
    assert "/api/v4/projects/1/pipelines" in t.url
    assert "ref=main" in t.url
    assert "status=failed" in t.url
    assert "per_page=5" in t.url


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/api",
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://10.0.0.5",
        "http://metadata.google.internal",
        "ftp://gitlab.com",
    ],
)
def test_internal_base_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        GitlabCiSource(
            {"project_id": "1", "base_url": bad_url},
            http_get=_FakeTransport(200, []),
        )


def test_public_base_url_accepted() -> None:
    src = GitlabCiSource(
        {"project_id": "1", "base_url": "https://gitlab.example.com/"},
        http_get=_FakeTransport(200, []),
    )
    assert src.base_url == "https://gitlab.example.com"


def test_health_ok() -> None:
    src = _make(_FakeTransport(200, _PIPELINES))
    assert src.health() == {"ok": True, "detail": "HTTP 200"}


def test_health_not_ok_on_error_status() -> None:
    src = _make(_FakeTransport(403, {"message": "forbidden"}))
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "HTTP 403"


def test_health_never_raises_on_transport_error() -> None:
    def boom(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        raise RuntimeError("network down")

    src = GitlabCiSource({"project_id": "1"}, http_get=boom)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_query_returns_empty_on_non_list_body() -> None:
    src = _make(_FakeTransport(200, {"unexpected": "shape"}))
    assert src.query({}) == []


def test_query_returns_empty_on_error_status() -> None:
    src = _make(_FakeTransport(500, None))
    assert src.query({}) == []


def test_fetch_jobs_returns_jobs() -> None:
    jobs = [{"id": 1, "name": "build"}, {"id": 2, "name": "test"}]
    t = _FakeTransport(200, jobs)
    out = _make(t).fetch_jobs(47)
    assert out == jobs
    assert t.url is not None
    assert t.url.endswith("/pipelines/47/jobs")


def test_fetch_jobs_empty_on_error() -> None:
    src = _make(_FakeTransport(404, None))
    assert src.fetch_jobs(99) == []
