"""Regression test for TG-1: read/update todo endpoints must reject an unknown
or missing ``project_id`` in multi-project mode with the SAME 422 contract as
CREATE — not a 404 ("not found") or a silent empty result.

Before the fix, ``GET /api/todos/{id}``, ``GET /api/todos/scheduled``,
``POST /api/todos/{id}/schedule/pause`` and ``.../resume`` either 404'd or
scoped to an empty list when handed a project_id that does not exist, while
CREATE returned 422. The shared ``_validate_project_id`` helper unifies the
contract: when a ProjectManager exists AND has >=1 active project, a null
project_id is 422 ("required") and an unknown id is 422 ("unknown"). When no
projects are active the field is unconstrained (single-project / no-PM
back-compat).
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.todos import register


def _build_client(*, with_active_project: bool) -> TestClient:
    """A FastAPI app with the todo routes registered.

    When ``with_active_project`` is True the app exposes a ProjectManager whose
    ``list_active()`` returns a single project ``proj-known`` — so the TG-1
    validator engages. When False, no project manager is set, exercising the
    single-project / no-PM back-compat path where project_id is unconstrained.
    """
    app = FastAPI()
    register(app, {})
    if with_active_project:
        app.state._project_manager = SimpleNamespace(
            list_active=lambda: [SimpleNamespace(project_id="proj-known")]
        )
    # Intentionally no _session_factory: validation happens BEFORE any DB access,
    # so the 422 paths never reach a session. The back-compat path returns [].
    return TestClient(app, raise_server_exceptions=False)


def test_get_todo_unknown_project_is_422_not_404() -> None:
    client = _build_client(with_active_project=True)
    resp = client.get("/api/todos/TODO-ABCD1234", params={"project_id": "proj-unknown"})
    assert resp.status_code == 422
    assert "Unknown project_id" in resp.json()["detail"]


def test_list_scheduled_unknown_project_is_422() -> None:
    client = _build_client(with_active_project=True)
    resp = client.get("/api/todos/scheduled", params={"project_id": "proj-unknown"})
    assert resp.status_code == 422


def test_pause_schedule_unknown_project_is_422() -> None:
    client = _build_client(with_active_project=True)
    resp = client.post(
        "/api/todos/TODO-ABCD1234/schedule/pause",
        params={"project_id": "proj-unknown"},
    )
    assert resp.status_code == 422


def test_resume_schedule_unknown_project_is_422() -> None:
    client = _build_client(with_active_project=True)
    resp = client.post(
        "/api/todos/TODO-ABCD1234/schedule/resume",
        params={"project_id": "proj-unknown"},
    )
    assert resp.status_code == 422


def test_known_project_passes_validation() -> None:
    # A KNOWN project_id must NOT be rejected by the validator. With no session
    # factory wired, api_get_todo falls through to its 404 ("Todo not found"),
    # proving validation passed (it did not 422 on the project_id).
    client = _build_client(with_active_project=True)
    resp = client.get("/api/todos/TODO-ABCD1234", params={"project_id": "proj-known"})
    assert resp.status_code == 404


def test_no_project_manager_is_back_compat() -> None:
    # Single-project / no-PM mode: project_id is unconstrained. The scheduled
    # list with no project_id must NOT 422 — it returns an empty list via the
    # degraded (no session factory) path.
    client = _build_client(with_active_project=False)
    resp = client.get("/api/todos/scheduled")
    assert resp.status_code == 200
    assert resp.json() == []
