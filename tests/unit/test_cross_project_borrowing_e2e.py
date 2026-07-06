"""End-to-end wiring for project-hierarchy phase 3 (cross-project borrowing).

Phase 3 lets a thin-history project BORROW routing knowledge from its declared
neighbors (parent/child/sibling/external). The borrowing logic lived in the
``AdaptiveRouter`` + ``ProjectRelationshipRepository`` but was never wired into
the running daemon: the app-level router is global (relationship_repo=None) and
the ``/api/environment`` project facet hard-coded ``inherited_knowledge={}``.

These tests prove the wiring is now live AND strictly opt-in:

  - ``AdaptiveRouter.inherited_knowledge()`` returns ``{}`` for an unconfigured /
    global router (borrowing OFF, or no project_id / relationship_repo), and the
    resolved borrow graph + borrowed candidates when borrowing is ON;
  - the ``/api/environment`` project facet (``_project_facet`` /
    ``_inherited_knowledge_facet``) populates ``inherited_knowledge`` from a
    project-scoped router when ``relationship_routing.enable_cross_project_
    borrowing`` is true, and stays ``{}`` (backward compatible) when the flag is
    absent/false — at both the helper and the HTTP-route level.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.config.user_config import RelationshipRoutingConfig
from general_ludd.routers.environment import (
    _inherited_knowledge_facet,
    _project_facet,
    register,
)
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter

# ---------------------------------------------------------------------------
# Fakes: a declared edge, the relationship repo, the benchmark repo, a session.
# ---------------------------------------------------------------------------


class _Edge:
    """ProjectRelationshipModel duck-type the router + facet read off."""

    def __init__(
        self,
        relation_type: str,
        related_project_id: str,
        *,
        controlled_by_gludd: bool = True,
        location_kind: str = "gludd_project_name",
        location_value: str = "neighbor",
        interface_hint: str | None = None,
        interface_contract: str = "{}",
    ) -> None:
        self.relation_type = relation_type
        self.related_project_id = related_project_id
        self.controlled_by_gludd = controlled_by_gludd
        self.location_kind = location_kind
        self.location_value = location_value
        self.interface_hint = interface_hint
        self.interface_contract = interface_contract


class _FakeRelRepo:
    """Mirrors ProjectRelationshipRepository.list_for_project(project_id, ...).

    ``edges_by_project`` maps a project id to its declared out-edges, so the
    router's BFS resolves a real (small) graph rather than one flat edge list.
    """

    def __init__(self, edges_by_project: dict[str, list[_Edge]]) -> None:
        self._edges = edges_by_project
        self.calls: list[str] = []

    async def list_for_project(
        self, project_id: str, relation_type: str | None = None
    ) -> list[_Edge]:
        self.calls.append(project_id)
        edges = self._edges.get(project_id, [])
        if relation_type is None:
            return list(edges)
        return [e for e in edges if e.relation_type == relation_type]


class _FakeBenchRepo:
    """Benchmark repo whose get_aggregate_scores is keyed by project_id kwarg."""

    def __init__(self, scores_by_project: dict[str, list[dict[str, Any]]]) -> None:
        self._scores = scores_by_project

    async def get_aggregate_scores(
        self, task_type: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        return list(self._scores.get(project_id, []))


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


def _agg(model_id: str, project_id: str, *, score: float = 0.9) -> dict[str, Any]:
    return {
        "model_profile_id": model_id,
        "prompt_profile_id": None,
        "task_type": TaskType.BUG_FIX.value,
        "composite_score": score,
        "avg_cost": 0.001,
        "sample_count": 5,
        "project_id": project_id,
    }


def _borrowing_config(*, enabled: bool) -> Any:
    """A startup-config dict shaped like the daemon's ``_startup_config``.

    Uses the REAL ``RelationshipRoutingConfig`` so the test exercises the same
    config object ``_borrowing_config`` reads at runtime, not a look-alike.
    """
    rr = RelationshipRoutingConfig(enable_cross_project_borrowing=enabled)
    return {"user_config": SimpleNamespace(relationship_routing=rr)}


# ---------------------------------------------------------------------------
# AdaptiveRouter.inherited_knowledge() — the borrow surface itself.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_inherited_knowledge_empty_when_off() -> None:
    # Borrowing OFF (default) -> nothing inherited even with a repo + project.
    rel_repo = _FakeRelRepo({"proj-child": [_Edge("parent", "proj-parent")]})
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    router = AdaptiveRouter(
        benchmark_repo=bench,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=False,
    )
    assert await router.inherited_knowledge() == {}
    # OFF must not even touch the relationship graph.
    assert rel_repo.calls == []


@pytest.mark.asyncio
async def test_router_inherited_knowledge_empty_without_project_or_repo() -> None:
    bench = _FakeBenchRepo({})
    # Global router (no project_id) -> {}.
    r1 = AdaptiveRouter(
        benchmark_repo=bench,
        relationship_repo=_FakeRelRepo({}),
        enable_cross_project_borrowing=True,
    )
    assert await r1.inherited_knowledge() == {}
    # Project but no relationship_repo -> {}.
    r2 = AdaptiveRouter(
        benchmark_repo=bench,
        project_id="proj-child",
        enable_cross_project_borrowing=True,
    )
    assert await r2.inherited_knowledge() == {}


@pytest.mark.asyncio
async def test_router_inherited_knowledge_borrows_neighbors() -> None:
    rel_repo = _FakeRelRepo(
        {
            "proj-child": [
                _Edge("parent", "proj-parent", controlled_by_gludd=True),
            ],
            "proj-parent": [],
        }
    )
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    router = AdaptiveRouter(
        benchmark_repo=bench,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
    )
    inherited = await router.inherited_knowledge()

    assert inherited.get("enabled") is True
    sources = inherited["sources"]
    assert "proj-parent" in sources
    src = sources["proj-parent"]
    assert src["relation_type"] == "parent"
    assert src["edge_distance"] == 1
    assert src["controlled"] is True
    assert src["weight"] > 0.0
    cand_models = {c["model_profile_id"] for c in src["borrowed_candidates"]}
    assert "parent-model" in cand_models


@pytest.mark.asyncio
async def test_router_inherited_knowledge_does_not_corrupt_route() -> None:
    # inherited_knowledge() must not leave state that biases a subsequent route().
    rel_repo = _FakeRelRepo({"proj-child": [_Edge("parent", "proj-parent")]})
    bench = _FakeBenchRepo(
        {
            "proj-child": [],  # thin own history -> route() will borrow
            "proj-parent": [_agg("parent-model", "proj-parent")],
        }
    )
    router = AdaptiveRouter(
        benchmark_repo=bench,
        project_id="proj-child",
        relationship_repo=rel_repo,
        enable_cross_project_borrowing=True,
    )
    await router.inherited_knowledge()
    decision = await router.route(TaskType.BUG_FIX)
    assert decision.selected_model_profile_id == "parent-model"
    assert decision.reason == "inherited_parent_history"


# ---------------------------------------------------------------------------
# _inherited_knowledge_facet — the environment.py wiring helper.
# ---------------------------------------------------------------------------


def _app(*, enabled: bool, bench: _FakeBenchRepo | None = None) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = _FakeSessionFactory()
    app.state._startup_config = _borrowing_config(enabled=enabled)
    if bench is not None:
        app.state._adaptive_router = SimpleNamespace(_repo=bench)
    return app


@pytest.mark.asyncio
async def test_inherited_knowledge_facet_empty_when_flag_off() -> None:
    rel_repo = _FakeRelRepo({"proj-child": [_Edge("parent", "proj-parent")]})
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    app = _app(enabled=False, bench=bench)
    facet = await _inherited_knowledge_facet(
        app, "proj-child", rel_repo, app.state._session_factory
    )
    assert facet == {}


@pytest.mark.asyncio
async def test_inherited_knowledge_facet_populated_when_flag_on() -> None:
    rel_repo = _FakeRelRepo(
        {
            "proj-child": [_Edge("parent", "proj-parent", controlled_by_gludd=True)],
            "proj-parent": [],
        }
    )
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    app = _app(enabled=True, bench=bench)
    facet = await _inherited_knowledge_facet(
        app, "proj-child", rel_repo, app.state._session_factory
    )
    assert facet.get("enabled") is True
    assert "proj-parent" in facet["sources"]
    assert facet["sources"]["proj-parent"]["relation_type"] == "parent"


# ---------------------------------------------------------------------------
# _project_facet: default-off vs. flag-on inherited_knowledge population.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_facet_inherited_empty_by_default() -> None:
    # No relationship_routing config -> borrowing OFF -> inherited_knowledge {}.
    app = FastAPI()
    app.state._session_factory = _FakeSessionFactory()
    rel_repo = _FakeRelRepo({"proj-child": [_Edge("parent", "proj-parent")]})
    facet = await _project_facet(
        app, "proj-child", rel_repo_factory=lambda _s: rel_repo
    )
    assert facet["project_id"] == "proj-child"
    assert facet["inherited_knowledge"] == {}
    assert len(facet["relationships"]) == 1


@pytest.mark.asyncio
async def test_project_facet_inherited_populated_when_flag_on() -> None:
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    app = _app(enabled=True, bench=bench)
    rel_repo = _FakeRelRepo(
        {
            "proj-child": [_Edge("parent", "proj-parent", controlled_by_gludd=True)],
            "proj-parent": [],
        }
    )
    facet = await _project_facet(
        app, "proj-child", rel_repo_factory=lambda _s: rel_repo
    )
    inherited = facet["inherited_knowledge"]
    assert inherited.get("enabled") is True
    src = inherited["sources"]["proj-parent"]
    assert src["relation_type"] == "parent"
    assert any(
        c["model_profile_id"] == "parent-model" for c in src["borrowed_candidates"]
    )


# ---------------------------------------------------------------------------
# HTTP route-level e2e: GET /api/environment carries populated inheritance.
# ---------------------------------------------------------------------------


def _route_app(*, enabled: bool, bench: _FakeBenchRepo, edges: dict[str, list[_Edge]]):
    app = FastAPI()
    app.state._session_factory = _FakeSessionFactory()
    app.state._startup_config = _borrowing_config(enabled=enabled)
    app.state._adaptive_router = SimpleNamespace(_repo=bench)
    register(app, {})
    return app


def test_environment_route_inherits_knowledge_when_flag_on() -> None:
    import general_ludd.db.repository as repo_mod

    edges = {
        "proj-child": [_Edge("parent", "proj-parent", controlled_by_gludd=True)],
        "proj-parent": [],
    }
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    app = _route_app(enabled=True, bench=bench, edges=edges)

    class _Repo(_FakeRelRepo):
        def __init__(self, _session: Any) -> None:
            super().__init__(edges)

    orig = repo_mod.ProjectRelationshipRepository
    cast(Any, repo_mod).ProjectRelationshipRepository = _Repo
    try:
        client = TestClient(app)
        resp = client.get("/api/environment", params={"project_id": "proj-child"})
    finally:
        cast(Any, repo_mod).ProjectRelationshipRepository = orig

    assert resp.status_code == 200, resp.text
    project = resp.json()["project"]
    assert project["project_id"] == "proj-child"
    inherited = project["inherited_knowledge"]
    assert inherited.get("enabled") is True
    assert "proj-parent" in inherited["sources"]


def test_environment_route_no_inheritance_when_flag_off() -> None:
    import general_ludd.db.repository as repo_mod

    edges = {"proj-child": [_Edge("parent", "proj-parent", controlled_by_gludd=True)]}
    bench = _FakeBenchRepo({"proj-parent": [_agg("parent-model", "proj-parent")]})
    app = _route_app(enabled=False, bench=bench, edges=edges)

    class _Repo(_FakeRelRepo):
        def __init__(self, _session: Any) -> None:
            super().__init__(edges)

    orig = repo_mod.ProjectRelationshipRepository
    cast(Any, repo_mod).ProjectRelationshipRepository = _Repo
    try:
        client = TestClient(app)
        resp = client.get("/api/environment", params={"project_id": "proj-child"})
    finally:
        cast(Any, repo_mod).ProjectRelationshipRepository = orig

    assert resp.status_code == 200, resp.text
    project = resp.json()["project"]
    assert project["inherited_knowledge"] == {}
