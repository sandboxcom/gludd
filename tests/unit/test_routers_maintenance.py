# ruff: noqa: I001
"""Unit + functional tests for general_ludd/routers/maintenance.py.

Strategy
--------
* Build a minimal FastAPI test app with ``register()`` applied.
* Stub all three backend classes (GitIntelligence, DependencyManager,
  QualityGateChecker, GitHubIssueIngestor) via ``unittest.mock.patch`` /
  ``AsyncMock`` so tests are hermetic (no git, no network, no coverage files).
* Cover every route:  GET /admin/code-intel/hot-files,
                       GET /admin/deps/outdated,
                       POST /admin/issues/poll,
                       POST /admin/quality/check
* Cover: happy paths, edge values, idempotency (issues/poll seen-id dedup
  across requests), error propagation.

Uses ``httpx`` + ``anyio`` (installed transitively with fastapi[all] / pytest-anyio)
via httpx.AsyncClient + ASGITransport so no real server is started.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(daemon_state: dict[str, Any] | None = None) -> tuple[FastAPI, dict[str, Any]]:
    """Return a fresh FastAPI app with the maintenance router registered."""
    from general_ludd.routers.maintenance import register

    app = FastAPI()
    state: dict[str, Any] = daemon_state if daemon_state is not None else {}
    register(app, state)
    return app, state


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# /admin/code-intel/hot-files
# ---------------------------------------------------------------------------

class TestCodeIntelHotFiles:
    @pytest.mark.anyio
    async def test_happy_path_default_limit(self) -> None:
        """Returns hot_files and recent_commits from GitIntelligence."""
        hot = [{"path": "foo.py", "changes": 5}]
        commits = [{"hash": "abc1234", "message": "fix thing", "author": "A", "date": "2026-01-01"}]

        mock_gi = MagicMock()
        mock_gi.hot_files.return_value = hot
        mock_gi.recent_commits.return_value = commits

        app, _ = _make_app()

        with patch(
            "general_ludd.code_intelligence.git_intel.GitIntelligence",
            return_value=mock_gi,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/code-intel/hot-files")

        assert r.status_code == 200
        body = r.json()
        assert body["hot_files"] == hot
        assert body["recent_commits"] == commits
        mock_gi.hot_files.assert_called_once_with(limit=10)
        mock_gi.recent_commits.assert_called_once_with(limit=10)  # min(10, 20) = 10

    @pytest.mark.anyio
    async def test_custom_limit(self) -> None:
        mock_gi = MagicMock()
        mock_gi.hot_files.return_value = []
        mock_gi.recent_commits.return_value = []

        app, _ = _make_app()

        with patch(
            "general_ludd.code_intelligence.git_intel.GitIntelligence",
            return_value=mock_gi,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/code-intel/hot-files?limit=5")

        assert r.status_code == 200
        mock_gi.hot_files.assert_called_once_with(limit=5)
        mock_gi.recent_commits.assert_called_once_with(limit=5)  # min(5, 20) = 5

    @pytest.mark.anyio
    async def test_limit_capped_recent_commits_at_20(self) -> None:
        """recent_commits limit is capped at 20 even when caller passes limit=50."""
        mock_gi = MagicMock()
        mock_gi.hot_files.return_value = []
        mock_gi.recent_commits.return_value = []

        app, _ = _make_app()

        with patch(
            "general_ludd.code_intelligence.git_intel.GitIntelligence",
            return_value=mock_gi,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/code-intel/hot-files?limit=50")

        assert r.status_code == 200
        mock_gi.recent_commits.assert_called_once_with(limit=20)  # min(50, 20) = 20

    @pytest.mark.anyio
    async def test_empty_results(self) -> None:
        mock_gi = MagicMock()
        mock_gi.hot_files.return_value = []
        mock_gi.recent_commits.return_value = []

        app, _ = _make_app()

        with patch(
            "general_ludd.code_intelligence.git_intel.GitIntelligence",
            return_value=mock_gi,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/code-intel/hot-files")

        assert r.status_code == 200
        assert r.json() == {"hot_files": [], "recent_commits": []}

    @pytest.mark.anyio
    async def test_repo_root_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GLUDD_REPO_ROOT env var is forwarded to GitIntelligence."""
        monkeypatch.setenv("GLUDD_REPO_ROOT", "/custom/repo")

        captured: list[str] = []

        def fake_gi(repo_path: str) -> MagicMock:
            captured.append(repo_path)
            m = MagicMock()
            m.hot_files.return_value = []
            m.recent_commits.return_value = []
            return m

        # register() reads the env at registration time so we need a fresh app
        # built while the env var is already set.
        app, _ = _make_app()

        with patch(
            "general_ludd.code_intelligence.git_intel.GitIntelligence",
            side_effect=fake_gi,
        ):
            async with await _client(app) as c:
                await c.get("/admin/code-intel/hot-files")

        assert captured == ["/custom/repo"]


# ---------------------------------------------------------------------------
# /admin/deps/outdated
# ---------------------------------------------------------------------------

class TestDepsOutdated:
    @pytest.mark.anyio
    async def test_happy_path_with_outdated_packages(self) -> None:
        pkg = MagicMock()
        pkg.name = "requests"
        pkg.current_version = "2.28.0"
        pkg.latest_version = "2.31.0"

        mock_dm = MagicMock()
        mock_dm.check_for_updates = AsyncMock(return_value=[pkg])

        app, _ = _make_app()

        with patch(
            "general_ludd.dependency.manager.DependencyManager",
            return_value=mock_dm,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/deps/outdated")

        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["outdated"] == [
            {
                "name": "requests",
                "current_version": "2.28.0",
                "latest_version": "2.31.0",
            }
        ]

    @pytest.mark.anyio
    async def test_no_outdated_packages(self) -> None:
        mock_dm = MagicMock()
        mock_dm.check_for_updates = AsyncMock(return_value=[])

        app, _ = _make_app()

        with patch(
            "general_ludd.dependency.manager.DependencyManager",
            return_value=mock_dm,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/deps/outdated")

        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["outdated"] == []

    @pytest.mark.anyio
    async def test_multiple_outdated_packages(self) -> None:
        pkgs = []
        for name, cur, new in [("boto3", "1.0", "2.0"), ("flask", "2.0", "3.0")]:
            p = MagicMock()
            p.name = name
            p.current_version = cur
            p.latest_version = new
            pkgs.append(p)

        mock_dm = MagicMock()
        mock_dm.check_for_updates = AsyncMock(return_value=pkgs)

        app, _ = _make_app()

        with patch(
            "general_ludd.dependency.manager.DependencyManager",
            return_value=mock_dm,
        ):
            async with await _client(app) as c:
                r = await c.get("/admin/deps/outdated")

        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        names = [p["name"] for p in body["outdated"]]
        assert sorted(names) == ["boto3", "flask"]


# ---------------------------------------------------------------------------
# /admin/issues/poll
# ---------------------------------------------------------------------------

class TestIssuesPoll:
    @pytest.mark.anyio
    async def test_happy_path_returns_new_todos(self) -> None:
        todo = {"title": "Fix bug", "work_type": "bug_fix"}
        mock_ingestor = MagicMock()
        mock_ingestor.poll_issues = AsyncMock(return_value=[todo])

        app, _ = _make_app()

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            return_value=mock_ingestor,
        ):
            async with await _client(app) as c:
                r = await c.post(
                    "/admin/issues/poll",
                    json={"owner": "acme", "repo": "myrepo", "label": "gludd"},
                )

        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["new_todos"] == [todo]

    @pytest.mark.anyio
    async def test_default_label_is_gludd(self) -> None:
        """When label is omitted it defaults to 'gludd'."""
        captured_kwargs: list[dict] = []

        def fake_ingestor(**kwargs: Any) -> MagicMock:
            captured_kwargs.append(kwargs)
            m = MagicMock()
            m.poll_issues = AsyncMock(return_value=[])
            return m

        app, _ = _make_app()

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            side_effect=fake_ingestor,
        ):
            async with await _client(app) as c:
                await c.post(
                    "/admin/issues/poll",
                    json={"owner": "acme", "repo": "myrepo"},
                )

        assert captured_kwargs[0]["label"] == "gludd"

    @pytest.mark.anyio
    async def test_idempotency_seen_ids_persist_across_requests(self) -> None:
        """The seen-id set in daemon_state prevents duplicate todos on re-poll."""
        call_count = 0
        seen_on_second_call: set | None = None

        def fake_ingestor(**kwargs: Any) -> MagicMock:
            nonlocal call_count, seen_on_second_call
            call_count += 1
            seen = kwargs.get("seen_ids", set())
            if call_count == 2:
                seen_on_second_call = seen
            m = MagicMock()
            m.poll_issues = AsyncMock(return_value=[])
            return m

        state: dict[str, Any] = {}
        app, _ = _make_app(daemon_state=state)

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            side_effect=fake_ingestor,
        ):
            async with await _client(app) as c:
                await c.post("/admin/issues/poll", json={"owner": "o", "repo": "r"})
                # Mutate the seen set as the real ingestor would
                seen_store = state["issue_ingestor_seen_ids"]["o/r#gludd"]
                seen_store.add(42)
                await c.post("/admin/issues/poll", json={"owner": "o", "repo": "r"})

        assert call_count == 2
        # The second call must receive the same (mutated) set object
        assert seen_on_second_call is not None
        assert 42 in seen_on_second_call

    @pytest.mark.anyio
    async def test_idempotency_different_repos_get_separate_seen_sets(self) -> None:
        """Two different owner/repo combos get independent seen-id sets."""
        seen_sets: list[set] = []

        def fake_ingestor(**kwargs: Any) -> MagicMock:
            seen_sets.append(kwargs["seen_ids"])
            m = MagicMock()
            m.poll_issues = AsyncMock(return_value=[])
            return m

        state: dict[str, Any] = {}
        app, _ = _make_app(daemon_state=state)

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            side_effect=fake_ingestor,
        ):
            async with await _client(app) as c:
                await c.post("/admin/issues/poll", json={"owner": "o1", "repo": "r1"})
                await c.post("/admin/issues/poll", json={"owner": "o2", "repo": "r2"})

        assert len(seen_sets) == 2
        assert seen_sets[0] is not seen_sets[1]

    @pytest.mark.anyio
    async def test_empty_owner_repo_returns_empty(self) -> None:
        """Missing owner/repo strings are forwarded; ingestor returns []."""
        mock_ingestor = MagicMock()
        mock_ingestor.poll_issues = AsyncMock(return_value=[])

        app, _ = _make_app()

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            return_value=mock_ingestor,
        ):
            async with await _client(app) as c:
                r = await c.post("/admin/issues/poll", json={})

        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["new_todos"] == []

    @pytest.mark.anyio
    async def test_daemon_state_initialized_on_first_request(self) -> None:
        """seen-id store is created in daemon_state under the expected key."""
        mock_ingestor = MagicMock()
        mock_ingestor.poll_issues = AsyncMock(return_value=[])

        state: dict[str, Any] = {}
        app, _ = _make_app(daemon_state=state)

        with patch(
            "general_ludd.git_automation.issue_ingestor.GitHubIssueIngestor",
            return_value=mock_ingestor,
        ):
            async with await _client(app) as c:
                await c.post("/admin/issues/poll", json={"owner": "o", "repo": "r", "label": "mylab"})

        assert "issue_ingestor_seen_ids" in state
        assert "o/r#mylab" in state["issue_ingestor_seen_ids"]
        assert isinstance(state["issue_ingestor_seen_ids"]["o/r#mylab"], set)


# ---------------------------------------------------------------------------
# /admin/quality/check
# ---------------------------------------------------------------------------

class TestQualityCheck:
    @pytest.mark.anyio
    async def test_happy_path_passing_coverage(self) -> None:
        expected = {"gate": "python_coverage", "passed": True, "checks": []}
        mock_checker = MagicMock()
        mock_checker.check_python_coverage.return_value = expected

        app, _ = _make_app()

        with patch(
            "general_ludd.quality.gate.QualityGateChecker",
            return_value=mock_checker,
        ):
            async with await _client(app) as c:
                r = await c.post(
                    "/admin/quality/check",
                    json={"coverage_percent": 92.5},
                )

        assert r.status_code == 200
        assert r.json() == expected
        mock_checker.check_python_coverage.assert_called_once_with(
            coverage_percent=92.5,
            branch_percent=None,
        )

    @pytest.mark.anyio
    async def test_with_branch_percent(self) -> None:
        expected = {"gate": "python_coverage", "passed": True, "checks": []}
        mock_checker = MagicMock()
        mock_checker.check_python_coverage.return_value = expected

        app, _ = _make_app()

        with patch(
            "general_ludd.quality.gate.QualityGateChecker",
            return_value=mock_checker,
        ):
            async with await _client(app) as c:
                r = await c.post(
                    "/admin/quality/check",
                    json={"coverage_percent": 88.0, "branch_percent": 75.0},
                )

        assert r.status_code == 200
        mock_checker.check_python_coverage.assert_called_once_with(
            coverage_percent=88.0,
            branch_percent=75.0,
        )

    @pytest.mark.anyio
    async def test_missing_coverage_percent_defaults_to_zero(self) -> None:
        """coverage_percent defaults to 0.0 when absent from payload."""
        mock_checker = MagicMock()
        mock_checker.check_python_coverage.return_value = {"gate": "python_coverage", "passed": False, "checks": []}

        app, _ = _make_app()

        with patch(
            "general_ludd.quality.gate.QualityGateChecker",
            return_value=mock_checker,
        ):
            async with await _client(app) as c:
                r = await c.post("/admin/quality/check", json={})

        assert r.status_code == 200
        mock_checker.check_python_coverage.assert_called_once_with(
            coverage_percent=0.0,
            branch_percent=None,
        )

    @pytest.mark.anyio
    async def test_failing_coverage_returns_checker_result(self) -> None:
        failing = {
            "gate": "python_coverage",
            "passed": False,
            "checks": [
                {"check": "line_coverage", "actual": 50.0, "required": 85.0, "status": "failed"}
            ],
        }
        mock_checker = MagicMock()
        mock_checker.check_python_coverage.return_value = failing

        app, _ = _make_app()

        with patch(
            "general_ludd.quality.gate.QualityGateChecker",
            return_value=mock_checker,
        ):
            async with await _client(app) as c:
                r = await c.post("/admin/quality/check", json={"coverage_percent": 50.0})

        assert r.status_code == 200
        body = r.json()
        assert body["passed"] is False
        assert body["checks"][0]["status"] == "failed"

    @pytest.mark.anyio
    async def test_coverage_percent_coerced_to_float(self) -> None:
        """Integer payload value should be cast to float before checker call."""
        mock_checker = MagicMock()
        mock_checker.check_python_coverage.return_value = {"gate": "python_coverage", "passed": True, "checks": []}

        app, _ = _make_app()

        with patch(
            "general_ludd.quality.gate.QualityGateChecker",
            return_value=mock_checker,
        ):
            async with await _client(app) as c:
                r = await c.post("/admin/quality/check", json={"coverage_percent": 90})

        assert r.status_code == 200
        call_args = mock_checker.check_python_coverage.call_args
        assert isinstance(call_args.kwargs["coverage_percent"], float)
        assert call_args.kwargs["coverage_percent"] == 90.0


# ---------------------------------------------------------------------------
# register() wiring — daemon_state isolation
# ---------------------------------------------------------------------------

class TestRegisterWiring:
    def test_register_returns_none_and_app_has_routes(self) -> None:
        from general_ludd.routers.maintenance import register

        app = FastAPI()
        result = register(app, {})
        assert result is None
        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/admin/code-intel/hot-files" in paths
        assert "/admin/deps/outdated" in paths
        assert "/admin/issues/poll" in paths
        assert "/admin/quality/check" in paths

    def test_separate_apps_have_independent_states(self) -> None:
        """Two register() calls with different states do not share data."""
        state_a: dict[str, Any] = {}
        state_b: dict[str, Any] = {}

        _app_a, _ = _make_app(daemon_state=state_a)
        _app_b, _ = _make_app(daemon_state=state_b)

        state_a["x"] = 1
        assert "x" not in state_b
