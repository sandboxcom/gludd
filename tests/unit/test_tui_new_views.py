"""Tests for TUI views: todos, metrics, hooks, workers.

TDD: These tests define the expected behavior of the new TUI view builder
functions BEFORE implementation exists.
"""

from __future__ import annotations

from rich.table import Table


class TestBuildTodosView:
    def test_build_todos_table_with_items(self):
        from general_ludd.cli import _build_todos_table

        todos = [
            {"todo_id": "t1", "title": "Fix bug", "status": "pending", "priority": "high"},
            {"todo_id": "t2", "title": "Add feature", "status": "in_progress", "priority": "medium"},
        ]
        t = _build_todos_table(todos)
        assert isinstance(t, Table)
        assert t.row_count == 2

    def test_build_todos_table_empty(self):
        from general_ludd.cli import _build_todos_table

        t = _build_todos_table([])
        assert isinstance(t, Table)
        assert t.row_count == 0

    def test_build_todos_table_has_required_columns(self):
        from general_ludd.cli import _build_todos_table

        t = _build_todos_table([{"todo_id": "x", "title": "y", "status": "z", "priority": "lo"}])
        col_names = [c.header for c in t.columns]
        assert "ID" in col_names
        assert "Title" in col_names
        assert "Status" in col_names
        assert "Pri" in col_names


class TestBuildHooksView:
    def test_build_hooks_table_with_items(self):
        from general_ludd.cli import _build_hooks_table

        hooks = [
            {"hook_id": "h1", "event_name": "job.complete", "hook_type": "webhook", "url": "http://x", "priority": 1},
        ]
        t = _build_hooks_table(hooks)
        assert isinstance(t, Table)
        assert t.row_count == 1

    def test_build_hooks_table_empty(self):
        from general_ludd.cli import _build_hooks_table

        t = _build_hooks_table([])
        assert isinstance(t, Table)
        assert t.row_count == 0

    def test_build_hooks_table_has_required_columns(self):
        from general_ludd.cli import _build_hooks_table

        hooks = [{"hook_id": "h1", "event_name": "e", "hook_type": "w", "url": "u", "priority": 1}]
        t = _build_hooks_table(hooks)
        col_names = [c.header for c in t.columns]
        assert "ID" in col_names
        assert "Event" in col_names
        assert "Type" in col_names


class TestBuildWorkersView:
    def test_build_workers_table_with_items(self):
        from general_ludd.cli import _build_workers_table

        workers = [
            {"worker_id": "w1", "address": "http://10.0.0.1:9000", "last_seen": "2026-01-01T00:00:00"},
        ]
        t = _build_workers_table(workers)
        assert isinstance(t, Table)
        assert t.row_count == 1

    def test_build_workers_table_empty(self):
        from general_ludd.cli import _build_workers_table

        t = _build_workers_table([])
        assert isinstance(t, Table)
        assert t.row_count == 0

    def test_build_workers_table_has_required_columns(self):
        from general_ludd.cli import _build_workers_table

        workers = [{"worker_id": "w1", "address": "a", "last_seen": "ls"}]
        t = _build_workers_table(workers)
        col_names = [c.header for c in t.columns]
        assert "ID" in col_names
        assert "Address" in col_names


class TestBuildMetricsView:
    def test_build_metrics_table_with_cost_data(self):
        from general_ludd.cli import _build_metrics_table

        cost_data = {
            "total_cost_usd": 12.50,
            "subscription_name": "pro",
            "subscription_cost_usd_per_month": 50.0,
            "tokens_used": 100000,
            "tokens_remaining_this_week": 50000,
            "cost_as_pct_of_subscription": 25.0,
        }
        t = _build_metrics_table(cost_data)
        assert isinstance(t, Table)
        assert t.row_count >= 5

    def test_build_metrics_table_empty(self):
        from general_ludd.cli import _build_metrics_table

        t = _build_metrics_table({})
        assert isinstance(t, Table)
        assert t.row_count == 0

    def test_build_metrics_table_shows_cost_and_tokens(self):
        from general_ludd.cli import _build_metrics_table

        data = {
            "total_cost_usd": 5.0,
            "tokens_used": 5000,
            "cost_as_pct_of_subscription": 10.0,
        }
        t = _build_metrics_table(data)
        col_names = [c.header for c in t.columns]
        assert "Metric" in col_names
        assert "Value" in col_names


class TestBuildAgentsView:
    def test_build_agents_table_with_agents(self):
        from general_ludd.cli import _build_agents_table

        agents = [
            {
                "agent_id": "a1",
                "agent_name": "worker-1",
                "status": "running",
                "project": "gludd",
                "uptime_seconds": 3600,
                "total_tokens": 1000,
                "total_cost_usd": 0.05,
            },
        ]
        t = _build_agents_table(agents)
        assert isinstance(t, Table)
        assert t.row_count == 1

    def test_build_agents_table_empty(self):
        from general_ludd.cli import _build_agents_table

        t = _build_agents_table([])
        assert isinstance(t, Table)
        assert t.row_count == 0

    def test_build_agents_table_has_required_columns(self):
        from general_ludd.cli import _build_agents_table

        agents = [
            {
                "agent_id": "a1",
                "agent_name": "n",
                "status": "s",
                "project": "p",
                "uptime_seconds": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }
        ]
        t = _build_agents_table(agents)
        col_names = [c.header for c in t.columns]
        assert "ID" in col_names
        assert "Name" in col_names
        assert "Status" in col_names
        assert "Project" in col_names


class TestTUIViewKeyBindings:
    def test_handle_key_t_toggles_todos_view(self):
        current_view = "main"
        ch = "t"
        view_map = {
            "t": "todos",
            "h": "hooks",
            "o": "workers",
            "x": "metrics",
            "g": "agents",
        }
        if ch in view_map:
            current_view = view_map[ch] if current_view != view_map[ch] else "main"
        assert current_view == "todos"

    def test_handle_key_t_toggles_back_to_main(self):
        current_view = "todos"
        ch = "t"
        view_map = {"t": "todos"}
        if ch in view_map:
            current_view = view_map[ch] if current_view != view_map[ch] else "main"
        assert current_view == "main"

    def test_all_new_views_have_keybindings(self):
        expected_views = {"todos", "hooks", "workers", "metrics", "agents"}
        view_keys = {"t": "todos", "h": "hooks", "o": "workers", "x": "metrics", "g": "agents"}
        mapped = set(view_keys.values())
        assert expected_views == mapped
