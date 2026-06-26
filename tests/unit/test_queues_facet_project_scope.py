"""Regression test for XT-10: the environment `_queues_facet` must forward
`project_id` to the todo/work summaries so the queue-depth facet cannot surface
another tenant's backlog/in-flight counts.

Before the fix, `_queues_facet(app)` called `status_summary()` / `work_summary()`
with no project_id, returning cross-tenant aggregates even when the
`GET /api/environment` caller was scoped to a project. This pins that the
resolved project_id reaches both repository calls (and that None stays
unscoped for back-compat).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import general_ludd.db.repository as repo_mod
from general_ludd.routers.environment import _queues_facet


class _FakeApp:
    def __init__(self, factory: Any) -> None:
        # Instance attribute (NOT a class attr) so the factory is not bound as a
        # method when accessed via getattr(app.state, "_session_factory").
        self.state = SimpleNamespace(_session_factory=factory)


@asynccontextmanager
async def _fake_session_factory():
    yield object()  # the session is unused; repos are faked


def _install_capturing_repos(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict[str, Any] = {}

    class _FakeTodoRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def status_summary(self, project_id: str | None = None) -> dict:
            captured["todo_pid"] = project_id
            return {"backlog_size": 3}

    class _FakeWorkRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def work_summary(self, project_id: str | None = None) -> dict:
            captured["work_pid"] = project_id
            return {"in_flight": 2}

    monkeypatch.setattr(repo_mod, "TodoRepository", _FakeTodoRepo)
    monkeypatch.setattr(repo_mod, "TaskReturnRepository", _FakeWorkRepo)
    return captured


async def test_queues_facet_forwards_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_capturing_repos(monkeypatch)
    app = _FakeApp(_fake_session_factory)

    queues = await _queues_facet(app, project_id="proj-a")

    assert captured["todo_pid"] == "proj-a"
    assert captured["work_pid"] == "proj-a"
    # The facet still builds its queue list from the (now-scoped) summaries.
    depths = {q["name"]: q["depth"] for q in queues}
    assert depths == {"todos": 3, "work_in_flight": 2}


async def test_queues_facet_none_is_unscoped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_capturing_repos(monkeypatch)
    app = _FakeApp(_fake_session_factory)

    await _queues_facet(app)

    assert captured["todo_pid"] is None
    assert captured["work_pid"] is None
