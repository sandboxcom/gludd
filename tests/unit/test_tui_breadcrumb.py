"""TDD: Breadcrumb navigation system for TUI.

The breadcrumb system tracks navigation history so:
1. Every view transition is recorded
2. Escape navigates back through the breadcrumb trail
3. The current breadcrumb is displayed in the header
4. Full e2e test coverage of navigation paths

This tests the COMPLETE navigation flow: key press -> state change -> rendering.
"""

from __future__ import annotations

from typing import Any


def _make_state(view: str = "main", **kwargs: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
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
        "breadcrumb": ["main"],
    }
    state.update(kwargs)
    return state


class TestBreadcrumbSystem:
    def test_initial_breadcrumb_is_main(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        TUIKeyHandler(state)
        assert state["breadcrumb"] == ["main"]

    def test_view_change_pushes_breadcrumb(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("p")
        assert state["current_view"] == "projects"
        assert state["breadcrumb"] == ["main", "projects"]

    def test_nested_view_pushes_deep(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("p")
        handler.handle_key("u")
        assert state["breadcrumb"] == ["main", "projects", "mcp"]

    def test_escape_pops_breadcrumb(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("p")
        assert state["breadcrumb"] == ["main", "projects"]
        handler.handle_key("\x1b")
        assert state["current_view"] == "main"
        assert state["breadcrumb"] == ["main"]

    def test_double_escape_stays_at_main(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("p")
        handler.handle_key("\x1b")
        handler.handle_key("\x1b")
        assert state["current_view"] == "main"
        assert state["breadcrumb"] == ["main"]

    def test_toggle_same_view_pops_breadcrumb(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("p")
        assert state["breadcrumb"] == ["main", "projects"]
        handler.handle_key("p")
        assert state["current_view"] == "main"
        assert state["breadcrumb"] == ["main"]

    def test_breadcrumb_renders_in_header(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        bc = ["main", "projects"]
        result = render_breadcrumb(bc)
        assert "main" in result
        assert "projects" in result
        assert ">" in result or "/" in result or "→" in result


class TestAllViewsHaveDataPopulation:
    """Every view with arrow key navigation must populate its data key in tui_state."""

    def test_agents_view_populates_agents_data(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("agents", agents_data=[
            {"agent_id": "a1", "name": "coder"},
        ])
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state.get("selected_agent_idx") is not None

    def test_metrics_view_has_no_arrow_navigation(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("metrics")
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b[B")
        assert result is True

    def test_integrity_view_populates_changes(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("integrity", integrity_changes=[
            {"path": "/etc/config.yml"},
        ])
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state.get("selected_integrity_idx") is not None


class TestNavigationEndToEnd:
    """Full flow: key press → state change → breadcrumb update → status msg."""

    def test_enter_projects_then_arrow_down_then_escape(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state(projects_data=[
            {"project_id": "p1", "name": "alpha"},
            {"project_id": "p2", "name": "beta"},
        ])
        handler = TUIKeyHandler(state)

        handler.handle_key("p")
        assert state["current_view"] == "projects"
        assert state["breadcrumb"] == ["main", "projects"]

        handler.handle_key("\x1b[B")
        assert state["selected_project_idx"] == 1
        assert "beta" in state.get("status_msg", "")

        handler.handle_key(" ")
        assert state.get("active_project_id") == "p2"

        handler.handle_key("\x1b")
        assert state["current_view"] == "main"
        assert state["breadcrumb"] == ["main"]

    def test_enter_todos_arrow_up_wraps(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = _make_state("todos", todos_data=[
            {"todo_id": "t1", "title": "first"},
            {"todo_id": "t2", "title": "second"},
        ], selected_todo_idx=0)
        handler = TUIKeyHandler(state)

        handler.handle_key("\x1b[A")
        assert state["selected_todo_idx"] == 1

    def test_all_toggle_views_push_breadcrumb(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        toggle_keys = {
            "u": "mcp", "j": "skills", "e": "compute",
            "b": "scores", "l": "templates", "n": "quantization",
            "f": "filestore", "z": "deployments",
        }
        for key, expected_view in toggle_keys.items():
            state = _make_state()
            handler = TUIKeyHandler(state)
            handler.handle_key(key)
            assert state["current_view"] == expected_view, f"Key {key} should go to {expected_view}"
            assert state["breadcrumb"] == ["main", expected_view], f"Key {key} breadcrumb wrong"

    def test_all_toggle_views_pop_on_same_key(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        toggle_keys = {
            "u": "mcp", "j": "skills", "e": "compute",
            "b": "scores", "l": "templates", "n": "quantization",
            "f": "filestore", "z": "deployments",
        }
        for key, expected_view in toggle_keys.items():
            state = _make_state()
            handler = TUIKeyHandler(state)
            handler.handle_key(key)
            assert state["current_view"] == expected_view
            handler.handle_key(key)
            assert state["current_view"] == "main", f"Key {key} again should go back to main"
            assert state["breadcrumb"] == ["main"]

    def test_space_activates_in_every_list_view(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        test_cases = [
            ("projects", "projects_data", "selected_project_idx", "active_project_id",
             [{"project_id": "p1", "name": "a"}]),
            ("todos", "todos_data", "selected_todo_idx", "active_todo_id",
             [{"todo_id": "t1", "title": "x"}]),
            ("hooks", "hooks_data", "selected_hook_idx", "active_hook_id",
             [{"hook_id": "h1", "event_name": "push"}]),
            ("workers", "workers_data", "selected_worker_idx", "active_worker_id",
             [{"worker_id": "w1", "address": "10.0.0.1"}]),
        ]
        for view, data_key, idx_key, active_key, data in test_cases:
            state = _make_state(view, **{data_key: data, idx_key: 0})
            handler = TUIKeyHandler(state)
            handler.handle_key(" ")
            assert state.get(active_key) is not None, (
                f"Space in {view} should set {active_key}"
            )
            assert state.get(active_key) != ""
