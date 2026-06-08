"""TDD tests for TUI selection highlighting.

These tests verify that:
1. Table builders accept selected_idx and render the selected row differently
2. tui_state is populated with fetched data so selection tracking works
3. Space bar activates the selected item with visible feedback
4. The selection index is shown in the status_msg after navigation
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table


def _render_table(t: Table) -> str:
    buf = StringIO()
    Console(file=buf, width=200).print(t)
    return buf.getvalue()


class TestProjectsTableSelectionHighlight:
    def test_selected_idx_highlights_row(self):
        from general_ludd.cli import _build_projects_table

        projects = [
            {"project_id": "p1", "name": "alpha", "weight": 10, "dispatch_mode": "active"},
            {"project_id": "p2", "name": "beta", "weight": 20, "dispatch_mode": "passive"},
        ]
        t = _build_projects_table(projects, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert "p1" in rendered
        assert "p2" in rendered
        assert (
            "▶" in rendered or "►" in rendered
            or "selected" in rendered.lower()
            or "[bold]" in rendered or "reverse" in rendered
        )

    def test_selected_idx_second_row(self):
        from general_ludd.cli import _build_projects_table

        projects = [
            {"project_id": "p1", "name": "alpha", "weight": 10, "dispatch_mode": "active"},
            {"project_id": "p2", "name": "beta", "weight": 20, "dispatch_mode": "passive"},
        ]
        t = _build_projects_table(projects, selected_idx=1, term_width=120)
        rendered = _render_table(t)
        assert "p1" in rendered
        assert "p2" in rendered
        assert "▶" in rendered or "►" in rendered or "[bold]" in rendered or "reverse" in rendered

    def test_no_selection_no_marker(self):
        from general_ludd.cli import _build_projects_table

        projects = [
            {"project_id": "p1", "name": "alpha", "weight": 10, "dispatch_mode": "active"},
        ]
        t = _build_projects_table(projects, selected_idx=None, term_width=120)
        rendered = _render_table(t)
        assert "▶" not in rendered
        assert "►" not in rendered


class TestTodosTableSelectionHighlight:
    def test_selected_idx_highlights_row(self):
        from general_ludd.cli import _build_todos_table

        todos = [
            {"todo_id": "t1", "title": "fix bug", "status": "pending", "priority": 5},
            {"todo_id": "t2", "title": "add feat", "status": "in_progress", "priority": 3},
        ]
        t = _build_todos_table(todos, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered or "►" in rendered or "[bold]" in rendered or "reverse" in rendered

    def test_no_selection_no_marker(self):
        from general_ludd.cli import _build_todos_table

        todos = [
            {"todo_id": "t1", "title": "fix bug", "status": "pending", "priority": 5},
        ]
        t = _build_todos_table(todos, selected_idx=None, term_width=120)
        rendered = _render_table(t)
        assert "▶" not in rendered


class TestHooksTableSelectionHighlight:
    def test_selected_idx_highlights_row(self):
        from general_ludd.cli import _build_hooks_table

        hooks = [
            {"hook_id": "h1", "event_name": "push", "hook_type": "webhook"},
            {"hook_id": "h2", "event_name": "merge", "hook_type": "slack"},
        ]
        t = _build_hooks_table(hooks, selected_idx=1, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered or "►" in rendered or "[bold]" in rendered or "reverse" in rendered


class TestWorkersTableSelectionHighlight:
    def test_selected_idx_highlights_row(self):
        from general_ludd.cli import _build_workers_table

        workers = [
            {"worker_id": "w1", "address": "10.0.0.1:8080"},
            {"worker_id": "w2", "address": "10.0.0.2:8080"},
        ]
        t = _build_workers_table(workers, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert "▶" in rendered or "►" in rendered or "[bold]" in rendered or "reverse" in rendered


class TestModelsTableSelectionHighlight:
    def test_selected_idx_highlights_row(self):
        from general_ludd.cli import _build_model_table

        servers = [{"server_id": "s1", "engine": "llamacpp", "model": "llama-7b", "status": "running"}]
        downloaded = [{"model_id": "d1", "engine": "llamacpp", "size_bytes": 4096}]
        t = _build_model_table(servers, downloaded, selected_idx=0, term_width=120)
        rendered = _render_table(t)
        assert isinstance(t, Table)
        assert "▶" in rendered or "reverse" in rendered


class TestSelectionDataPopulation:
    def test_handle_key_down_reads_from_state_data(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "projects",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "projects_data": [
                {"project_id": "p1", "name": "test", "weight": 10},
            ],
        }
        handler = TUIKeyHandler(state)
        handler.handle_key_down()
        assert state.get("selected_project_idx", -1) == 0

    def test_arrow_down_increments_index(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "projects",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "projects_data": [
                {"project_id": "p1", "name": "a"},
                {"project_id": "p2", "name": "b"},
            ],
        }
        handler = TUIKeyHandler(state)
        assert state.get("selected_project_idx", 0) == 0
        handler.handle_key_down()
        assert state["selected_project_idx"] == 1
        handler.handle_key_down()
        assert state["selected_project_idx"] == 0


class TestSpaceBarActivation:
    def test_space_bar_activates_project(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "projects",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "projects_data": [
                {"project_id": "p1", "name": "alpha"},
                {"project_id": "p2", "name": "beta"},
            ],
            "selected_project_idx": 1,
        }
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_project_id") == "p2"
        assert "p2" in state.get("status_msg", "")

    def test_space_bar_activates_todo(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "todos",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "todos_data": [
                {"todo_id": "t1", "title": "fix bug"},
            ],
            "selected_todo_idx": 0,
        }
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_todo_id") == "t1"
        assert "t1" in state.get("status_msg", "")

    def test_space_bar_activates_hook(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "hooks",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "hooks_data": [
                {"hook_id": "h1", "event_name": "push"},
            ],
            "selected_hook_idx": 0,
        }
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_hook_id") == "h1"

    def test_space_bar_activates_worker(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "workers",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "workers_data": [
                {"worker_id": "w1", "address": "10.0.0.1"},
            ],
            "selected_worker_idx": 0,
        }
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state.get("active_worker_id") == "w1"


class TestSelectionIndexInStatus:
    def test_arrow_down_updates_status_with_selection_info(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = {
            "current_view": "projects",
            "daemon_url": "http://localhost:8000",
            "daemon_running": True,
            "status_msg": "",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "projects_data": [
                {"project_id": "p1", "name": "alpha"},
                {"project_id": "p2", "name": "beta"},
            ],
        }
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        idx = state.get("selected_project_idx", -1)
        assert idx == 1
        assert "beta" in state.get("status_msg", "") or "p2" in state.get("status_msg", "")
