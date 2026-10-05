"""Tests for CLI-to-TUI parity: new views for missing CLI commands."""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _mock_httpx_response(status_code: int = 200, json_data: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock httpx response with the given status code and JSON body."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data if json_data is not None else {}
    return mock_resp


def _make_handler() -> tuple[Any, dict[str, Any]]:
    from general_ludd.tui.keybindings import TUIKeyHandler

    state: dict[str, Any] = {
        "current_view": "main",
        "daemon_running": False,
        "status_msg": "",
        "daemon_url": "http://localhost:8000",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "dispatch_mode": "active",
        "ansible_search_results": [],
    }
    return TUIKeyHandler(state), state


class TestHealthView:
    def test_health_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("H")
        assert state["current_view"] == "health"

    def test_health_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "health"
        handler.handle_key("H")
        assert state["current_view"] == "main"

    def test_build_health_table_empty(self) -> None:
        from general_ludd.cli import _build_health_table

        t = _build_health_table({})
        assert t is not None
        assert "Health" in str(t.title)

    def test_build_health_table_with_data(self) -> None:
        from general_ludd.cli import _build_health_table

        data = {"status": "ok", "version": "1.0.0", "uptime_s": 3600}
        t = _build_health_table(data)
        assert t is not None
        assert t.row_count >= 1


class TestSelftestView:
    def test_selftest_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("T")
        assert state["current_view"] == "selftest"

    def test_selftest_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "selftest"
        handler.handle_key("T")
        assert state["current_view"] == "main"

    def test_build_selftest_table_empty(self) -> None:
        from general_ludd.cli import _build_selftest_table

        t = _build_selftest_table({})
        assert t is not None
        assert "Selftest" in str(t.title)

    def test_build_selftest_table_with_results(self) -> None:
        from general_ludd.cli import _build_selftest_table

        data = {
            "scenarios_run": 3,
            "scenarios_passed": 2,
            "results": [
                {"scenario": "test_a", "passed": True},
                {"scenario": "test_b", "passed": False},
            ],
        }
        t = _build_selftest_table(data)
        assert t is not None


class TestVersionView:
    def test_version_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("0")
        assert state["current_view"] == "version"

    def test_version_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "version"
        handler.handle_key("0")
        assert state["current_view"] == "main"

    def test_build_version_table(self) -> None:
        from general_ludd.cli import _build_version_table

        info = {
            "version": "1.2.3",
            "python_version": "3.11.0",
            "platform": "macOS-14.0",
        }
        t = _build_version_table(info)
        assert t is not None
        assert "Version" in str(t.title)
        assert t.row_count >= 1


class TestLogLevelView:
    def test_loglevel_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("1")
        assert state["current_view"] == "log-level"

    def test_loglevel_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "log-level"
        handler.handle_key("1")
        assert state["current_view"] == "main"

    def test_build_loglevel_table(self) -> None:
        from general_ludd.cli import _build_loglevel_table

        t = _build_loglevel_table("info")
        assert t is not None
        assert "Log Level" in str(t.title)

    def test_loglevel_cycle_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "log-level"
        handler.handle_key("c")
        assert "log" in state.get("status_msg", "").lower() or state.get("last_loglevel") is not None


class TestDiscoveredModelsView:
    def test_discovered_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("D")
        assert state["current_view"] == "discovered"

    def test_discovered_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "discovered"
        handler.handle_key("D")
        assert state["current_view"] == "main"

    def test_build_discovered_table_empty(self) -> None:
        from general_ludd.cli import _build_discovered_table

        t = _build_discovered_table([])
        assert t is not None
        assert "Discovered" in str(t.title)

    def test_build_discovered_table_with_profiles(self) -> None:
        from general_ludd.cli import _build_discovered_table

        profiles = [
            {
                "model_profile_id": "openrouter/free-1",
                "display_name": "Free Model 1",
                "enabled": True,
            },
            {
                "model_profile_id": "openrouter/free-2",
                "display_name": "Free Model 2",
                "enabled": False,
            },
        ]
        t = _build_discovered_table(profiles)
        assert t.row_count == 2


class TestCodeIntelView:
    def test_code_toggle_key(self) -> None:
        handler, state = _make_handler()
        handler.handle_key("C")
        assert state["current_view"] == "code"

    def test_code_exit(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "code"
        handler.handle_key("C")
        assert state["current_view"] == "main"

    def test_build_code_table_empty(self) -> None:
        from general_ludd.cli import _build_code_table

        t = _build_code_table([])
        assert t is not None
        assert "Code" in str(t.title)

    def test_build_code_table_with_results(self) -> None:
        from general_ludd.cli import _build_code_table

        results = [
            {"file": "src/main.py", "line": 42, "text": "def hello():"},
        ]
        t = _build_code_table(results)
        assert t.row_count == 1


class TestModelsDiscoverAction:
    def test_models_discover_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "models"
        mock_resp = _mock_httpx_response(200, {"discovered_count": 5})
        with patch("httpx.post", return_value=mock_resp):
            handler.handle_key("d")
        # Handler sets status_msg with "Discovered" text and sets last_discover
        assert "discover" in state.get("status_msg", "").lower() or state.get("last_discover") is not None
        assert state.get("last_discover") is True


class TestWorktreesScanAction:
    def test_worktrees_scan_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "worktrees"
        mock_resp = _mock_httpx_response(200, {"tracked_count": 3, "todos": []})
        with patch("httpx.post", return_value=mock_resp):
            handler.handle_key("s")
        # Handler sets status_msg with "Scan" text and sets last_scan
        assert "scan" in state.get("status_msg", "").lower() or state.get("last_scan") is not None
        assert state.get("last_scan") is True


class TestIntegrityReportAction:
    def test_integrity_report_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "integrity"
        mock_resp = _mock_httpx_response(200, {"report": "ok"})
        with patch("httpx.get", return_value=mock_resp):
            handler.handle_key("p")
        # Handler sets status_msg with "report" text and sets last_report
        assert "report" in state.get("status_msg", "").lower() or state.get("last_report") is not None
        assert state.get("last_report") is True


class TestAnsibleBuiltinsAction:
    def test_ansible_builtins_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "ansible"
        mock_resp = _mock_httpx_response(200, {"modules": ["command", "copy", "file"]})
        with patch("httpx.get", return_value=mock_resp):
            handler.handle_key("b")
        # Handler sets ansible_builtins with modules list and status_msg with "Builtins"
        assert "builtin" in state.get("status_msg", "").lower() or state.get("ansible_builtins") is not None
        assert state.get("ansible_builtins") == ["command", "copy", "file"]


class TestFilestoreBinariesAction:
    def test_filestore_binaries_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "filestore"
        mock_resp = _mock_httpx_response(200, {"binaries": ["openbao", "consul"], "count": 2})
        with patch("httpx.get", return_value=mock_resp):
            handler.handle_key("b")
        # Handler sets filestore_binaries with list and status_msg with "Binaries"
        assert "binar" in state.get("status_msg", "").lower() or state.get("filestore_binaries") is not None
        assert state.get("filestore_binaries") == ["openbao", "consul"]


class TestFilestoreBootstrapAction:
    def test_filestore_bootstrap_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "filestore"
        mock_resp = _mock_httpx_response(200, {"binary": "openbao", "status": "ok"})
        with patch("httpx.post", return_value=mock_resp):
            handler.handle_key("B")
        # Handler sets status_msg with "Bootstrapped" text and sets last_bootstrap
        assert "bootstrap" in state.get("status_msg", "").lower() or state.get("last_bootstrap") is not None
        assert state.get("last_bootstrap") is True


class TestAllNewTablesNoUnboundedColumns:
    @pytest.mark.parametrize("name,builder_key", [
        ("health", "_build_health_table"),
        ("selftest", "_build_selftest_table"),
        ("version", "_build_version_table"),
        ("log-level", "_build_loglevel_table"),
        ("discovered", "_build_discovered_table"),
        ("code", "_build_code_table"),
    ])
    def test_no_unbounded_columns(self, name: str, builder_key: str) -> None:
        import importlib

        mod = importlib.import_module("general_ludd.cli")
        fn = getattr(mod, builder_key)
        if name == "log-level":
            t = fn("info")
        elif name == "version":
            t = fn({"version": "1.0", "python_version": "3.11", "platform": "linux"})
        else:
            t = fn([])
        for col in t.columns:
            assert col.max_width is not None, f"{name}: column {col.header!r} has no max_width"


class TestToggleViewsCompleteness:
    def test_all_new_views_in_toggle_dict(self) -> None:
        from general_ludd.tui.keybindings import _TOGGLE_VIEWS

        expected_views = {"health", "selftest", "version", "log-level", "discovered", "code"}
        view_names = {v[0] for v in _TOGGLE_VIEWS.values()}
        for v in expected_views:
            assert v in view_names, f"View '{v}' missing from _TOGGLE_VIEWS"

    def test_all_new_views_have_rendering(self) -> None:
        from general_ludd.tui.runner import run_tui

        expected = {
            "health": "_build_health_table",
            "selftest": "_build_selftest_table",
            "version": "_build_version_table",
            "log-level": "_build_loglevel_table",
            "discovered": "_build_discovered_table",
            "code": "_build_code_table",
        }
        tree = ast.parse(textwrap.dedent(inspect.getsource(run_tui)))
        renderers: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Match)
                and isinstance(node.subject, ast.Name)
                and node.subject.id == "current_view"
            ):
                continue
            for case in node.cases:
                pattern = case.pattern
                if not (
                    isinstance(pattern, ast.MatchValue)
                    and isinstance(pattern.value, ast.Constant)
                    and isinstance(pattern.value.value, str)
                ):
                    continue
                body = ast.Module(body=case.body, type_ignores=[])
                renderers.setdefault(pattern.value.value, set()).update(
                    child.func.attr
                    for child in ast.walk(body)
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                )

        for view_name, builder_name in expected.items():
            assert builder_name in renderers.get(view_name, set()), (
                f"Missing {builder_name} rendering for view '{view_name}'"
            )

    def test_new_views_have_table_builders(self) -> None:
        from general_ludd import cli

        builders = [
            "_build_health_table",
            "_build_selftest_table",
            "_build_version_table",
            "_build_loglevel_table",
            "_build_discovered_table",
            "_build_code_table",
        ]
        for name in builders:
            assert hasattr(cli, name), f"Missing builder: {name}"
