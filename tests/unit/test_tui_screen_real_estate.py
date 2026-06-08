import io
import re

import pytest
from rich.console import Console
from rich.table import Table


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _render_width(table: Table, width: int) -> str:
    buf = io.StringIO()
    c = Console(file=buf, width=width, force_terminal=True, legacy_windows=False)
    c.print(table)
    return buf.getvalue()


def _max_line_width(output: str) -> int:
    stripped = _strip_ansi(output)
    return max(len(line) for line in stripped.splitlines()) if stripped.strip() else 0


def _rendered_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()]


class TestExtractedTablesStayWithinTerminal:
    def test_controls_table_narrow_terminal(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(True, "ok")
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Controls table overflows 60-col terminal: {_max_line_width(out)}"

    def test_controls_table_wide_terminal(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(True, "ok")
        out = _render_width(t, 200)
        assert _max_line_width(out) <= 200

    def test_daemon_table_narrow_terminal(self):
        from general_ludd.cli import _build_daemon_table

        t = _build_daemon_table(True, "http://localhost:8080", "main")
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Daemon table overflows 60-col terminal: {_max_line_width(out)}"

    def test_info_table_narrow_terminal(self):
        from general_ludd.cli import _build_info_table

        info = {
            "version": "0.1.0",
            "python_version": "3.14.0",
            "platform": "macOS",
            "cwd": "/very/long/path/that/should/not/overflow/the/terminal/window/ever",
            "config_dir": "/also/a/very/long/path/that/should/not/overflow/the/terminal",
            "config_files": [{"name": "general-ludd.yml", "size_bytes": 4096}],
            "filestore_root": "/path/to/filestore/root/directory/very/long",
            "filestore_size_bytes": 1024 * 1024,
            "db_engine": "sqlite",
            "db_exists": True,
            "db_size_bytes": 2048,
        }
        t = _build_info_table(info)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Info table overflows 60-col terminal: {_max_line_width(out)}"

    def test_todos_table_narrow_terminal(self):
        from general_ludd.cli import _build_todos_table

        todos = [
            {
                "todo_id": "abc123def456",
                "title": "A very long todo title that would normally overflow the terminal width",
                "status": "pending",
                "priority": "high",
            }
        ]
        t = _build_todos_table(todos)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Todos table overflows 60-col terminal: {_max_line_width(out)}"

    def test_hooks_table_narrow_terminal(self):
        from general_ludd.cli import _build_hooks_table

        hooks = [{"hook_id": "hook-123456789012", "event_type": "config_changed", "hook_type": "webhook"}]
        t = _build_hooks_table(hooks)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Hooks table overflows 60-col terminal: {_max_line_width(out)}"

    def test_workers_table_narrow_terminal(self):
        from general_ludd.cli import _build_workers_table

        workers = [
            {"worker_id": "worker-1234567890", "address": "http://very-long-worker-hostname.example.com:8080"}
        ]
        t = _build_workers_table(workers)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Workers table overflows 60-col terminal: {_max_line_width(out)}"

    def test_metrics_table_narrow_terminal(self):
        from general_ludd.cli import _build_metrics_table

        metrics = {"total_cost_usd": "1.234", "total_tokens": 100000, "models_used": 5}
        t = _build_metrics_table(metrics)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Metrics table overflows 60-col terminal: {_max_line_width(out)}"

    def test_agents_table_narrow_terminal(self):
        from general_ludd.cli import _build_agents_table

        agents = [
            {
                "agent_id": "agent-12345",
                "name": "code-builder-agent",
                "status": "active",
                "project": "my-project-name",
                "uptime_seconds": 3600,
            }
        ]
        t = _build_agents_table(agents)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Agents table overflows 60-col terminal: {_max_line_width(out)}"

    def test_model_table_narrow_terminal(self):
        from general_ludd.cli import _build_model_table

        servers = [
            {
                "id": "model-with-very-long-id",
                "engine": "llamacpp",
                "model": "very-long-model-name-here",
                "status": "running",
            }
        ]
        t = _build_model_table(servers, [])
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Model table overflows 60-col terminal: {_max_line_width(out)}"

    def test_config_table_narrow_terminal(self):
        from general_ludd.cli import _build_config_table

        info = {"config_files": [{"name": "a-very-long-config-file-name.yml", "size_bytes": 4096}]}
        t = _build_config_table(info)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Config table overflows 60-col terminal: {_max_line_width(out)}"

    def test_binary_table_narrow_terminal(self):
        from general_ludd.cli import _build_binary_table

        info = {
            "binary_paths": {
                "ansible-playbook": "/usr/local/bin/ansible-playbook",
                "terraform": "/opt/homebrew/bin/terraform",
            }
        }
        t = _build_binary_table(info)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Binary table overflows 60-col terminal: {_max_line_width(out)}"


class TestAdaptiveColumnWidths:
    def test_table_builders_accept_term_width(self):
        from general_ludd.cli import _build_controls_table, _build_daemon_table, _build_info_table

        t = _build_controls_table(True, "ok", term_width=40)
        out = _render_width(t, 40)
        assert _max_line_width(out) <= 40, f"Controls table ignores term_width=40: {_max_line_width(out)}"

        t = _build_daemon_table(True, "http://localhost:8080", "main", term_width=40)
        out = _render_width(t, 40)
        assert _max_line_width(out) <= 40, f"Daemon table ignores term_width=40: {_max_line_width(out)}"

        info = {"version": "0.1.0", "cwd": "/short", "config_files": [], "filestore_root": "/short"}
        t = _build_info_table(info, term_width=40)
        out = _render_width(t, 40)
        assert _max_line_width(out) <= 40, f"Info table ignores term_width=40: {_max_line_width(out)}"

    def test_wide_terminal_uses_more_space(self):
        from general_ludd.cli import _build_info_table

        info = {
            "version": "0.1.0",
            "cwd": "/some/path",
            "config_files": [],
            "filestore_root": "/some/path",
        }
        t_narrow = _build_info_table(info, term_width=60)
        t_wide = _build_info_table(info, term_width=160)

        out_narrow = _render_width(t_narrow, 60)
        out_wide = _render_width(t_wide, 160)

        assert _max_line_width(out_narrow) <= 60
        assert _max_line_width(out_wide) <= 160
        assert _max_line_width(out_wide) > _max_line_width(out_narrow)


class TestConfigEditorTableBounds:
    def test_config_editor_columns_bounded(self):
        from general_ludd.cli import _build_config_editor_table

        items = [
            {"label": "short", "value": "x" * 200, "help_text": "h" * 200},
        ]
        t = _build_config_editor_table(items, selected=0, depth=1, term_width=60)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Config editor overflows 60-col: {_max_line_width(out)}"

    def test_config_editor_category_list_bounded(self):
        from general_ludd.cli import _build_config_editor_table

        items = [
            {"label": "Credentials", "value": "", "help_text": ""},
            {"label": "Model Profiles", "value": "", "help_text": ""},
        ]
        t = _build_config_editor_table(items, selected=0, depth=0, term_width=60)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Config editor categories overflows 60-col: {_max_line_width(out)}"


class TestInlineTableBounds:
    def test_worktrees_table_bounded(self):
        from general_ludd.cli import _build_worktrees_table

        t = _build_worktrees_table(
            [("my-project-directory-name-very-long", "has AGENTS.md")], term_width=60
        )
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Worktrees table overflows 60-col: {_max_line_width(out)}"

    def test_projects_table_bounded(self):
        from general_ludd.cli import _build_projects_table

        projects = [
            {"project_id": "proj-123456789", "name": "my-long-project-name", "weight": 50, "dispatch_mode": "active"},
        ]
        t = _build_projects_table(projects, term_width=60)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Projects table overflows 60-col: {_max_line_width(out)}"

    def test_integrity_table_bounded(self):
        from general_ludd.cli import _build_integrity_table

        changes = [
            {"file": "/very/long/path/to/config/file/that/should/not/overflow", "type": "modified", "approved": False},
        ]
        t = _build_integrity_table(changes, term_width=60)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Integrity table overflows 60-col: {_max_line_width(out)}"

    def test_ansible_table_bounded(self):
        from general_ludd.cli import _build_ansible_table

        results = [
            {"name": "very-long-ansible-role-name-here", "description": "A very long description that could overflow"},
        ]
        t = _build_ansible_table(results, term_width=60)
        out = _render_width(t, 60)
        assert _max_line_width(out) <= 60, f"Ansible table overflows 60-col: {_max_line_width(out)}"


class TestFooterAdaptiveHeight:
    def test_footer_shrinks_on_short_terminal(self):
        from general_ludd.cli import _compute_footer_rows

        result = _compute_footer_rows(24)
        assert result <= 8, f"Footer should be <=8 rows on 24-row terminal, got {result}"
        assert _compute_footer_rows(40) <= 18, "Footer should be <=18 rows on 40-row terminal"
        assert _compute_footer_rows(50) == 18, "Footer should be 18 rows on 50-row terminal"


class TestAllTablesNoUnboundedColumns:
    def test_no_table_has_unbounded_columns(self):
        import inspect

        from general_ludd import cli

        builders = [
            name for name, obj in inspect.getmembers(cli)
            if inspect.isfunction(obj) and name.startswith("_build_") and name.endswith("_table")
        ]
        assert len(builders) >= 10, f"Expected >=10 table builders, found {len(builders)}: {builders}"

        for name in builders:
            fn = getattr(cli, name)
            sig = inspect.signature(fn)
            has_term_width = "term_width" in sig.parameters
            assert has_term_width, f"{name} missing term_width parameter — columns may be hardcoded"


class TestRenderAtMultipleWidths:
    @pytest.mark.parametrize("width", [40, 60, 80, 120, 160, 200])
    def test_controls_table_at_all_widths(self, width):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(True, "ok", term_width=width)
        out = _render_width(t, width)
        assert _max_line_width(out) <= width, f"Controls overflows at width={width}: {_max_line_width(out)}"

    @pytest.mark.parametrize("width", [40, 60, 80, 120, 160, 200])
    def test_info_table_at_all_widths(self, width):
        from general_ludd.cli import _build_info_table

        info = {
            "version": "0.1.0",
            "python_version": "3.14.0",
            "platform": "macOS-26.4.1-arm64",
            "cwd": "/Users/shawnwilson/gludd",
            "config_dir": "/Users/shawnwilson/.config/general-ludd",
            "config_files": [{"name": "general-ludd.yml", "size_bytes": 4096}],
            "filestore_root": "/Users/shawnwilson/.local/share/general-ludd/filestore",
            "filestore_size_bytes": 1024 * 1024,
            "db_engine": "sqlite",
            "db_exists": True,
            "db_size_bytes": 2048,
        }
        t = _build_info_table(info, term_width=width)
        out = _render_width(t, width)
        assert _max_line_width(out) <= width, f"Info overflows at width={width}: {_max_line_width(out)}"

    @pytest.mark.parametrize("width", [40, 60, 80, 120, 160, 200])
    def test_todos_table_at_all_widths(self, width):
        from general_ludd.cli import _build_todos_table

        todos = [
            {"todo_id": "abc123", "title": "Implement feature X", "status": "pending", "priority": "high"},
            {"todo_id": "def456", "title": "Fix bug Y in subsystem Z", "status": "in_progress", "priority": "medium"},
        ]
        t = _build_todos_table(todos, term_width=width)
        out = _render_width(t, width)
        assert _max_line_width(out) <= width, f"Todos overflows at width={width}: {_max_line_width(out)}"
