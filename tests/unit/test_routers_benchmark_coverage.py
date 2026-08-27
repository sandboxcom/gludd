"""Branch coverage for the benchmark router's owned runtime seams."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.routers import benchmark


def _endpoint(app: FastAPI, path: str) -> Any:
    """Return the registered endpoint for ``path``."""
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route.endpoint
    raise AssertionError(f"route not registered: {path}")


class _Session:
    """Minimal async database session test double."""

    committed = False

    async def commit(self) -> None:
        """Record the commit boundary."""
        self.committed = True


class _SessionContext(AbstractAsyncContextManager[_Session]):
    """Own a fake session for one route invocation."""

    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close the fake context without suppressing failures."""
        return None

    async def __aenter__(self) -> _Session:
        """Return the owned fake session."""
        return self._session


class _BenchmarkRepo:
    """Exercise every database-backed benchmark route."""

    def __init__(self, _session: _Session) -> None:
        pass

    async def get_aggregate_scores(self, *, task_type: str | None) -> list[dict[str, object]]:
        """Return one aggregate score."""
        return [{"task_type": task_type, "score": 0.9}]

    async def list_recent(self, *, limit: int) -> list[SimpleNamespace]:
        """Return one recent row, including the nullable timestamp branch."""
        return [
            SimpleNamespace(
                id=limit,
                prompt_profile_id="prompt",
                model_profile_id="model",
                task_type="feature",
                completion_score=1.0,
                code_quality_score=0.9,
                instruction_adherence_score=0.8,
                token_efficiency_score=0.7,
                success=True,
                cost_usd=0.1,
                created_at=None,
            )
        ]

    async def record_result(self, *, data: dict[str, object]) -> SimpleNamespace:
        """Return the recorded result."""
        assert data["model_profile_id"] == "model"
        return SimpleNamespace(id=17, success=data["success"])


class _PromptRepo:
    """Provide one prompt profile."""

    def __init__(self, _session: _Session) -> None:
        pass

    async def list_all(self) -> list[SimpleNamespace]:
        """Return one serializable prompt profile."""
        return [SimpleNamespace(id="p", name="Prompt", source="local", source_url=None, version="1")]


class _Router:
    """Provide one leaderboard candidate."""

    def __init__(self, *, benchmark_repo: _BenchmarkRepo) -> None:
        assert isinstance(benchmark_repo, _BenchmarkRepo)

    async def get_leaderboard(self, *, task_type: object) -> list[SimpleNamespace]:
        """Return one leaderboard row."""
        assert getattr(task_type, "value", None) == "bug_fix"
        return [
            SimpleNamespace(
                prompt_profile_id="prompt",
                model_profile_id="model",
                composite_score=0.95,
                avg_cost_usd=0.1,
                sample_count=3,
                task_type=task_type,
            )
        ]


def test_ab_request_and_allowed_root_validation(tmp_path: Path) -> None:
    """Require absolute roots and include only configured daemon roots."""
    with pytest.raises(ValidationError, match="A/B roots must be absolute"):
        benchmark.ABTestRequest(baseline_root="relative", candidate_root=str(tmp_path), module="general_ludd.x")

    app = FastAPI()
    assert benchmark._abtest_allowed_roots(app) == [Path("/tmp/gludd-worktrees")]
    app.state._project_root = str(tmp_path)
    assert benchmark._abtest_allowed_roots(app) == [Path("/tmp/gludd-worktrees"), tmp_path]


@pytest.mark.asyncio
async def test_ab_endpoint_success_and_fail_closed_paths(tmp_path: Path) -> None:
    """Cover owned-root, missing-root, and bounded-timeout decisions."""
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    app = FastAPI()
    app.state._project_root = str(tmp_path)
    benchmark.register(app, {})
    endpoint = _endpoint(app, "/admin/abtest/run")
    request = benchmark.ABTestRequest(
        baseline_root=str(baseline),
        candidate_root=str(candidate),
        module="general_ludd.example",
    )
    verdict = SimpleNamespace(promote=True, to_dict=lambda: {"winner": "candidate"})

    with (
        patch.object(benchmark, "import_module_workload", return_value=object()),
        patch.object(benchmark, "run_ab", return_value=verdict),
    ):
        assert await endpoint(request) == {"verdict": {"winner": "candidate"}, "promote": True}

    outside = benchmark.ABTestRequest(
        baseline_root="/var/gludd-outside",
        candidate_root=str(candidate),
        module="general_ludd.example",
    )
    with pytest.raises(HTTPException, match="outside daemon-owned"):
        await endpoint(outside)

    missing = benchmark.ABTestRequest(
        baseline_root=str(tmp_path / "missing"),
        candidate_root=str(candidate),
        module="general_ludd.example",
    )
    with pytest.raises(HTTPException, match="does not exist"):
        await endpoint(missing)

    async def timeout_thread(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError

    with (
        patch.object(benchmark, "import_module_workload", return_value=object()),
        patch("asyncio.to_thread", side_effect=timeout_thread),
        pytest.raises(HTTPException, match="timed out") as exc_info,
    ):
        await endpoint(request)
    assert exc_info.value.status_code == 504


def test_database_backed_routes_serialize_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise every database-backed route and its commit boundary."""
    session = _Session()

    def factory() -> _SessionContext:
        return _SessionContext(session)

    app = FastAPI()
    monkeypatch.setattr(benchmark, "_get_session_factory", lambda _app: factory)
    monkeypatch.setattr(benchmark, "BenchmarkRepository", _BenchmarkRepo)
    monkeypatch.setattr(benchmark, "PromptProfileRepository", _PromptRepo)
    monkeypatch.setattr(benchmark, "AdaptiveRouter", _Router)
    benchmark.register(app, {})

    with TestClient(app) as client:
        scores = client.get("/admin/benchmark/scores", params={"task_type": "feature"})
        recent = client.get("/admin/benchmark/recent", params={"limit": 7})
        leaderboard = client.get("/admin/benchmark/leaderboard", params={"task_type": "bug_fix"})
        recorded = client.post(
            "/admin/benchmark/record",
            json={"model_profile_id": "model", "success": False, "scores": {"completion": 0.5}},
        )
        profiles = client.get("/admin/prompt-profiles")

    assert scores.json() == {"scores": [{"task_type": "feature", "score": 0.9}]}
    assert recent.json()["results"][0]["created_at"] is None
    assert leaderboard.json()["leaderboard"][0]["task_type"] == "bug_fix"
    assert recorded.json() == {"id": 17, "success": False}
    assert profiles.json()["profiles"][0]["name"] == "Prompt"
    assert session.committed is True
