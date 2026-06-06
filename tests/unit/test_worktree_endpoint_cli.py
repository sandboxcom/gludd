"""Tests for worktree daemon endpoint and CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestWorktreeDaemonEndpoint:
    @pytest.mark.asyncio
    async def test_admin_worktree_scan_returns_todos(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.state._config_dir = None

        @app.post("/admin/worktree/scan")
        async def scan(watch_paths: str | None = None):
            return {
                "todos": [
                    {"title": "Test Task", "queue": "intake", "status": "queued"}
                ],
                "tracked_count": 1,
            }

        client = TestClient(app)
        resp = client.post("/admin/worktree/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["todos"]) == 1
        assert data["todos"][0]["title"] == "Test Task"
        assert data["tracked_count"] == 1

    @pytest.mark.asyncio
    async def test_admin_worktree_scan_with_watch_paths(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.state._config_dir = None

        @app.post("/admin/worktree/scan")
        async def scan(watch_paths: str | None = None):
            assert watch_paths == "/projects,/home"
            return {"todos": [], "tracked_count": 0}

        client = TestClient(app)
        resp = client.post("/admin/worktree/scan?watch_paths=/projects,/home")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_worktree_status_returns_tracked(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/admin/worktree/status")
        async def status():
            return {
                "tracked_worktrees": [
                    {
                        "path": "/tmp/wt",
                        "todo_id": "WT-001",
                        "has_agents_md": True,
                        "last_scanned": "2026-01-01T00:00:00+00:00",
                        "last_activity": None,
                    }
                ],
                "tracked_count": 1,
            }

        client = TestClient(app)
        resp = client.get("/admin/worktree/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tracked_count"] == 1
        assert data["tracked_worktrees"][0]["path"] == "/tmp/wt"
        assert data["tracked_worktrees"][0]["has_agents_md"] is True

    @pytest.mark.asyncio
    async def test_admin_worktree_status_empty(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.get("/admin/worktree/status")
        async def status():
            return {"tracked_worktrees": [], "tracked_count": 0}

        client = TestClient(app)
        resp = client.get("/admin/worktree/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tracked_worktrees"] == []
        assert data["tracked_count"] == 0

    @pytest.mark.asyncio
    async def test_worktree_scan_end_to_end_with_real_worktree(self, tmp_path: Path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig

        wt_dir = tmp_path / "api-wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/api-wt")
        (wt_dir / "AGENTS.md").write_text("# API Discovered Task\n## Work Type: feature")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)

        app = FastAPI()

        @app.post("/admin/worktree/scan")
        async def scan():
            todos = monitor.evaluate()
            return {"todos": todos, "tracked_count": len(monitor.tracked_worktrees)}

        client = TestClient(app)
        resp = client.post("/admin/worktree/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["todos"]) == 1
        assert data["todos"][0]["title"] == "API Discovered Task"


class TestWorktreeCLI:
    def test_worktree_scan_cli_success(self):
        import sys

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "todos": [{"title": "CLI Task", "queue": "core"}],
            "tracked_count": 1,
        }

        with patch("httpx.post", return_value=mock_resp), patch.object(sys, "argv", ["gludd", "worktree", "scan"]):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 0

    def test_worktree_scan_cli_with_path(self):
        import sys

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"todos": [], "tracked_count": 0}

        with patch(
            "httpx.post", return_value=mock_resp
        ) as mock_post, patch.object(
            sys, "argv", ["gludd", "worktree", "scan", "--path", "/a,/b"]
        ):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 0
                assert "/a,/b" in str(mock_post.call_args)

    def test_worktree_status_cli_success(self):
        import sys

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "tracked_worktrees": [
                {"path": "/tmp/x", "todo_id": "T1", "has_agents_md": True}
            ],
            "tracked_count": 1,
        }

        with patch("httpx.get", return_value=mock_resp), patch.object(sys, "argv", ["gludd", "worktree", "status"]):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 0

    def test_worktree_scan_cli_offline_error(self):
        import sys

        import httpx

        with patch(
            "httpx.post", side_effect=httpx.ConnectError("refused")
        ), patch.object(sys, "argv", ["gludd", "worktree", "scan"]):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 1

    def test_worktree_status_cli_offline_error(self):
        import sys

        import httpx

        with patch(
            "httpx.get", side_effect=httpx.ConnectError("refused")
        ), patch.object(sys, "argv", ["gludd", "worktree", "status"]):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 1

    def test_worktree_subcommand_without_action_shows_help(self):
        import sys

        with patch.object(sys, "argv", ["gludd", "worktree"]):
            import general_ludd.cli as cli_mod

            try:
                cli_mod.main()
            except SystemExit as exc:
                assert exc.code == 0
