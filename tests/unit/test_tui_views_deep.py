"""Deep edge-case tests for TUI modules: tables, config_editor, breadcrumb, logger, keybindings.

Covers: _make_table boundary conditions, ConfigEditor coercion + overlay paths,
breadcrumb nil/empty/single states, TUILogger no-dir/empty-flush/verbose paths,
TUIKeyHandler uncovered dispatch modes, subview interactions, and input-mode edges.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.tui.keybindings import (
    TUIKeyHandler,
    build_gunicorn_cmd,
    validate_gunicorn_spawn_args,
)

ESC = "\033"
DOWN = ESC + "[B"
UP = ESC + "[A"
LEFT = ESC + "[D"
BS = "\177"
TAB = "\t"
CR = "\r"
SP = " "


def _state(**overrides: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "current_view": "main",
        "daemon_url": "http://127.0.0.1:8000",
        "status_msg": "",
        "daemon_running": False,
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "panel_focus": "left",
        "projects_data": [],
        "hooks_data": [],
        "models_data": [],
        "todos_data": [],
        "workers_data": [],
        "agents_data": [],
        "integrity_changes": [],
        "selected_main_idx": 0,
        "selected_project_idx": 0,
        "selected_hook_idx": 0,
        "selected_model_idx": 0,
        "selected_todo_idx": 0,
        "selected_worker_idx": 0,
        "selected_agent_idx": 0,
        "selected_integrity_idx": 0,
        "dispatch_mode": "active",
        "active_project_id": "",
        "active_todo_id": "",
        "active_hook_id": "",
        "active_worker_id": "",
        "active_model_id": "",
        "health_data": {},
        "selftest_data": {},
        "current_log_level": "info",
        "verbose_logging": False,
        "last_reload": False,
        "last_discover": False,
        "last_scan": False,
        "last_report": False,
        "last_bootstrap": False,
        "last_loglevel": False,
        "discovered_data": [],
        "ansible_builtins": [],
        "filestore_binaries": [],
        "code_graph_data": {},
        "code_search_results": [],
        "breadcrumb": ["main"],
    }
    s.update(overrides)
    return s


def _handler(**overrides: Any) -> tuple[TUIKeyHandler, dict[str, Any]]:
    st = _state(**overrides)
    return TUIKeyHandler(st), st


# ══════════════════════════════════════════════════════════════════════════════
# _make_table deep edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestMakeTableDeep:
    def test_rows_and_data_both_set_raises(self):
        from general_ludd.tui.tables import _make_table

        with pytest.raises(ValueError, match="not both"):
            _make_table(
                title="t",
                columns=[("a", "", 1, 5)],
                rows=[("x",)],
                data=[1],
                row_formatter=lambda *a: ("x",),
            )

    def test_empty_data_with_empty_msg_shows_message(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="Empty",
            columns=[("Col", "", 1, 10)],
            data=[],
            empty_msg="No data",
            row_formatter=lambda item, idx, sel: (str(item),),
        )
        cells = [r for r in t.columns[0].cells if r]
        assert any("No data" in str(c) for c in cells)

    def test_empty_data_no_empty_msg_shows_no_row(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="Empty",
            columns=[("Col", "", 1, 10)],
            data=[],
            row_formatter=lambda item, idx, sel: (str(item),),
        )
        rows = t.rows
        assert len(rows) == 0

    def test_empty_rows_with_empty_msg_shows_message(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="Empty",
            columns=[("A", "", 1, 10), ("B", "", 1, 10)],
            rows=[],
            empty_msg="Nothing here",
        )
        cells = [r for r in t.columns[0].cells if r]
        assert any("Nothing here" in str(c) for c in cells)

    def test_empty_rows_no_empty_msg_returns_none_rows(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="Empty",
            columns=[("A", "", 1, 10)],
            rows=[],
        )
        assert len(t.rows) == 0

    def test_selected_idx_on_empty_rows_does_not_crash(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("C", "", 1, 10)],
            rows=[],
            selected_idx=0,
            empty_msg="gone",
        )
        cells = [r for r in t.columns[0].cells if r]
        assert any("gone" in str(c) for c in cells)

    def test_selected_idx_out_of_bounds_no_highlight(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=[("hello",)],
            selected_idx=5,
        )
        rows = t.rows
        assert len(rows) == 1
        rendered = str(rows[0])
        assert "\u25b6" not in rendered

    def test_selected_idx_negative_no_highlight(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=[("hello",)],
            selected_idx=-1,
        )
        rendered = str(t.rows[0])
        assert "\u25b6" not in rendered

    def test_selected_idx_zero_highlights(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=[("hello",)],
            selected_idx=0,
        )
        row_style = str(t.rows[0].style or "")
        assert "reverse" in row_style.lower()
        cell_text = str(t.columns[0]._cells[0] if t.columns[0]._cells else "")
        assert "\u25b6" in cell_text

    def test_data_row_formatter_receives_correct_args(self):
        from general_ludd.tui.tables import _make_table

        captured: list[tuple[Any, int, int | None]] = []

        def fmt(item: Any, idx: int, sel: int | None) -> tuple[str, ...]:
            captured.append((item, idx, sel))
            return (str(item),)

        _make_table(
            title="T",
            columns=[("C", "", 1, 10)],
            data=[10, 20, 30],
            selected_idx=1,
            row_formatter=fmt,
        )
        assert len(captured) == 3
        assert captured[0] == (10, 0, 1)
        assert captured[1] == (20, 1, 1)
        assert captured[2] == (30, 2, 1)

    def test_single_column_table_works(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="Solo",
            columns=[("X", "", 1, 80)],
            rows=[("a",), ("b",), ("c",)],
        )
        assert len(t.columns) == 1
        assert len(t.rows) == 3

    def test_multi_column_selected_arrow_only_on_first(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10), ("B", "", 2, 10), ("C", "", 1, 10)],
            rows=[("x", "y", "z")],
            selected_idx=0,
        )
        c0 = str(t.columns[0]._cells[0] if t.columns[0]._cells else "")
        c1 = str(t.columns[1]._cells[0] if len(t.columns) > 1 and t.columns[1]._cells else "")
        c2 = str(t.columns[2]._cells[0] if len(t.columns) > 2 and t.columns[2]._cells else "")
        assert "\u25b6 x" in c0
        assert "\u25b6" not in c1
        assert "\u25b6" not in c2

    def test_none_rows_uses_empty_list(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=None,
        )
        assert len(t.rows) == 0

    def test_show_header_false(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=[("x",)],
            show_header=False,
        )
        assert t.show_header is False

    def test_zero_term_width(self):
        from general_ludd.tui.tables import _make_table

        t = _make_table(
            title="T",
            columns=[("A", "", 1, 10)],
            rows=[("x",)],
            term_width=0,
        )
        assert t.width == 0

    def test_row_formatter_single_element_list_data(self):
        from general_ludd.tui.tables import _make_table

        def fmt(item: Any, idx: int, sel: int | None) -> tuple[str, ...]:
            return (f"item-{item}",)

        t = _make_table(
            title="T",
            columns=[("C", "", 1, 10)],
            data=[42],
            row_formatter=fmt,
        )
        cells = [r for r in t.columns[0].cells if r]
        assert any("item-42" in str(c) for c in cells)


# ══════════════════════════════════════════════════════════════════════════════
# ConfigEditor deep edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigEditorDeep:
    def test_start_editing_menu_item_is_noop(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        parent = MenuItem(
            label="Parent",
            key="p",
            item_type="menu",
            submenu=[
                MenuItem(label="Child", key="c", value=42, item_type="int"),
            ],
        )
        editor.start_editing(parent)
        assert editor.editing is False
        assert editor.editing_item is None

    def test_start_editing_leaf_item_starts_editing(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8080, item_type="int")
        editor.start_editing(item, "/tmp/test.yml")
        assert editor.editing is True
        assert editor.editing_item is item
        assert editor.input_buffer == "8080"
        assert editor._active_overlay_path == "/tmp/test.yml"

    def test_start_editing_none_value_produces_empty_buffer(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value=None, item_type="str")
        editor.start_editing(item)
        assert editor.input_buffer == ""

    def test_start_editing_falls_back_to_item_overlay_path(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(
            label="Key",
            key="k",
            value="v",
            item_type="str",
            overlay_path="/tmp/fallback.yml",
        )
        editor.start_editing(item)
        assert editor._active_overlay_path == "/tmp/fallback.yml"

    def test_handle_input_backspace_on_empty_buffer_noop(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="", item_type="str")
        editor.start_editing(item)
        result = editor.handle_input_key("\x7f")
        assert result is None
        assert editor.input_buffer == ""

    def test_handle_input_enter_when_not_editing_returns_none(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        editor.editing = False
        result = editor.handle_input_key("\r")
        assert result is None

    def test_handle_input_escape_when_not_editing_returns_none(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        editor.editing = False
        result = editor.handle_input_key("\x1b")
        assert result is None

    def test_handle_input_escape_when_editing_cancels(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="old", item_type="str")
        editor.start_editing(item)
        result = editor.handle_input_key("\x1b")
        assert result == "cancelled"
        assert editor.editing is False
        assert editor.editing_item is None
        assert editor.input_buffer == ""

    def test_handle_input_enter_triggers_save_and_returns_saved(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="old", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "new_value"
        result = editor.handle_input_key("\r")
        assert result == "saved"
        assert editor.editing is False
        assert item.value == "new_value"

    def test_bool_coercion_true_variants(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        for val in ("true", "1", "yes"):
            item = MenuItem(label="B", key="b", value=False, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = val
            editor._save_edit()
            assert item.value is True, f"'{val}' should coerce to True"

    def test_bool_coercion_false_variants(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        for val in ("", "0", "no", "False", "off"):
            item = MenuItem(label="B", key="b", value=True, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = val
            editor._save_edit()
            assert item.value is False, f"'{val}' should coerce to False"

    def test_int_coercion(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="P", key="p", value=0, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "42"
        editor._save_edit()
        assert item.value == 42
        assert isinstance(item.value, int)

    def test_int_coercion_negative(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="P", key="p", value=0, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "-7"
        editor._save_edit()
        assert item.value == -7

    def test_int_coercion_invalid_raises(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="P", key="p", value=0, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "not_a_number"
        with pytest.raises(ValueError):
            editor._save_edit()

    def test_float_coercion(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="F", key="f", value=0.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "3.14"
        editor._save_edit()
        assert item.value == 3.14
        assert isinstance(item.value, float)

    def test_float_coercion_integer_string(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="F", key="f", value=0.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "10"
        editor._save_edit()
        assert item.value == 10.0
        assert isinstance(item.value, float)

    def test_save_with_editing_item_none_noop(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        editor.editing_item = None
        editor._save_edit()

    def test_save_writes_overlay_file(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "fs" / "test.yml"
            editor = ConfigEditor(config_dir=tmp)
            item = MenuItem(label="Engine", key="engine", value="sqlite", item_type="str")
            editor.start_editing(item, str(overlay))
            editor.input_buffer = "postgresql"
            editor._save_edit()
            assert overlay.exists()
            content = overlay.read_text()
            assert "postgresql" in content

    def test_save_no_overlay_path_does_not_write(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="old", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "new"
        editor._active_overlay_path = ""
        editor._save_edit()
        assert item.value == "new"

    def test_get_input_display_not_editing(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        assert editor.get_input_display() == ""

    def test_get_input_display_editing(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "hello"
        display = editor.get_input_display()
        assert "hello" in display
        assert display.endswith("_")

    def test_handle_input_regular_char_while_editing_appends(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("a")
        editor.handle_input_key("b")
        assert editor.input_buffer == "ab"

    def test_handle_input_regular_char_not_editing_noop(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        result = editor.handle_input_key("x")
        assert result is None
        assert editor.input_buffer == ""

    def test_overlay_path_defaults_for_all_categories(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        cats = editor.get_categories()
        for cat in cats:
            assert cat.overlay_path.endswith(".yml"), f"{cat.name} overlay missing"
            assert os.path.basename(cat.overlay_path)

    def test_read_yaml_nonexistent_file_returns_empty_dict(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        result = editor.read_yaml("/tmp/does_not_exist_xyZ987654.yml")
        assert result == {}

    def test_read_yaml_scalar_not_dict(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            cf = Path(tmp) / "scalar.yml"
            cf.write_text("42")
            editor = ConfigEditor()
            result = editor.read_yaml(str(cf))
            assert result == {}

    def test_menu_item_is_menu_empty_submenu(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="X", key="x", value=1, item_type="int")
        assert item.is_menu is False

    def test_menu_item_is_menu_with_submenu(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(
            label="X",
            key="x",
            item_type="menu",
            submenu=[
                MenuItem(label="Y", key="y", value=1, item_type="int"),
            ],
        )
        assert item.is_menu is True

    def test_config_category_default_overlay_empty(self):
        from general_ludd.tui.config_editor import ConfigCategory

        cat = ConfigCategory(name="Test", menu_items=[])
        assert cat.overlay_path == ""

    def test_write_overlay_creates_parent_dirs(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            deep_path = Path(tmp) / "a" / "b" / "c" / "config.yml"
            editor = ConfigEditor()
            editor.write_overlay(str(deep_path), {"key": "value"})
            assert deep_path.exists()
            content = deep_path.read_text()
            assert "value" in content


# ══════════════════════════════════════════════════════════════════════════════
# Breadcrumb deep edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestBreadcrumbDeep:
    def test_push_can_start_from_empty_state(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_push_initializes_to_main_when_none(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {"breadcrumb": None}
        push_breadcrumb(state, "edit")
        assert state["breadcrumb"] == ["main", "edit"]

    def test_push_initializes_when_empty_list(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {"breadcrumb": []}
        push_breadcrumb(state, "config")
        assert state["breadcrumb"] == ["main", "config"]

    def test_push_duplicate_last_does_not_append(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {"breadcrumb": ["main", "projects"]}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_push_different_view_appends(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {"breadcrumb": ["main"]}
        push_breadcrumb(state, "mcp")
        assert state["breadcrumb"] == ["main", "mcp"]

    def test_pop_returns_last_after_popping(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict[str, Any] = {"breadcrumb": ["main", "projects", "mcp"]}
        result = pop_breadcrumb(state)
        assert result == "projects"
        assert state["breadcrumb"] == ["main", "projects"]

    def test_pop_single_element_does_not_pop(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict[str, Any] = {"breadcrumb": ["main"]}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_none_initializes(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict[str, Any] = {"breadcrumb": None}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_empty_initializes(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict[str, Any] = {"breadcrumb": []}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_five_deep_back_to_main(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict[str, Any] = {"breadcrumb": ["main", "a", "b", "c", "d"]}
        for expected in ["c", "b", "a", "main"]:
            result = pop_breadcrumb(state)
            assert result == expected
        result = pop_breadcrumb(state)
        assert result == "main"

    def test_render_empty_list(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb([])
        assert result == ""

    def test_render_single_element(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["main"])
        assert result == "main"

    def test_render_special_characters(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["main", "log-level", "code"])
        assert result == "main > log-level > code"

    def test_push_many_deep(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict[str, Any] = {}
        views = ["a", "b", "c", "d", "e", "f"]
        for v in views:
            push_breadcrumb(state, v)
        assert state["breadcrumb"] == ["main", *views]
        assert len(state["breadcrumb"]) == len(views) + 1


# ══════════════════════════════════════════════════════════════════════════════
# TUILogger deep edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestTUILoggerDeep:
    def test_no_log_dir_does_not_create_log_file(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory():
            logger = TUILogger(log_dir="", daemon_url="http://127.0.0.1:8000")
            logger.log_key_press("main", "a")
            logger.log_view_change("main", "projects")
            assert len(logger._entries) == 2
            assert logger._log_path == ""

    def test_verbose_false_blocks_key_log(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=False)
            logger.log_key_press("main", "q")
            assert len(logger._entries) == 0

    def test_verbose_true_logs_key_press(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=True)
            logger.log_key_press("config", "v")
            assert len(logger._entries) == 1
            assert logger._entries[0]["event"] == "key_press"
            assert logger._entries[0]["key"] == "v"

    def test_view_change_always_logs(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=False)
            logger.log_view_change("main", "todos")
            assert len(logger._entries) == 1
            assert logger._entries[0]["event"] == "view_change"

    def test_daemon_action_logs(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_daemon_action("start", {"pid": 1234})
            assert logger._entries[0]["action"] == "start"
            assert logger._entries[0]["details"] == {"pid": 1234}

    def test_daemon_action_none_details(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_daemon_action("stop")
            assert logger._entries[0]["details"] == {}

    def test_selection_logs(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_selection("projects", 2, "p42")
            assert logger._entries[0]["event"] == "selection_change"
            assert logger._entries[0]["index"] == 2
            assert logger._entries[0]["item_id"] == "p42"

    def test_status_msg_logs(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_status_msg("Daemon started")
            assert logger._entries[0]["event"] == "status_msg"
            assert logger._entries[0]["message"] == "Daemon started"

    def test_toggle_verbose(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=False)
            assert logger.verbose is False
            logger.toggle_verbose()
            assert logger.verbose is True
            logger.toggle_verbose()
            assert logger.verbose is False

    def test_flush_no_daemon_url_noop(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="")
            logger.log_view_change("a", "b")
            logger.flush_to_database()

    def test_flush_no_entries_noop(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://127.0.0.1:8000")
            logger.flush_to_database()

    def test_flush_sends_last_50_entries(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://127.0.0.1:8000")
            for i in range(100):
                logger.log_status_msg(f"msg-{i}")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                logger.flush_to_database()
                mock_post.assert_called_once()
                sent_entries = mock_post.call_args.kwargs["json"]["entries"]
                assert len(sent_entries) <= 50

    def test_flush_on_close(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://127.0.0.1:8000")
            logger.log_status_msg("closing")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                logger.close()
                mock_post.assert_called_once()

    def test_close_no_entries_no_flush(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://127.0.0.1:8000")
            mock_resp = MagicMock()
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                logger.close()
                mock_post.assert_not_called()

    def test_entries_have_session_id_and_timestamp(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_key_press("main", "a")
            e = logger._entries[0]
            assert "session_id" in e
            assert len(e["session_id"]) == 12
            assert "timestamp" in e
            assert isinstance(e["timestamp"], float)

    def test_writes_to_log_file(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=True)
            logger.log_key_press("main", "q")
            log_path = Path(tmp) / "tui.log"
            assert log_path.exists()
            content = log_path.read_text().strip()
            assert "key_press" in content

    def test_flush_exception_suppressed(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://127.0.0.1:8000")
            logger.log_status_msg("test")
            with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
                logger.flush_to_database()


# ══════════════════════════════════════════════════════════════════════════════
# TUIKeyHandler uncovered edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestKeyHandlerViewTransitions:
    def test_toggle_key_in_toggle_view_closes(self):
        for key, view_name in [
            ("y", "leaderboard"),
            ("P", "playbooks"),
            ("L", "slurm"),
            ("H", "health"),
            ("T", "selftest"),
            ("0", "version"),
            ("1", "log-level"),
            ("D", "discovered"),
            ("C", "code"),
        ]:
            h, s = _handler(current_view=view_name)
            h.handle_key(key)
            assert s["current_view"] == "main", f"Key {key!r} in {view_name} should go to main"

    def test_letter_no_effect_in_irrelevant_view(self):
        h, s = _handler(current_view="filestore")
        s["status_msg"] = "before"
        h.handle_key("m")
        assert s["status_msg"] != "before"

    def test_enter_when_input_mode_is_skill_install(self):
        h, _s = _handler(current_view="skills", input_mode="skills_install", input_buffer="my-skill")
        with patch.object(h, "_handle_skills_install_input", return_value=True) as mock:
            h.handle_key(CR)
            mock.assert_called_once_with(CR)

    def test_enter_when_input_mode_is_ansible_install(self):
        h, _s = _handler(current_view="ansible", input_mode="ansible_install", input_buffer="my-role")
        with patch.object(h, "_handle_ansible_install_input", return_value=True) as mock:
            h.handle_key(CR)
            mock.assert_called_once_with(CR)

    def test_enter_when_input_mode_is_todos_add(self):
        h, _s = _handler(current_view="todos", input_mode="todos_add", input_buffer="fix")
        with patch.object(h, "_handle_todos_add_input", return_value=True) as mock:
            h.handle_key(CR)
            mock.assert_called_once_with(CR)

    def test_enter_when_input_mode_is_hooks_register(self):
        h, _s = _handler(current_view="hooks", input_mode="hooks_register", input_buffer="push")
        with patch.object(h, "_handle_hooks_register_input", return_value=True) as mock:
            h.handle_key(CR)
            mock.assert_called_once_with(CR)

    def test_enter_when_input_mode_is_code_graph(self):
        h, _s = _handler(current_view="code", input_mode="code_graph", input_buffer="daemon.py")
        with patch.object(h, "_handle_code_graph_input", return_value=True) as mock:
            h.handle_key(CR)
            mock.assert_called_once_with(CR)

    def test_dispatch_error_status_msg(self):
        h, s = _handler(current_view="main", dispatch_mode="active")
        with patch("httpx.put", side_effect=Exception("boom")):
            h._cycle_dispatch_mode()
        assert "error" in s["status_msg"].lower()


class TestKeyHandlerDelegateActions:
    def test_health_refresh_http_error(self):
        h, s = _handler(current_view="health")
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            h._health_refresh()
        assert "fail" in s["status_msg"].lower()

    def test_health_refresh_connection_error(self):
        h, s = _handler(current_view="health")
        with patch("httpx.get", side_effect=Exception("refused")):
            h._health_refresh()
        assert "error" in s["status_msg"].lower()

    def test_selftest_run_http_error(self):
        h, s = _handler(current_view="selftest")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._selftest_run()
        assert "fail" in s["status_msg"].lower()

    def test_loglevel_cycle_connection_error(self):
        h, s = _handler(current_view="log-level", current_log_level="info")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._loglevel_cycle()
        assert "error" in s["status_msg"].lower()

    def test_loglevel_cycles_through_all(self):
        h, s = _handler(current_view="log-level", current_log_level="error")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp):
            h._loglevel_cycle()
        assert s["current_log_level"] == "debug"

    def test_loglevel_unknown_resets_to_first(self):
        h, s = _handler(current_view="log-level", current_log_level="unknown")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp):
            h._loglevel_cycle()
        assert s["current_log_level"] == "debug"

    def test_models_discover_connection_error(self):
        h, s = _handler(current_view="models")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._models_discover()
        assert "error" in s["status_msg"].lower()
        assert s["last_discover"] is True

    def test_worktree_scan_connection_error(self):
        h, s = _handler(current_view="worktrees")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._worktree_scan()
        assert "error" in s["status_msg"].lower()
        assert s["last_scan"] is True

    def test_integrity_report_connection_error(self):
        h, s = _handler(current_view="integrity")
        with patch("httpx.get", side_effect=Exception("refused")):
            h._integrity_report()
        assert "error" in s["status_msg"].lower()
        assert s["last_report"] is True

    def test_ansible_builtins_http_error(self):
        h, s = _handler(current_view="ansible")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.get", return_value=mock_resp):
            h._ansible_builtins()
        assert "fail" in s["status_msg"].lower()

    def test_filestore_binaries_connection_error(self):
        h, s = _handler(current_view="filestore")
        with patch("httpx.get", side_effect=Exception("refused")):
            h._filestore_binaries()
        assert "error" in s["status_msg"].lower()

    def test_filestore_bootstrap_connection_error(self):
        h, s = _handler(current_view="filestore")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._filestore_bootstrap()
        assert "error" in s["status_msg"].lower()
        assert s["last_bootstrap"] is True

    def test_discovered_refresh_http_error(self):
        h, s = _handler(current_view="discovered")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            h._discovered_refresh()
        assert "fail" in s["status_msg"].lower()

    def test_compute_register_submit_http_error(self):
        h, s = _handler(
            current_view="compute",
            input_mode="compute_register",
            input_buffer="https://x.com",
            input_field_index=1,
            input_fields=[
                {"label": "endpoint_url", "value": "https://x.com"},
                {"label": "provider", "value": ""},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._handle_compute_register_input(CR)
        assert "fail" in s["status_msg"].lower()

    def test_todos_add_submit_http_error(self):
        h, s = _handler(
            current_view="todos",
            input_mode="todos_add",
            input_buffer="5",
            input_field_index=1,
            input_fields=[
                {"label": "title", "value": "my todo"},
                {"label": "priority", "value": ""},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._handle_todos_add_input(CR)
        assert "fail" in s["status_msg"].lower()

    def test_hooks_register_submit_http_error(self):
        h, s = _handler(
            current_view="hooks",
            input_mode="hooks_register",
            input_buffer="http://hook.example.com",
            input_field_index=1,
            input_fields=[
                {"label": "event_name", "value": "push"},
                {"label": "url", "value": ""},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._handle_hooks_register_input(CR)
        assert "fail" in s["status_msg"].lower()

    def test_models_add_submit_http_error(self):
        h, s = _handler(
            current_view="models",
            input_mode="models_add",
            input_buffer="https://api.com",
            input_field_index=2,
            input_fields=[
                {"label": "model_id", "value": "gpt-4"},
                {"label": "provider", "value": "openai"},
                {"label": "api_base", "value": ""},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._handle_models_add_input(CR)
        assert "fail" in s["status_msg"].lower()

    def test_ping_workers_http_error(self):
        h, s = _handler(current_view="workers")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._ping_workers()
        assert "fail" in s["status_msg"].lower()

    def test_ping_workers_connection_error(self):
        h, s = _handler(current_view="workers")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._ping_workers()
        assert "error" in s["status_msg"].lower()

    def test_reload_daemon_http_error(self):
        h, s = _handler(current_view="main")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            h._reload_daemon()
        assert "fail" in s["status_msg"].lower()
        assert s["last_reload"] is False

    def test_reload_daemon_connection_error(self):
        h, s = _handler(current_view="main")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._reload_daemon()
        assert "error" in s["status_msg"].lower()
        assert s["last_reload"] is False

    def test_reload_daemon_success(self):
        h, s = _handler(current_view="main")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"scope": "all"}
        with patch("httpx.post", return_value=mock_resp):
            h._reload_daemon()
        assert "Reloaded" in s["status_msg"]
        assert s["last_reload"] is True

    def test_start_daemon_already_running(self):
        h, s = _handler(current_view="main")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.get", return_value=mock_resp):
            h._start_daemon()
        assert "already running" in s["status_msg"].lower()
        assert s["daemon_running"] is True

    def test_stop_daemon_http_success(self):
        h, s = _handler(current_view="main", daemon_running=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp):
            h._stop_daemon()
        assert "stopped" in s["status_msg"].lower()
        assert s["daemon_running"] is False

    def test_stop_daemon_http_fails(self):
        h, s = _handler(current_view="main", daemon_running=True)
        with patch("httpx.post", side_effect=Exception("refused")):
            h._stop_daemon()
        assert s["daemon_running"] is False

    def test_delete_selected_hook_empty(self):
        h, s = _handler(current_view="hooks", hooks_data=[])
        h._delete_selected_hook()
        assert "No hooks" in s["status_msg"]

    def test_delete_selected_hook_connection_error(self):
        h, s = _handler(
            current_view="hooks",
            hooks_data=[{"hook_id": "hk1"}],
            selected_hook_idx=0,
        )
        with patch("httpx.delete", side_effect=Exception("refused")):
            h._delete_selected_hook()
        assert "error" in s["status_msg"].lower()

    def test_remove_selected_model_empty(self):
        h, s = _handler(current_view="models", models_data=[])
        h._remove_selected_model()
        assert "No models" in s["status_msg"]

    def test_remove_selected_model_connection_error(self):
        h, s = _handler(
            current_view="models",
            models_data=[{"model_id": "m1"}],
            selected_model_idx=0,
        )
        with patch("httpx.delete", side_effect=Exception("refused")):
            h._remove_selected_model()
        assert "error" in s["status_msg"].lower()

    def test_integrity_scan_connection_error(self):
        h, s = _handler(current_view="integrity")
        with patch("httpx.post", side_effect=Exception("refused")):
            h._integrity_scan()
        assert "error" in s["status_msg"].lower()

    def test_integrity_approve_empty_changes(self):
        h, s = _handler(current_view="integrity", integrity_changes=[])
        h._integrity_approve()
        assert "No changes" in s["status_msg"]

    def test_integrity_reject_empty_changes(self):
        h, s = _handler(current_view="integrity", integrity_changes=[])
        h._integrity_reject()
        assert "No changes" in s["status_msg"]

    def test_integrity_approve_connection_error(self):
        h, s = _handler(
            current_view="integrity",
            integrity_changes=[{"path": "/tmp/x"}],
            selected_integrity_idx=0,
        )
        with patch("httpx.post", side_effect=Exception("refused")):
            h._integrity_approve()
        assert "error" in s["status_msg"].lower()

    def test_integrity_reject_connection_error(self):
        h, s = _handler(
            current_view="integrity",
            integrity_changes=[{"path": "/tmp/x"}],
            selected_integrity_idx=0,
        )
        with patch("httpx.post", side_effect=Exception("refused")):
            h._integrity_reject()
        assert "error" in s["status_msg"].lower()

    def test_handle_ansible_search_connection_error(self):
        h, s = _handler(
            current_view="ansible",
            input_mode="ansible_search",
            input_buffer="nginx",
        )
        with patch("httpx.get", side_effect=Exception("refused")):
            h._handle_ansible_search_input(CR)
        assert "error" in s["status_msg"].lower()
        assert s["input_mode"] is None

    def test_handle_skills_install_connection_error(self):
        h, s = _handler(
            current_view="skills",
            input_mode="skills_install",
            input_buffer="my-skill",
        )
        with patch("httpx.post", side_effect=Exception("refused")):
            h._handle_skills_install_input(CR)
        assert "error" in s["status_msg"].lower()

    def test_handle_ansible_install_connection_error(self):
        h, s = _handler(
            current_view="ansible",
            input_mode="ansible_install",
            input_buffer="my-role",
        )
        with patch("httpx.post", side_effect=Exception("refused")):
            h._handle_ansible_install_input(CR)
        assert "error" in s["status_msg"].lower()

    def test_handle_code_graph_connection_error(self):
        h, s = _handler(
            current_view="code",
            input_mode="code_graph",
            input_buffer="main.py",
        )
        with patch("httpx.get", side_effect=Exception("refused")):
            h._handle_code_graph_input(CR)
        assert "error" in s["status_msg"].lower()

    def test_handle_code_graph_success(self):
        h, s = _handler(
            current_view="code",
            input_mode="code_graph",
            input_buffer="daemon.py",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"nodes": [{"name": "main"}]}
        with patch("httpx.get", return_value=mock_resp):
            h._handle_code_graph_input(CR)
        assert "1 nodes" in s["status_msg"]

    def test_todos_priority_default_on_non_int(self):
        h, _s = _handler(
            current_view="todos",
            input_mode="todos_add",
            input_buffer="urgent",
            input_field_index=1,
            input_fields=[
                {"label": "title", "value": "my todo"},
                {"label": "priority", "value": ""},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"todo_id": "t1"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            h._handle_todos_add_input(CR)
        payload = mock_post.call_args.kwargs["json"]
        assert payload["priority"] == 5

    def test_get_main_menu_items_returns_copy(self):
        h, _s = _handler()
        items = h.get_main_menu_items()
        items.append(("Z", "test", "test_target"))
        assert len(h.MAIN_MENU_ITEMS) == len(TUIKeyHandler.MAIN_MENU_ITEMS)

    def test_activate_selected_models_empty(self):
        h, s = _handler(current_view="models", models_data=[], selected_model_idx=0)
        s["status_msg"] = "before"
        h._activate_selected("models")
        assert s["status_msg"] == "before"

    def test_start_daemon_invalid_spawn_args(self):
        h, s = _handler(current_view="main", daemon_host="127.0.0.1", daemon_port=70000)
        with patch("httpx.get", side_effect=Exception("not running")):
            h._start_daemon()
        assert "invalid spawn args" in s["status_msg"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# spawn hardening uncovered edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestSpawnHardeningDeep:
    def test_null_byte_host_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host="a\x00b", port=8000, workers=1)

    def test_empty_string_host_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host="", port=8000, workers=1)

    def test_newline_in_host_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host="valid\nhost", port=8000, workers=1)

    def test_string_port_rejected(self):
        with pytest.raises(ValueError, match="port"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port="8000", workers=1)

    def test_port_zero_rejected(self):
        with pytest.raises(ValueError, match="port"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=0, workers=1)

    def test_port_too_high_rejected(self):
        with pytest.raises(ValueError, match="port"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=65536, workers=1)

    def test_workers_zero_rejected(self):
        with pytest.raises(ValueError, match="workers"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=0)

    def test_bad_log_level_rejected(self):
        with pytest.raises(ValueError, match="log-level"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=1, log_level="trace")

    def test_non_string_log_level_rejected(self):
        with pytest.raises(ValueError, match="log-level"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=1, log_level=42)

    def test_empty_path_in_list_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        with pytest.raises(ValueError, match="path"):
            validate_gunicorn_spawn_args(
                host="127.0.0.1",
                port=8000,
                workers=1,
                paths=[""],
                confine_root=str(confine),
            )

    def test_path_escaping_confinement_rejected(self, tmp_path):
        confine = tmp_path / "root"
        confine.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("")
        with pytest.raises(ValueError, match="escapes"):
            validate_gunicorn_spawn_args(
                host="127.0.0.1",
                port=8000,
                workers=1,
                paths=[str(outside)],
                confine_root=str(confine),
            )

    def test_hostname_leading_dot_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host=".example.com", port=8000, workers=1)

    def test_hostname_trailing_dot_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host="example.com.", port=8000, workers=1)

    def test_hostname_leading_hyphen_rejected(self):
        with pytest.raises(ValueError, match="host"):
            validate_gunicorn_spawn_args(host="-example.com", port=8000, workers=1)

    def test_build_cmd_no_log_level(self):
        cmd = build_gunicorn_cmd(host="0.0.0.0", port=8000, workers=4)
        assert "--log-level" not in cmd
        assert "0.0.0.0:8000" in cmd

    def test_build_cmd_validates_before_construction(self):
        with pytest.raises(ValueError, match="port"):
            build_gunicorn_cmd(host="127.0.0.1", port=-1, workers=1)
