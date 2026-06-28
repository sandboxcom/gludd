"""GET /api/environment `project` facet: declared topology relationships.

Phase-2 of the project-hierarchy feature surfaces a project's DECLARED edges
(parent/child/sibling/external) + their interface contracts through the
environment brief, so a running job (and an ansible role, via gludd_environment's
blanket pass-through) knows its position relative to neighbor projects.

These tests prove:
  - ``_project_facet`` lists every declared edge for a project that has a parent
    + children, using the REAL ProjectRelationshipRepository.list_for_project;
  - interface_contract JSON is parsed defensively (malformed -> {} hint kept);
  - it fails soft to {} when no rel_repo / session factory is wired, when the
    repo raises, and when no project scope can be resolved;
  - the /api/environment response carries the ``project`` facet (top-level key);
  - inherited_knowledge stays {} (router borrowing is a later phase).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.environment import _project_facet, register

# ---------------------------------------------------------------------------
# Lightweight fakes: an edge row, a repo, and a session factory.
# ---------------------------------------------------------------------------


class _Edge:
    """ProjectRelationshipModel duck-type the facet reads off."""

    def __init__(
        self,
        relation_type: str,
        location_kind: str,
        location_value: str,
        *,
        controlled_by_gludd: bool = False,
        related_project_id: str | None = None,
        interface_hint: str | None = None,
        interface_contract: str = "{}",
    ) -> None:
        self.relation_type = relation_type
        self.location_kind = location_kind
        self.location_value = location_value
        self.controlled_by_gludd = controlled_by_gludd
        self.related_project_id = related_project_id
        self.interface_hint = interface_hint
        self.interface_contract = interface_contract


class _FakeRelRepo:
    """Mirrors the REAL ProjectRelationshipRepository.list_for_project signature."""

    def __init__(self, edges: list[_Edge], *, raises: bool = False) -> None:
        self._edges = edges
        self._raises = raises

    async def list_for_project(
        self, project_id: str, relation_type: str | None = None
    ) -> list[_Edge]:
        if self._raises:
            raise RuntimeError("boom")
        if relation_type is None:
            return list(self._edges)
        return [e for e in self._edges if e.relation_type == relation_type]


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


def _app_with_factory(factory: Any) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = factory
    return app


def _parent_and_children_edges() -> list[_Edge]:
    return [
        _Edge(
            "parent",
            "gludd_project_name",
            "acme-platform",
            controlled_by_gludd=True,
            related_project_id="proj-abc123",
            interface_hint="consumes platform auth: GET /oauth/introspect",
            interface_contract=json.dumps(
                {"direction": "consumes", "protocol": "http"}
            ),
        ),
        _Edge(
            "child",
            "directory",
            "./services/ledger",
            controlled_by_gludd=True,
            related_project_id="proj-ledger",
        ),
        _Edge(
            "child",
            "gludd_project_name",
            "notifications",
            controlled_by_gludd=True,
        ),
        _Edge(
            "external",
            "url",
            "https://api.stripe.com",
            controlled_by_gludd=False,
            interface_hint="calls Stripe Charges API; we observe only",
        ),
    ]


# ---------------------------------------------------------------------------
# _project_facet unit behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_facet_lists_declared_edges() -> None:
    app = _app_with_factory(_FakeSessionFactory())
    repo = _FakeRelRepo(_parent_and_children_edges())

    facet = await _project_facet(
        app, "proj-self", rel_repo_factory=lambda _s: repo
    )

    assert facet["project_id"] == "proj-self"
    assert facet["inherited_knowledge"] == {}
    rels = facet["relationships"]
    assert len(rels) == 4

    # The parent edge with a parsed structured contract + hint.
    parent = next(r for r in rels if r["relation_type"] == "parent")
    assert parent["location_kind"] == "gludd_project_name"
    assert parent["location_value"] == "acme-platform"
    assert parent["controlled_by_gludd"] is True
    assert parent["related_project_id"] == "proj-abc123"
    assert parent["interface"]["hint"] == "consumes platform auth: GET /oauth/introspect"
    assert parent["interface"]["contract"] == {
        "direction": "consumes",
        "protocol": "http",
    }

    # Two children present.
    children = [r for r in rels if r["relation_type"] == "child"]
    assert len(children) == 2
    assert {c["location_value"] for c in children} == {
        "./services/ledger",
        "notifications",
    }

    # External neighbor: not controlled, no structured contract -> {} but hint kept.
    external = next(r for r in rels if r["relation_type"] == "external")
    assert external["controlled_by_gludd"] is False
    assert external["related_project_id"] is None
    assert external["interface"]["contract"] == {}
    assert "Stripe" in external["interface"]["hint"]


@pytest.mark.asyncio
async def test_project_facet_parses_malformed_contract_defensively() -> None:
    app = _app_with_factory(_FakeSessionFactory())
    repo = _FakeRelRepo(
        [
            _Edge(
                "sibling",
                "gludd_project_name",
                "peer",
                interface_hint="peer hint",
                interface_contract="{not valid json",
            ),
            _Edge(
                "child",
                "gludd_project_name",
                "arr-contract",
                # A JSON array is not a contract object -> degrade to {}.
                interface_contract="[1, 2, 3]",
            ),
        ]
    )

    facet = await _project_facet(app, "proj-self", rel_repo_factory=lambda _s: repo)
    rels = {r["relation_type"]: r for r in facet["relationships"]}
    assert rels["sibling"]["interface"]["contract"] == {}
    assert rels["sibling"]["interface"]["hint"] == "peer hint"
    assert rels["child"]["interface"]["contract"] == {}


@pytest.mark.asyncio
async def test_project_facet_empty_when_no_session_factory() -> None:
    app = FastAPI()
    app.state._session_factory = None
    facet = await _project_facet(app, "proj-self")
    assert facet == {}


@pytest.mark.asyncio
async def test_project_facet_fail_soft_when_repo_raises() -> None:
    app = _app_with_factory(_FakeSessionFactory())
    repo = _FakeRelRepo([], raises=True)
    facet = await _project_facet(app, "proj-self", rel_repo_factory=lambda _s: repo)
    assert facet == {}


@pytest.mark.asyncio
async def test_project_facet_empty_when_no_scope_resolved() -> None:
    # No project_id and no project manager -> nothing to scope to -> {}.
    app = _app_with_factory(_FakeSessionFactory())
    facet = await _project_facet(app, None)
    assert facet == {}


@pytest.mark.asyncio
async def test_project_facet_defaults_to_single_active_project() -> None:
    app = _app_with_factory(_FakeSessionFactory())

    class _Proj:
        project_id = "proj-only"

    class _Manager:
        def list_active(self) -> list[_Proj]:
            return [_Proj()]

    app.state._project_manager = _Manager()
    repo = _FakeRelRepo([_Edge("parent", "gludd_project_name", "root")])
    facet = await _project_facet(app, None, rel_repo_factory=lambda _s: repo)
    assert facet["project_id"] == "proj-only"
    assert facet["relationships"][0]["relation_type"] == "parent"


@pytest.mark.asyncio
async def test_project_facet_no_default_when_multiple_active() -> None:
    app = _app_with_factory(_FakeSessionFactory())

    class _Proj:
        def __init__(self, pid: str) -> None:
            self.project_id = pid

    class _Manager:
        def list_active(self) -> list[_Proj]:
            return [_Proj("a"), _Proj("b")]

    app.state._project_manager = _Manager()
    facet = await _project_facet(app, None)
    assert facet == {}


# ---------------------------------------------------------------------------
# /api/environment carries the project facet (route-level)
# ---------------------------------------------------------------------------


def _bare_env_app(factory: Any = None) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = factory
    register(app, {})
    return app


def test_environment_response_includes_project_facet_key() -> None:
    # Even a bare app (no factory, no project) must include the top-level
    # ``project`` key, defaulting to {} — never absent, never a 500.
    client = TestClient(_bare_env_app(None))
    resp = client.get("/api/environment")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "project" in body
    assert body["project"] == {}


def test_environment_response_renders_project_facet_with_edges() -> None:
    app = _bare_env_app(_FakeSessionFactory())

    # Patch the repository the facet imports so the route exercises the real
    # facet code path end-to-end with declared edges.
    import general_ludd.db.repository as repo_mod

    edges = _parent_and_children_edges()

    class _Repo(_FakeRelRepo):
        def __init__(self, _session: Any) -> None:
            super().__init__(edges)

    orig = repo_mod.ProjectRelationshipRepository
    repo_mod.ProjectRelationshipRepository = _Repo  # type: ignore[assignment,misc]
    try:
        client = TestClient(app)
        resp = client.get("/api/environment", params={"project_id": "proj-self"})
    finally:
        repo_mod.ProjectRelationshipRepository = orig  # type: ignore[misc]

    assert resp.status_code == 200, resp.text
    project = resp.json()["project"]
    assert project["project_id"] == "proj-self"
    assert project["inherited_knowledge"] == {}
    assert len(project["relationships"]) == 4
    assert any(r["relation_type"] == "parent" for r in project["relationships"])
