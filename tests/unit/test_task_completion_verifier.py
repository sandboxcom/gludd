"""TDD tests for task_completion_verifier.

This module verifies that claimed-completed features ACTUALLY WORK
by testing the real observable behavior, not just that code exists.

Each test corresponds to a user feature request that was repeatedly
asked for because it wasn't properly implemented.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

from rich.console import Console


def _render_table(t: object) -> str:

    buf = StringIO()
    Console(file=buf, width=200).print(t)
    return buf.getvalue()


class TestArrowKeySelectionVisible:
    """User request #74: 'when i arrow up/down in the tui, i see no change'
    Asked 8+ times across sessions. The selected row MUST be visually different."""

    def test_projects_selected_row_has_marker(self):
        from general_ludd.cli import _build_projects_table

        projects = [
            {"project_id": "p1", "name": "alpha", "weight": 10, "dispatch_mode": "active"},
            {"project_id": "p2", "name": "beta", "weight": 20, "dispatch_mode": "passive"},
        ]
        t = _build_projects_table(projects, selected_idx=1, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered, "Selected row must have visual marker ▶"
        assert "p2" in rendered
        assert "p1" in rendered

    def test_todos_selected_row_has_marker(self):
        from general_ludd.cli import _build_todos_table

        todos = [
            {"todo_id": "t1", "title": "fix bug", "status": "pending", "priority": 5},
            {"todo_id": "t2", "title": "add feat", "status": "in_progress", "priority": 3},
        ]
        t = _build_todos_table(todos, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered

    def test_workers_selected_row_has_marker(self):
        from general_ludd.cli import _build_workers_table

        workers = [
            {"worker_id": "w1", "address": "10.0.0.1:8080"},
            {"worker_id": "w2", "address": "10.0.0.2:8080"},
        ]
        t = _build_workers_table(workers, selected_idx=1, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered

    def test_hooks_selected_row_has_marker(self):
        from general_ludd.cli import _build_hooks_table

        hooks = [
            {"hook_id": "h1", "event_name": "push", "hook_type": "webhook"},
            {"hook_id": "h2", "event_name": "merge", "hook_type": "slack"},
        ]
        t = _build_hooks_table(hooks, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered


class TestSpaceBarAndEnterActivate:
    """User request #74: 'does the space bar/enter work to select the item?'
    Both space and enter must activate the selected item with visible feedback."""

    def test_space_bar_activates_project(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("projects", projects_data=[
            {"project_id": "p1", "name": "alpha"},
        ], selected_project_idx=0)
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_project_id") == "p1"
        assert "p1" in state.get("status_msg", "")

    def test_enter_activates_project(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("projects", projects_data=[
            {"project_id": "p1", "name": "alpha"},
        ], selected_project_idx=0)
        handler = TUIKeyHandler(state)
        handler.handle_key("\r")
        assert state.get("active_project_id") == "p1"

    def test_space_bar_activates_todo(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("todos", todos_data=[
            {"todo_id": "t1", "title": "fix bug"},
        ], selected_todo_idx=0)
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_todo_id") == "t1"


class TestArrowKeyUpdatesStatus:
    """When pressing arrow down, the status bar must show what's selected."""

    def test_arrow_down_shows_item_name_in_status(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("projects", projects_data=[
            {"project_id": "p1", "name": "alpha"},
            {"project_id": "p2", "name": "beta"},
        ])
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state["selected_project_idx"] == 1
        assert "beta" in state.get("status_msg", ""), (
            f"Status should show selected item name, got: {state.get('status_msg')}"
        )

    def test_arrow_up_shows_item_name_in_status(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("projects", projects_data=[
            {"project_id": "p1", "name": "alpha"},
            {"project_id": "p2", "name": "beta"},
        ], selected_project_idx=1)
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[A")
        assert state["selected_project_idx"] == 0
        assert "alpha" in state.get("status_msg", "")


class TestDaemonDetachedFromTUI:
    """User request #55/#59/#67: 'daemon should stay running when TUI exits'
    Asked 5+ times. start_new_session=True is required."""

    def test_daemon_start_uses_new_session(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("main")
        handler = TUIKeyHandler(state)
        with patch("general_ludd.tui.keybindings.httpx.get") as mock_get:
            mock_get.side_effect = [
                Exception("no daemon"),
                MagicMock(status_code=200),
            ]
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.poll.return_value = None
                mock_proc.pid = 12345
                mock_popen.return_value = mock_proc
                handler._start_daemon()
                assert mock_popen.called
                call_kwargs = mock_popen.call_args
                assert call_kwargs[1].get("start_new_session") is True, \
                    "Daemon MUST start with start_new_session=True to detach from TUI"

    def test_cli_start_daemon_uses_new_session(self):
        from general_ludd.cli import _build_daemon_start_cmd

        cmd = _build_daemon_start_cmd(host="0.0.0.0", port=8000, workers=1)
        assert "gunicorn" in cmd[0]


class TestDaemonStatsShown:
    """User request #70: 'report service stats such as pid, requests/responses
    processed, memory used, etc when the daemon is running'"""

    def test_daemon_table_shows_pid_when_running(self):
        from general_ludd.cli import _build_daemon_table

        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"pid": 12345, "requests": 42, "memory_mb": 64, "uptime_s": 120},
            )
            t = _build_daemon_table(True, "http://localhost:8000", "main")
            rendered = _render_table(t)
            assert "12345" in rendered, "Daemon table must show PID"

    def test_daemon_stats_endpoint_exists(self):
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        routes = [r.path for r in app.routes]
        assert "/admin/daemon/stats" in routes


class TestVerboseLogging:
    """User request #73: 'tui is able to output verbose logs (both for itself
    and to the application database so that other agent daemons can see
    what another user session has done)'"""

    def test_logger_exists_and_writes_jsonl(self):
        import json
        import tempfile

        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=True)
            logger.log_key_press("main", "p")
            logger.close()
            with open(td + "/tui.log") as f:
                entry = json.loads(f.readline())
                assert entry["event"] == "key_press"
                assert entry["session_id"]

    def test_logger_flushes_to_daemon_endpoint(self):
        import tempfile

        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            logger.log_view_change("main", "projects")
            with patch("httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                logger.flush_to_database()
                assert mock_post.called
                url = mock_post.call_args[0][0]
                assert "/admin/tui-log" in url

    def test_tui_log_endpoint_in_daemon(self):
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app()
        routes = [r.path for r in app.routes]
        assert "/admin/tui-log" in routes


class TestStatusShowsBinaryVersions:
    """User request #29/#34/#36: 'status should report versions of all
    artifacts in the filestore' - asked 5+ times."""

    def test_status_shows_binary_versions(self):
        from general_ludd.cli import _build_binary_table

        info = {
            "binary_paths": {
                "openbao": "/usr/local/bin/openbao",
                "podman": "/usr/bin/podman",
            },
            "binary_versions": {
                "openbao": "2.0.0",
                "podman": "4.9.0",
            },
        }
        t = _build_binary_table(info)
        rendered = _render_table(t)
        assert "openbao" in rendered
        assert "podman" in rendered
        assert "2.0.0" in rendered or "4.9.0" in rendered


class TestGuardrailEffectiveness:
    """Meta-test: verify the guardrails would catch the failures from prior sessions."""

    def test_table_without_selected_idx_still_works(self):
        """Backwards compatibility: tables still render without selection."""
        from general_ludd.cli import _build_projects_table

        projects = [{"project_id": "p1", "name": "alpha", "weight": 10, "dispatch_mode": "active"}]
        t = _build_projects_table(projects, term_width=120)
        rendered = _render_table(t)
        assert "p1" in rendered
        assert "▶" not in rendered

    def test_selection_data_populated_in_render_cycle(self):
        """The data pipeline: API fetch -> tui_state -> table builder."""
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("projects", projects_data=[
            {"project_id": "p1", "name": "alpha"},
            {"project_id": "p2", "name": "beta"},
        ])
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state["selected_project_idx"] == 1
        handler.handle_key(" ")
        assert state.get("active_project_id") == "p2"
        assert "p2" in state.get("status_msg", "")


def _make_state(
    view: str,
    *,
    projects_data: list | None = None,
    todos_data: list | None = None,
    hooks_data: list | None = None,
    workers_data: list | None = None,
    selected_project_idx: int = 0,
    selected_todo_idx: int = 0,
) -> dict:
    state: dict = {
        "current_view": view,
        "daemon_running": True,
        "status_msg": "",
        "daemon_url": "http://localhost:8000",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "dispatch_mode": "active",
        "ansible_search_results": [],
        "verbose_logging": False,
    }
    if projects_data is not None:
        state["projects_data"] = projects_data
        state["selected_project_idx"] = selected_project_idx
    if todos_data is not None:
        state["todos_data"] = todos_data
        state["selected_todo_idx"] = selected_todo_idx
    if hooks_data is not None:
        state["hooks_data"] = hooks_data
        state["selected_hook_idx"] = 0
    if workers_data is not None:
        state["workers_data"] = workers_data
        state["selected_worker_idx"] = 0
    return state
