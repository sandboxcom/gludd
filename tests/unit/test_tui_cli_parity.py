"""Tests for CLI-to-TUI parity: new views for missing CLI commands."""

from __future__ import annotations

from typing import Any

import pytest


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
        assert "Health" in t.title

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
        assert "Selftest" in t.title

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
        assert "Version" in t.title
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
        assert "Log Level" in t.title

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
        assert "Discovered" in t.title

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
        assert "Code" in t.title

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
        handler.handle_key("d")
        assert "discover" in state.get("status_msg", "").lower() or state.get("last_discover") is not None


class TestWorktreesScanAction:
    def test_worktrees_scan_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "worktrees"
        handler.handle_key("s")
        assert "scan" in state.get("status_msg", "").lower() or state.get("last_scan") is not None


class TestIntegrityReportAction:
    def test_integrity_report_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "integrity"
        handler.handle_key("p")
        assert "report" in state.get("status_msg", "").lower() or state.get("last_report") is not None


class TestAnsibleBuiltinsAction:
    def test_ansible_builtins_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "ansible"
        handler.handle_key("b")
        assert "builtin" in state.get("status_msg", "").lower() or state.get("ansible_builtins") is not None


class TestFilestoreBinariesAction:
    def test_filestore_binaries_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "filestore"
        handler.handle_key("b")
        assert "binar" in state.get("status_msg", "").lower() or state.get("filestore_binaries") is not None


class TestFilestoreBootstrapAction:
    def test_filestore_bootstrap_action(self) -> None:
        handler, state = _make_handler()
        state["current_view"] = "filestore"
        handler.handle_key("B")
        assert "bootstrap" in state.get("status_msg", "").lower() or state.get("last_bootstrap") is not None


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
        import inspect

        from general_ludd.tui.runner import run_tui

        src = inspect.getsource(run_tui)
        for view_name in ("health", "selftest", "version", "log-level", "discovered", "code"):
            assert f'current_view == "{view_name}"' in src, f"Missing rendering for view '{view_name}'"

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
