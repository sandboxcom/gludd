"""Structural tests for the GitHub Actions observability connector."""

from __future__ import annotations

import pytest

from general_ludd.connectors.github_actions import (
    GitHubActionsSource,
    _parse_ts,
)


class TestParseTs:
    def test_parse_ts_valid(self) -> None:
        result = _parse_ts("2025-01-15T10:30:00Z")
        assert result is not None
        assert result > 1700000000.0

    def test_parse_ts_none_or_empty(self) -> None:
        assert _parse_ts(None) is None
        assert _parse_ts("") is None

    def test_parse_ts_invalid(self) -> None:
        assert _parse_ts("not-a-timestamp") is None


class TestGitHubActionsSourceInit:
    def test_init_with_repo(self) -> None:
        src = GitHubActionsSource({"repo": "owner/name"})
        assert src.repo == "owner/name"
        assert src.name == "github-actions:owner/name"
        assert src.token_env == "GITHUB_TOKEN"

    def test_init_missing_repo_raises(self) -> None:
        with pytest.raises(ValueError, match="repo"):
            GitHubActionsSource({})

    def test_init_invalid_repo_raises(self) -> None:
        with pytest.raises(ValueError, match="repo"):
            GitHubActionsSource({"repo": "bad-repo"})
        with pytest.raises(ValueError, match="repo"):
            GitHubActionsSource({"repo": ""})

    def test_init_custom_token_env(self) -> None:
        src = GitHubActionsSource({"repo": "a/b", "token_env": "MY_TOKEN"})
        assert src.token_env == "MY_TOKEN"

    def test_init_custom_base_url(self) -> None:
        src = GitHubActionsSource(
            {"repo": "a/b", "base_url": "https://github.example.com/api/v3"}
        )
        assert "github.example.com" in src.base_url


class TestNormalize:
    def test_normalize_basic(self) -> None:
        src = GitHubActionsSource({"repo": "owner/repo"})
        run = {
            "name": "CI",
            "head_branch": "main",
            "conclusion": "success",
            "updated_at": "2025-01-15T10:30:00Z",
            "id": 12345,
            "head_sha": "abc123",
            "event": "push",
            "path": ".github/workflows/ci.yml",
        }
        result = src._normalize(run)
        assert result["source"] == "github-actions:owner/repo"
        assert result["kind"] == "pipeline"
        assert result["level_or_status"] == "success"
        assert result["message"] == "CI @ main"
        assert result["labels"]["run_id"] == 12345
        assert result["labels"]["head_sha"] == "abc123"
        assert result["labels"]["event"] == "push"
        assert result["labels"]["workflow"] == ".github/workflows/ci.yml"
        assert result["raw"] is run

    def test_normalize_empty_fields(self) -> None:
        src = GitHubActionsSource({"repo": "a/b"})
        result = src._normalize({})
        assert result["source"] == "github-actions:a/b"
        assert result["level_or_status"] == ""


class TestHealth:
    def test_health_ok(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (200, {"workflow_runs": []})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        result = src.health()
        assert result["ok"] is True

    def test_health_non_200(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (500, {})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        result = src.health()
        assert result["ok"] is False

    def test_health_never_raises(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            raise RuntimeError("boom")

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        result = src.health()
        assert result["ok"] is False


class TestQuery:
    def test_query_success(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (
                200,
                {
                    "workflow_runs": [
                        {"name": "CI", "head_branch": "main", "conclusion": "success"},
                        {"name": "Lint", "head_branch": "main", "conclusion": "failure"},
                    ]
                },
            )

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.query({})
        assert len(results) == 2

    def test_query_non_200(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (500, {})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.query({})
        assert results == []

    def test_query_filter_by_branch(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (
                200,
                {
                    "workflow_runs": [
                        {"name": "CI", "head_branch": "main", "conclusion": "success"},
                        {"name": "CI", "head_branch": "dev", "conclusion": "success"},
                    ]
                },
            )

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.query({"branch": "main"})
        assert len(results) == 1

    def test_query_filter_by_status(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (
                200,
                {
                    "workflow_runs": [
                        {"name": "A", "conclusion": "success"},
                        {"name": "B", "conclusion": "failure"},
                    ]
                },
            )

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.query({"status": "failure"})
        assert len(results) == 1
        assert results[0]["level_or_status"] == "failure"

    def test_query_limit(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (
                200,
                {
                    "workflow_runs": [
                        {"name": "A", "head_branch": "x", "conclusion": "y"},
                        {"name": "B", "head_branch": "x", "conclusion": "y"},
                        {"name": "C", "head_branch": "x", "conclusion": "y"},
                    ]
                },
            )

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.query({"limit": 2})
        assert len(results) == 2

    def test_query_empty_spec(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (200, {"workflow_runs": [{"name": "X", "head_branch": "y", "conclusion": "z"}]})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        assert len(src.query(None)) == 1  # type: ignore[arg-type]


class TestFetchFailedLogs:
    def test_success(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (200, {"jobs": [{"id": 1, "status": "completed"}, {"id": 2}]})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        results = src.fetch_failed_logs(42)
        assert len(results) == 2

    def test_non_200(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            return (404, {})

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        assert src.fetch_failed_logs(42) == []

    def test_exception_returns_empty(self) -> None:
        def fake(url: str, headers: dict[str, str]) -> tuple[int, object]:
            raise RuntimeError("boom")

        src = GitHubActionsSource({"repo": "a/b"}, http_get=fake)
        assert src.fetch_failed_logs(42) == []


class TestHeaders:
    def test_headers_format(self) -> None:
        src = GitHubActionsSource({"repo": "a/b"})
        headers = src._headers()
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert headers["User-Agent"] == "general-ludd-connector"

    def test_headers_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "fake-gh-token")
        src = GitHubActionsSource({"repo": "a/b"})
        headers = src._headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer fake-gh-token"

    def test_headers_without_token(self) -> None:
        src = GitHubActionsSource({"repo": "a/b"})
        headers = src._headers()
        assert "Authorization" not in headers
