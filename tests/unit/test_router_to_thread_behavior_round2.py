"""Behavioral tests (round 2): async router handlers offload sync-blocking work.

Round-2 companion to ``test_router_to_thread_behavior.py``. The accounting,
facts, observe, and projects routers each have ``async def`` FastAPI handlers
that call sync-blocking work (git ``diff --numstat`` subprocess, the
CodebaseIntrospector git/AST/coverage snapshot, blocking connector network
``query()``/``health_all()``, and a blocking ``git clone``). Those calls are
wrapped in ``await asyncio.to_thread(...)`` so they run on a worker thread
instead of freezing the event loop.

Each test monkeypatches the underlying blocking callable with a recorder that
detects, at call time, whether it is executing on the event loop thread. A
coroutine body runs on the loop thread, where ``asyncio.get_running_loop()``
succeeds; a ``to_thread`` worker runs on a plain thread, where it raises
``RuntimeError``. So ``on_loop is False`` proves the call was genuinely
offloaded. If a handler regressed to calling the sync work inline, the recorder
would run on the loop thread and the assertion would fail.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _on_event_loop_thread() -> bool:
    """True if running on a thread that owns a live asyncio event loop."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_accounting_project_loc_offloaded(monkeypatch) -> None:
    import general_ludd.routers.accounting as accounting

    seen: dict[str, bool] = {}

    def fake_loc(repo_dir: Any) -> int:
        seen["on_loop"] = _on_event_loop_thread()
        return 0

    # The blocking `git diff --numstat` subprocess lives in _project_loc_changed,
    # invoked (via the loc_provider closure) inside accountant.account_for().
    monkeypatch.setattr(accounting, "_project_loc_changed", fake_loc)
    app = FastAPI()
    accounting.register(app, {})
    client = TestClient(app)

    resp = client.get("/api/accounting/demo-project")
    assert resp.status_code == 200
    assert resp.json()["project_id"] == "demo-project"
    # The git subprocess ran off the event loop thread.
    assert seen.get("on_loop") is False


def test_facts_codebase_facet_offloaded(monkeypatch) -> None:
    import general_ludd.routers.facts as facts

    seen: dict[str, bool] = {}

    def fake_codebase(app: FastAPI, recent_failures: Any = None) -> dict[str, Any]:
        seen["on_loop"] = _on_event_loop_thread()
        return {"churn": None}

    # _codebase_facet runs the blocking CodebaseIntrospector snapshot (git
    # subprocess + whole-tree AST parse + coverage.xml parse).
    monkeypatch.setattr(facts, "_codebase_facet", fake_codebase)
    app = FastAPI()
    facts.register(app, {})
    client = TestClient(app)

    resp = client.get("/api/facts")
    assert resp.status_code == 200
    assert resp.json()["codebase"] == {"churn": None}
    # The introspector snapshot ran off the event loop thread.
    assert seen.get("on_loop") is False


class _FakeRegistry:
    """Minimal stand-in for ConnectorRegistry with recording blocking methods."""

    def __init__(self, seen: dict[str, bool]) -> None:
        self._seen = seen

    def get(self, name: str) -> object:
        return object()  # non-None: source exists

    def query(self, name: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
        self._seen["query_on_loop"] = _on_event_loop_thread()
        return [{"ok": True}]

    def health_all(self) -> dict[str, dict[str, Any]]:
        self._seen["health_on_loop"] = _on_event_loop_thread()
        return {"src": {"ok": True}}


def test_observe_query_offloaded(monkeypatch) -> None:
    import general_ludd.routers.observe as observe

    seen: dict[str, bool] = {}
    fake_reg = _FakeRegistry(seen)
    monkeypatch.setattr(observe, "_get_registry", lambda app: fake_reg)

    app = FastAPI()
    observe.register(app, {})
    client = TestClient(app)

    resp = client.post("/api/observe/query", json={"source": "src", "spec": {}})
    assert resp.status_code == 200
    assert resp.json()["records"] == [{"ok": True}]
    # The blocking connector network query ran off the event loop thread.
    assert seen.get("query_on_loop") is False


def test_observe_health_offloaded(monkeypatch) -> None:
    import general_ludd.routers.observe as observe

    seen: dict[str, bool] = {}
    fake_reg = _FakeRegistry(seen)
    monkeypatch.setattr(observe, "_get_registry", lambda app: fake_reg)

    app = FastAPI()
    observe.register(app, {})
    client = TestClient(app)

    resp = client.get("/api/observe/health")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    # The serial blocking health sweep ran off the event loop thread.
    assert seen.get("health_on_loop") is False


class _FakeProject:
    def __init__(self, **kw: Any) -> None:
        self.project_id = "proj-1"
        self.name = kw.get("name", "")
        self.weight = kw.get("weight", 0.0)
        self.description = kw.get("description", "")
        self.repo_url = kw.get("repo_url", "")
        self.workspace_path = kw.get("workspace_path", "")
        self.dispatch_mode = kw.get("dispatch_mode", "active")
        self.active = True


class _FakeProjects:
    def add_project(self, **kw: Any) -> _FakeProject:
        return _FakeProject(**kw)


def test_projects_materialize_offloaded(monkeypatch) -> None:
    import general_ludd.daemon as daemon
    import general_ludd.projects.manager as manager
    import general_ludd.routers.projects as projects

    seen: dict[str, bool] = {}

    # Isolate the handler from real daemon subsystems.
    monkeypatch.setattr(
        daemon,
        "_get_or_create_extended_subsystems",
        lambda app: {"projects": _FakeProjects()},
    )

    def fake_materialize(
        repo_url: str, workspace_path: str, base_dir: str = "/tmp/gludd-workspaces"
    ) -> str | None:
        seen["on_loop"] = _on_event_loop_thread()
        return "/tmp/whatever"

    # materialize_project_workspace runs a blocking git clone.
    monkeypatch.setattr(manager, "materialize_project_workspace", fake_materialize)

    app = FastAPI()
    projects.register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/admin/projects",
        json={
            "name": "demo",
            "weight": 1.0,
            "repo_url": "https://example.com/repo.git",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["project_id"] == "proj-1"
    # The blocking git clone ran off the event loop thread.
    assert seen.get("on_loop") is False
