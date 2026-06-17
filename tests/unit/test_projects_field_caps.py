"""Unit tests for AddProjectRequest field-length/dispatch_mode caps and persist-failure surfacing.

Covers:
  - Over-long name rejected with 422
  - Over-long description rejected with 422
  - Invalid dispatch_mode rejected with 422
  - Valid project add still returns 200
  - DB persist failure surfaces as non-200 (not a silent pass)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import projects as projects_router


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with the projects router registered."""
    app = FastAPI()
    projects_router.register(app, {})
    return app


def _mock_project(name: str = "test-proj", project_id: str = "proj-abc123") -> MagicMock:
    p = MagicMock()
    p.project_id = project_id
    p.name = name
    p.weight = 10.0
    p.description = ""
    p.repo_url = ""
    p.workspace_path = ""
    p.dispatch_mode = "active"
    p.active = True
    return p


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": "my-project", "weight": 10.0}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Field-length / pattern validation (pydantic rejects before the handler)
# ---------------------------------------------------------------------------


def test_name_too_long_is_rejected() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    long_name = "x" * 256  # max_length=255
    resp = client.post("/admin/projects", json=_valid_payload(name=long_name))
    assert resp.status_code == 422


def test_name_empty_is_rejected() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/admin/projects", json=_valid_payload(name=""))
    assert resp.status_code == 422


def test_description_too_long_is_rejected() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    long_desc = "d" * 4097  # max_length=4096
    resp = client.post("/admin/projects", json=_valid_payload(description=long_desc))
    assert resp.status_code == 422


def test_invalid_dispatch_mode_is_rejected() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/admin/projects", json=_valid_payload(dispatch_mode="bogus_mode"))
    assert resp.status_code == 422


def test_valid_dispatch_modes_accepted() -> None:
    """All three valid dispatch_mode values must pass pydantic field validation."""
    from general_ludd.routers.projects import AddProjectRequest

    for mode in ("active", "passive_external", "worktree_monitor"):
        req = AddProjectRequest(name="proj", weight=10.0, dispatch_mode=mode)
        assert req.dispatch_mode == mode


# ---------------------------------------------------------------------------
# Valid add (no DB) returns 200
# ---------------------------------------------------------------------------


def test_valid_project_add_returns_200() -> None:
    app = _make_app()
    mock_project = _mock_project()

    mock_pm = MagicMock()
    mock_pm.add_project.return_value = mock_project

    with (
        patch(
            "general_ludd.daemon._get_or_create_extended_subsystems",
            return_value={"projects": mock_pm},
        ),
        patch(
            "general_ludd.routers.projects._get_session_factory",
            return_value=None,
        ),
        patch(
            "general_ludd.projects.manager.materialize_project_workspace",
            return_value=None,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/projects", json=_valid_payload())

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == mock_project.project_id
    assert data["name"] == mock_project.name


# ---------------------------------------------------------------------------
# Persist failure surfaces as an error (not silent 200)
# ---------------------------------------------------------------------------


def test_persist_failure_is_not_silent_200() -> None:
    """If persist_project raises, the response must NOT be 200.

    The router should surface either a 500 (HTTPException) rather than
    silently returning 200 with an in-memory-only project that won't survive
    a restart.
    """
    app = _make_app()
    mock_project = _mock_project()

    mock_pm = MagicMock()
    mock_pm.add_project.return_value = mock_project
    mock_pm.remove_project = MagicMock()

    # Build a fake async session context manager
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def _failing_persist(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("DB write failed")

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch(
            "general_ludd.daemon._get_or_create_extended_subsystems",
            return_value={"projects": mock_pm},
        ),
        patch(
            "general_ludd.routers.projects._get_session_factory",
            return_value=mock_factory,
        ),
        patch(
            "general_ludd.projects.manager.persist_project",
            side_effect=_failing_persist,
        ),
        patch(
            "general_ludd.projects.manager.materialize_project_workspace",
            return_value=None,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/projects", json=_valid_payload())

    # Must NOT be a silent success
    assert resp.status_code != 200, (
        f"Expected non-200 on persist failure but got {resp.status_code}: {resp.text}"
    )
