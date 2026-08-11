"""Structural and behavioral tests for routers/worktree.py."""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.worktree import _MAX_WATCH_PATHS, register

CONSTANTS = {"MAX_WATCH_PATHS": 100}


class TestModuleImport:
    def test_register_is_callable(self) -> None:
        assert callable(register)

    def test_register_accepts_two_args(self) -> None:
        app = FastAPI()
        try:
            register(app, {})
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc


class TestConstants:
    def test_max_watch_paths_value(self) -> None:
        assert isinstance(_MAX_WATCH_PATHS, int)
        assert _MAX_WATCH_PATHS == 100

    def test_max_watch_paths_matches_expected(self) -> None:
        assert CONSTANTS["MAX_WATCH_PATHS"] == _MAX_WATCH_PATHS


class TestRouteRegistration:
    EXPECTED_PATHS: ClassVar[set[str]] = {
        "/admin/worktree/scan",
        "/admin/worktree/status",
    }

    def test_registers_both_routes(self) -> None:
        app = FastAPI()
        register(app, {})
        registered = {r.path for r in app.routes}
        assert registered >= self.EXPECTED_PATHS

    def test_scan_is_post(self) -> None:
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/worktree/scan":
                assert "POST" in r.methods
                return
        pytest.fail("route /admin/worktree/scan not found")

    def test_status_is_get(self) -> None:
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/worktree/status":
                assert "GET" in r.methods
                return
        pytest.fail("route /admin/worktree/status not found")


class TestRegisterReturnsNone:
    def test_register_returns_none(self) -> None:
        app = FastAPI()
        result = register(app, {})
        assert result is None


class TestDaemonStateMutation:
    def test_register_does_not_mutate_daemon_state(self) -> None:
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        assert daemon_state == {}


class TestStatusEndpoint:
    def test_status_returns_tracked_worktrees(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.get("/admin/worktree/status")
        assert response.status_code == 200
        data = response.json()
        assert "tracked_worktrees" in data
        assert "tracked_count" in data
        assert isinstance(data["tracked_worktrees"], list)
        assert data["tracked_count"] == 0


class TestScanEndpoint:
    def test_scan_no_params_returns_todos(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/worktree/scan")
        assert response.status_code == 200
        data = response.json()
        assert "todos" in data
        assert "tracked_count" in data

    def test_scan_with_watch_paths(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post("/admin/worktree/scan", params={"watch_paths": "/tmp"})
        assert response.status_code == 200
        data = response.json()
        assert "todos" in data
        assert "tracked_count" in data

    def test_scan_path_count_boundary(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post(
            "/admin/worktree/scan",
            params={"watch_paths": ",".join(str(i) for i in range(100))},
        )
        assert response.status_code == 200

    def test_scan_path_count_exceeds_limit(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        response = client.post(
            "/admin/worktree/scan",
            params={"watch_paths": ",".join(str(i) for i in range(101))},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "watch_paths exceeds maximum allowed count"}
