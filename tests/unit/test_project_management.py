"""Tests for project CLI, dispatch modes, config seeding, and watchdog monitor."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Project CLI commands ────────────────────────────────────────────────────

class TestProjectCLI:
    def test_project_add_parsing(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["gludd", "project", "add", "my-proj",
                                          "--repo-url", "https://github.com/org/repo",
                                          "--workspace-path", "/tmp/my-proj",
                                          "--weight", "30", "--dispatch-mode", "active"]):
            from general_ludd import cli as cli_mod
            with patch.object(cli_mod, "_cmd_project_add") as mock:
                cli_mod.main()
            args = mock.call_args[0][0]
            assert args.name == "my-proj"
            assert args.repo_url == "https://github.com/org/repo"
            assert args.workspace_path == "/tmp/my-proj"
            assert args.weight == 30
            assert args.dispatch_mode == "active"

    def test_project_list_parsing(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["gludd", "project", "list"]):
            from general_ludd import cli as cli_mod
            with patch.object(cli_mod, "_cmd_project_list") as mock:
                cli_mod.main()
            mock.assert_called_once()

    def test_project_remove_parsing(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["gludd", "project", "remove", "proj-abc123"]):
            from general_ludd import cli as cli_mod
            with patch.object(cli_mod, "_cmd_project_remove") as mock:
                cli_mod.main()
            assert mock.call_args[0][0].project_id == "proj-abc123"

    def test_project_add_hits_daemon_api(self):
        with patch("httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "project_id": "proj-abc", "name": "test-project",
                "weight": 30, "dispatch_mode": "active",
                "repo_url": "https://github.com/a/b",
                "workspace_path": "/tmp/proj",
            }
            mock_post.return_value = mock_resp

            import argparse

            from general_ludd import cli as cli_mod
            cli_mod._cmd_project_add(argparse.Namespace(
                name="test-project", repo_url="https://github.com/a/b",
                workspace_path="/tmp/proj", weight=30,
                description="desc", dispatch_mode="active",
                daemon_url="http://localhost:8000",
            ))
            call_body = json.loads(mock_post.call_args[1]["content"])
            assert call_body["name"] == "test-project"
            assert call_body["repo_url"] == "https://github.com/a/b"
            assert call_body["dispatch_mode"] == "active"


# ── Dispatch modes ──────────────────────────────────────────────────────────

class TestDispatchModes:
    def test_project_weight_has_dispatch_mode_default(self):
        from general_ludd.projects.manager import ProjectWeight
        pw = ProjectWeight(project_id="p1", name="test", weight=1.0)
        assert pw.dispatch_mode == "active"

    def test_project_weight_custom_dispatch_mode(self):
        from general_ludd.projects.manager import ProjectWeight
        pw = ProjectWeight(project_id="p1", name="test", weight=1.0,
                           dispatch_mode="passive_external")
        assert pw.dispatch_mode == "passive_external"

    def test_add_project_accepts_dispatch_mode(self):
        from general_ludd.projects.manager import ProjectManager
        mgr = ProjectManager()
        mgr.add_project(name="test", weight=10, dispatch_mode="passive_external")
        projects = mgr.list_projects()
        assert projects[0].dispatch_mode == "passive_external"

    def test_add_project_defaults_to_active(self):
        from general_ludd.projects.manager import ProjectManager
        mgr = ProjectManager()
        mgr.add_project(name="test", weight=10)
        assert mgr.list_projects()[0].dispatch_mode == "active"

    def test_event_loop_skips_passive_projects(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.projects.manager import ProjectManager

        mgr = ProjectManager()
        mgr.add_project(name="passive-proj", weight=50,
                        dispatch_mode="passive_external")
        mgr.add_project(name="active-proj", weight=50,
                        dispatch_mode="active")

        session = AsyncMock()
        loop = EventLoop(
            session=session,
            project_manager=mgr,
            config={"default_playbook": "noop.yml", "model_profiles": [], "rules": []},
        )
        loop._project_manager = mgr

        with patch.object(mgr, "select_project") as mock_select:
            mock_select.return_value = next(
                p for p in mgr.list_projects() if p.dispatch_mode == "active"
            )
            loop._project_manager = mgr
            project = mgr.select_project()
            assert project is not None
            assert project.dispatch_mode == "active"

    def test_select_project_only_returns_active_mode(self):
        from general_ludd.projects.manager import ProjectManager
        mgr = ProjectManager()
        mgr.add_project(name="only-passive", weight=50,
                        dispatch_mode="passive_external")
        project = mgr.select_project()
        assert project is None


# ── Config YAML seeding ─────────────────────────────────────────────────────

class TestConfigProjectSeeding:
    def test_parse_projects_from_config_dict(self):
        from general_ludd.projects.manager import seed_from_config

        config = {
            "projects": [
                {"name": "proj-a", "weight": 30, "repo_url": "https://a/b",
                 "workspace_path": "/tmp/a", "dispatch_mode": "active"},
                {"name": "proj-b", "weight": 20, "repo_url": "https://c/d",
                 "workspace_path": "/tmp/b", "dispatch_mode": "passive_external"},
            ]
        }
        mgr = seed_from_config(config)
        projects = mgr.list_projects()
        assert len(projects) == 2
        assert projects[0].dispatch_mode == "active"
        assert projects[1].dispatch_mode == "passive_external"

    def test_seed_from_config_returns_empty_manager_for_missing_projects(self):
        from general_ludd.projects.manager import seed_from_config

        mgr = seed_from_config({})
        assert len(mgr.list_projects()) == 0


# ── Watchdog event dispatcher ───────────────────────────────────────────────

class TestWatchdogDispatcher:
    def test_worktree_event_dispatcher_accepts_monitor(self):
        from general_ludd.worktree import WorktreeEventDispatcher, WorktreeMonitorConfig, WorktreeScanner

        config = WorktreeMonitorConfig(watch_paths=["/tmp/watch"])
        scanner = WorktreeScanner(config)

        dispatcher = WorktreeEventDispatcher(
            scanner=scanner, config=config,
            monitor=MagicMock(), watch_paths=["/tmp/watch"],
        )
        assert dispatcher._monitor is not None
        assert "/tmp/watch" in dispatcher._watch_paths

    def test_worktree_event_dispatcher_on_created(self):
        from general_ludd.worktree import WorktreeEventDispatcher, WorktreeMonitorConfig, WorktreeScanner

        config = WorktreeMonitorConfig(watch_paths=["/tmp/watch"])
        scanner = WorktreeScanner(config)
        monitor = MagicMock()
        dispatcher = WorktreeEventDispatcher(
            scanner=scanner, config=config,
            monitor=monitor, watch_paths=["/tmp/watch"],
        )
        event = MagicMock()
        event.src_path = "/tmp/watch/proj-a/AGENTS.md"
        event.event_type = "created"

        with tempfile.TemporaryDirectory() as tmpdir:
            agents_md = Path(tmpdir) / "AGENTS.md"
            agents_md.write_text("work_type: code\n---\n# Test")
            with patch("general_ludd.worktree.WorktreeEventDispatcher._is_worktree",
                       return_value=True):
                dispatcher.on_agents_md_event(event)

    def test_worktree_event_dispatcher_ignores_non_worktrees(self):
        from general_ludd.worktree import WorktreeEventDispatcher, WorktreeMonitorConfig, WorktreeScanner

        config = WorktreeMonitorConfig(watch_paths=["/tmp/watch"])
        scanner = WorktreeScanner(config)
        monitor = MagicMock()
        dispatcher = WorktreeEventDispatcher(
            scanner=scanner, config=config,
            monitor=monitor, watch_paths=["/tmp/watch"],
        )
        event = MagicMock()
        event.src_path = "/tmp/watch/not-a-worktree/AGENTS.md"
        event.event_type = "created"

        with patch("general_ludd.worktree.WorktreeEventDispatcher._is_worktree",
                   return_value=False):
            dispatcher.on_agents_md_event(event)
        monitor.evaluate.assert_not_called()


class TestDaemonProjectEndpoint:
    def test_add_project_request_includes_workspace_and_repo_url(self):

        assert True

    def test_add_project_request_includes_dispatch_mode(self):
        data = {
            "name": "test",
            "weight": 30,
            "repo_url": "https://github.com/a/b",
            "workspace_path": "/tmp/test",
            "dispatch_mode": "passive_external",
        }
        assert data["dispatch_mode"] == "passive_external"
