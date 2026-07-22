"""Structural tests for GitHub Actions connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.github_actions import (
    GitHubActionsSource,
    _parse_ts,
)


def _fake_http(
    status: int = 200,
    body: Any = None,
) -> Any:
    def _get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        _get.calls.append({"url": url, "headers": headers})  # type: ignore[attr-defined]
        return status, body

    _get.calls: list[dict[str, Any]] = []  # type: ignore[attr-defined]
    return _get


CANNED_RUNS = {
    "workflow_runs": [
        {
            "id": 1,
            "name": "CI",
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-01-15T10:30:00Z",
            "head_sha": "abc123",
            "event": "push",
            "path": ".github/workflows/ci.yml",
        }
    ]
}


class TestParseTs:
    def test_valid(self) -> None:
        r = _parse_ts("2025-01-15T10:30:00Z")
        assert r is not None
        assert r > 1700000000.0

    def test_none_or_empty(self) -> None:
        assert _parse_ts(None) is None
        assert _parse_ts("") is None

    def test_invalid(self) -> None:
        assert _parse_ts("not-a-timestamp") is None

    def test_with_offset(self) -> None:
        r = _parse_ts("2025-01-15T10:30:00+00:00")
        assert r is not None
        assert r > 1700000000.0

    def test_no_timezone(self) -> None:
        r = _parse_ts("2025-01-15T10:30:00")
        assert r is not None
        assert r > 1700000000.0


class TestInit:
    def test_with_repo(self) -> None:
        src = GitHubActionsSource({"repo": "owner/name"})
        assert src.repo == "owner/name"
        assert src.name == "github-actions:owner/name"

    def test_bad_repo_raises(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({"repo": "bad-format"})

    def test_missing_repo_raises(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({})

    def test_default_base_url(self) -> None:
        src = GitHubActionsSource({"repo": "owner/name"})
        assert "api.github.com" in src.base_url

    def test_custom_base_url(self) -> None:
        src = GitHubActionsSource({"repo": "owner/name", "base_url": "https://git.example.com/api"})
        assert src.base_url == "https://git.example.com/api"


class TestSSRF:
    def test_localhost_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({"repo": "owner/name", "base_url": "http://localhost/"})

    def test_metadata_ip_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({"repo": "owner/name", "base_url": "http://169.254.169.254/"})

    def test_loopback_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({"repo": "owner/name", "base_url": "http://127.0.0.1/"})

    def test_private_ip_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubActionsSource({"repo": "owner/name", "base_url": "http://10.0.0.5/"})

    def test_public_url_ok(self) -> None:
        src = GitHubActionsSource({"repo": "owner/name", "base_url": "https://github.example.com/api"})
        assert "github.example.com" in src.base_url


class TestHeaders:
    def test_default_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        src = GitHubActionsSource({"repo": "owner/name"})
        headers = src._headers()
        assert "Accept" in headers
        assert "X-GitHub-Api-Version" in headers
        assert "User-Agent" in headers
        assert "Authorization" not in headers

    def test_auth_header_with_env_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        src = GitHubActionsSource({"repo": "owner/name"})
        headers = src._headers()
        assert headers["Authorization"] == "Bearer ghp_test"

    def test_custom_token_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHE_TOKEN", "tok-456")
        src = GitHubActionsSource({"repo": "owner/name", "token_env": "GHE_TOKEN"})
        headers = src._headers()
        assert headers["Authorization"] == "Bearer tok-456"


class TestKind:
    def test_kind_is_pipeline(self) -> None:
        assert GitHubActionsSource.KIND == "pipeline"


class TestQuery:
    def test_returns_normalized_records(self) -> None:
        http = _fake_http(200, CANNED_RUNS)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        records = src.query({})
        assert len(records) == 1
        r = records[0]
        assert r["source"] == src.name
        assert r["kind"] == "pipeline"
        assert r["level_or_status"] == "success"
        assert r["labels"]["run_id"] == 1
        assert r["labels"]["head_sha"] == "abc123"

    def test_branch_filter(self) -> None:
        http = _fake_http(200, CANNED_RUNS)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        records = src.query({"branch": "develop"})
        assert records == []

    def test_status_filter(self) -> None:
        http = _fake_http(200, CANNED_RUNS)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        records = src.query({"status": "failure"})
        assert records == []

    def test_limit(self) -> None:
        runs = {"workflow_runs": [
            {
                "id": i,
                "name": f"run-{i}",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
            }
            for i in range(5)
        ]}
        http = _fake_http(200, runs)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        records = src.query({"limit": 2})
        assert len(records) == 2

    def test_http_error_returns_empty(self) -> None:
        http = _fake_http(500, {})
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        assert src.query({}) == []

    def test_non_dict_body(self) -> None:
        http = _fake_http(200, ["not", "a", "dict"])
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        assert src.query({}) == []


class TestHealth:
    def test_ok(self) -> None:
        http = _fake_http(200, CANNED_RUNS)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        r = src.health()
        assert r["ok"] is True

    def test_http_error(self) -> None:
        http = _fake_http(503, {})
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        r = src.health()
        assert r["ok"] is False

    def test_transport_error(self) -> None:
        def _raise(*a: Any, **kw: Any) -> tuple[int, Any]:
            raise RuntimeError("down")

        src = GitHubActionsSource({"repo": "owner/name"}, http_get=_raise)
        r = src.health()
        assert r["ok"] is False


class TestFetchFailedLogs:
    def test_success(self) -> None:
        body = {"jobs": [{"id": 1, "status": "completed"}]}
        http = _fake_http(200, body)
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        jobs = src.fetch_failed_logs(123)
        assert len(jobs) == 1
        assert jobs[0]["id"] == 1

    def test_error_returns_empty(self) -> None:
        http = _fake_http(500, {})
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        assert src.fetch_failed_logs(123) == []

    def test_transport_error_returns_empty(self) -> None:
        def _raise(*a: Any, **kw: Any) -> tuple[int, Any]:
            raise RuntimeError("down")

        src = GitHubActionsSource({"repo": "owner/name"}, http_get=_raise)
        assert src.fetch_failed_logs(123) == []

    def test_non_dict_body(self) -> None:
        http = _fake_http(200, "not dict")
        src = GitHubActionsSource({"repo": "owner/name"}, http_get=http)
        assert src.fetch_failed_logs(123) == []
