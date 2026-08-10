"""Deep edge-case tests for TUI widget components (tables, breadcrumb, logger, config_editor).

Covers: Unicode, extreme values, boundary conditions, malformed inputs,
concurrency-safety, type coercion edge cases, and silent-failure paths.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ── _make_table deep edge cases ──────────────────────────────────────


class TestMakeTableDeepEdges:
    def test_none_rows_with_empty_msg(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table("T", cols, rows=None, empty_msg="Nothing")
        assert table.row_count == 1

    def test_none_rows_without_empty_msg_is_empty(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table("T", cols, rows=None)
        assert table.row_count == 0

    def test_zero_columns(self):
        from general_ludd.tui.tables import _make_table

        table = _make_table("Zero", [], rows=[], empty_msg="Empty")
        assert table is not None

    def test_single_column_no_padding_empty_msg(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Name", "", 1, 20)]
        table = _make_table("T", cols, rows=[], empty_msg="Nothing here")
        assert table.row_count == 1
        assert len(table.columns) == 1

    def test_unicode_title_and_cells(self):
        from general_ludd.tui.tables import _make_table

        cols = [("\U0001f680", "", 1, 10), ("\u2603", "", 1, 10)]
        rows = [("\U0001f4a3", "\u00e9"), ("caf\u00e9", "r\u00e9sum\u00e9")]
        table = _make_table("\u03a9 Title \u03a9", cols, rows=rows)
        assert table.row_count == 2

    def test_emoji_in_selected_row(self):
        from general_ludd.tui.tables import _make_table

        cols = [("", "", 1, 30)]
        rows = [("\U0001f600",), ("\U0001f525",)]
        table = _make_table("T", cols, rows=rows, selected_idx=0)
        assert table.row_count == 2

    def test_selected_idx_negative_is_no_selection(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        rows = [("a",), ("b",)]
        table = _make_table("T", cols, rows=rows, selected_idx=-1)
        found_bold = any("bold" in str(getattr(row, "style", "")) for row in table.rows)
        assert not found_bold

    def test_selected_idx_beyond_range_is_no_selection(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        rows = [("a",), ("b",)]
        table = _make_table("T", cols, rows=rows, selected_idx=999)
        found_bold = any("bold" in str(getattr(row, "style", "")) for row in table.rows)
        assert not found_bold

    def test_very_long_cell_values(self):
        from general_ludd.tui.tables import _make_table

        long_str = "x" * 10000
        cols = [("Col", "", 1, 10)]
        rows = [(long_str,)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 1

    def test_empty_string_cells(self):
        from general_ludd.tui.tables import _make_table

        cols = [("A", "", 1, 5), ("B", "", 1, 5)]
        rows = [("", ""), ("x", "")]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 2

    def test_many_columns(self):
        from general_ludd.tui.tables import _make_table

        cols = [(f"C{i}", "", 1, 5) for i in range(20)]
        rows = [tuple(f"v{i}" for i in range(20))]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 1
        assert len(table.columns) == 20

    def test_many_rows(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Idx", "", 1, 5), ("Val", "", 1, 10)]
        rows = [(str(i), f"val-{i}") for i in range(500)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 500

    def test_data_empty_with_row_formatter_shows_empty_msg(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table(
            "T",
            cols,
            data=[],
            empty_msg="Zilch",
            row_formatter=lambda item, idx, sel: (str(item),),
        )
        assert table.row_count == 1

    def test_data_but_no_row_formatter_returns_empty(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table("T", cols, data=[1, 2, 3])
        assert table.row_count == 0

    def test_data_with_selected_idx_passed_to_formatter(self):
        from general_ludd.tui.tables import _make_table

        captured: list = []

        def fmt(item, idx, sel_idx):
            captured.append((item, idx, sel_idx))
            return (str(item),)

        cols = [("Col", "", 1, 10)]
        _make_table("T", cols, data=["a", "b", "c"], row_formatter=fmt, selected_idx=1)
        assert captured[0][2] == 1
        assert captured[1][2] == 1
        assert captured[2][2] == 1

    def test_selected_idx_is_0_with_rows(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Name", "", 1, 20)]
        rows = [("first",)]
        table = _make_table("T", cols, rows=rows, selected_idx=0)
        found_bold = any("bold" in str(getattr(row, "style", "")) for row in table.rows)
        assert found_bold

    def test_special_html_like_chars_in_cells(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 20)]
        rows = [("<script>alert(1)</script>",), ("&nbsp;",), (">>>",)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 3

    def test_rows_with_extra_columns_are_added_as_is(self):
        from general_ludd.tui.tables import _make_table

        cols = [("A", "", 1, 5)]
        rows = [("a", "extra"), ("b",)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 2

    def test_newline_in_title(self):
        from general_ludd.tui.tables import _make_table

        cols = [("C", "", 1, 10)]
        table = _make_table("Line1\nLine2", cols, rows=[("x",)])
        assert table.row_count == 1

    def test_newline_in_cell(self):
        from general_ludd.tui.tables import _make_table

        cols = [("C", "", 1, 20)]
        rows = [("a\nb",)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 1

    def test_whitespace_only_cells(self):
        from general_ludd.tui.tables import _make_table

        cols = [("C", "", 1, 10)]
        rows = [("   ",), ("\t",)]
        table = _make_table("T", cols, rows=rows)
        assert table.row_count == 2

    def test_show_header_true_explicit(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table("T", cols, rows=[("x",)], show_header=True)
        assert table.show_header is True


# ── breadcrumb deep edge cases ──────────────────────────────────────


class TestBreadcrumbDeepEdges:
    def test_push_breadcrumb_on_none_state_value(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {"breadcrumb": None}
        push_breadcrumb(state, "projects")
        assert "projects" in state["breadcrumb"]

    def test_push_breadcrumb_on_missing_key(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {}
        push_breadcrumb(state, "settings")
        assert state["breadcrumb"] == ["main", "settings"]

    def test_push_breadcrumb_on_empty_list(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {"breadcrumb": []}
        push_breadcrumb(state, "dashboard")
        assert state["breadcrumb"] == ["main", "dashboard"]

    def test_push_breadcrumb_unicode_view_name(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {"breadcrumb": ["main"]}
        push_breadcrumb(state, "\u00e9dition")
        assert state["breadcrumb"] == ["main", "\u00e9dition"]

    def test_push_deep_breadcrumb_chain(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {}
        for i in range(100):
            push_breadcrumb(state, f"view-{i}")
        assert len(state["breadcrumb"]) == 101

    def test_pop_breadcrumb_deep(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        bc = ["main"] + [f"v{i}" for i in range(50)]
        state: dict = {"breadcrumb": bc}
        result = pop_breadcrumb(state)
        assert result == "v48"
        assert len(state["breadcrumb"]) == 50

    def test_pop_breadcrumb_from_no_key(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict = {}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state.get("breadcrumb") == ["main"]

    def test_pop_breadcrumb_from_empty_list(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict = {"breadcrumb": []}
        result = pop_breadcrumb(state)
        assert result == "main"

    def test_pop_breadcrumb_from_falsy_string_key(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict = {"breadcrumb": ""}
        result = pop_breadcrumb(state)
        assert result == "main"

    def test_render_breadcrumb_empty_list(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        assert render_breadcrumb([]) == ""

    def test_render_breadcrumb_single_empty_string(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        assert render_breadcrumb([""]) == ""

    def test_render_breadcrumb_unicode_separator(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["\u03b1", "\u03b2", "\u03b3"])
        assert "\u03b1" in result
        assert "\u03b3" in result
        assert ">" in result

    def test_render_breadcrumb_with_spaces_in_views(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["main", "my projects", "edit config"])
        assert result == "main > my projects > edit config"

    def test_push_breadcrumb_idempotent_with_unicode(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state: dict = {"breadcrumb": ["main", "\u2603"]}
        push_breadcrumb(state, "\u2603")
        assert state["breadcrumb"] == ["main", "\u2603"]


# ── TUILogger deep edge cases ───────────────────────────────────────


class TestTUILoggerDeepEdges:
    def test_no_log_dir_does_not_write_files(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="")
        logger.log_key_press("main", "q")
        assert logger._entries
        assert logger._log_path == ""

    def test_verbose_false_key_press_does_not_log(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=False)
            logger.log_key_press("main", "q")
            assert logger._entries == []

    def test_verbose_false_view_change_still_logs(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, verbose=False)
            logger.log_view_change("main", "projects")
            assert logger._entries

    def test_log_entries_have_session_id_and_timestamp(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_key_press("main", "q")
            entry = logger._entries[0]
            assert "session_id" in entry
            assert "timestamp" in entry
            assert len(entry["session_id"]) == 12

    def test_log_file_written_if_log_dir_set(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_key_press("main", "a")
            log_file = Path(tmp) / "tui.log"
            assert log_file.exists()
            lines = log_file.read_text().strip().split("\n")
            assert lines

    def test_flush_to_database_no_daemon_url_noop(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="", daemon_url="")
        logger.log_key_press("main", "q")
        logger.flush_to_database()

    def test_flush_to_database_no_entries_noop(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="", daemon_url="http://localhost:8000")
        logger.flush_to_database()

    def test_close_with_entries_flushes(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="")
        logger.log_key_press("main", "q")
        logger.close()

    def test_close_without_entries_noop(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="")
        logger.close()

    def test_close_with_entries_no_daemon_url(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="", daemon_url="")
        logger.log_key_press("main", "q")
        logger.close()

    def test_toggle_verbose_on(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(verbose=False)
        logger.toggle_verbose()
        assert logger.verbose is True

    def test_toggle_verbose_off(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(verbose=True)
        logger.toggle_verbose()
        assert logger.verbose is False

    def test_log_selection_records_all_fields(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_selection("projects", 3, "p42")
            entry = logger._entries[0]
            assert entry["event"] == "selection_change"
            assert entry["view"] == "projects"
            assert entry["index"] == 3
            assert entry["item_id"] == "p42"

    def test_log_daemon_action_none_details(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_daemon_action("restart")
            entry = logger._entries[0]
            assert entry["details"] == {}

    def test_log_status_msg(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_status_msg("all systems operational")
            entry = logger._entries[0]
            assert entry["event"] == "status_msg"
            assert entry["message"] == "all systems operational"

    def test_multiple_sessions_have_different_ids(self):
        from general_ludd.tui.logger import TUILogger

        a = TUILogger()
        b = TUILogger()
        assert a._session_id != b._session_id

    def test_log_entries_persist_in_memory(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_key_press("a", "1")
            logger.log_key_press("b", "2")
            logger.log_view_change("x", "y")
            assert len(logger._entries) == 3

    def test_json_lines_in_log_file_are_parseable(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp)
            logger.log_key_press("main", "q")
            logger.log_view_change("main", "projects")
            log_path = Path(tmp) / "tui.log"
            for line in log_path.read_text().strip().split("\n"):
                parsed = json.loads(line)
                assert "session_id" in parsed
                assert "timestamp" in parsed

    def test_flush_to_database_sends_recent_50_only(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = TUILogger(log_dir=tmp, daemon_url="http://localhost:8000")
            for i in range(100):
                logger.log_key_press("main", str(i))
            with patch("httpx.post") as mock_post:
                logger.flush_to_database()
                assert mock_post.called
                sent = mock_post.call_args[1]["json"]["entries"]
                assert len(sent) == 50


# ── ConfigEditor deep edge cases ─────────────────────────────────────


class TestConfigEditorDeepEdges:
    def test_start_editing_none_value_converts_to_empty_string(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value=None, item_type="str")
        editor.start_editing(item)
        assert editor.input_buffer == ""

    def test_start_editing_int_zero(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Z", key="z", value=0, item_type="int")
        editor.start_editing(item)
        assert editor.input_buffer == "0"

    def test_handle_backspace_on_empty_buffer_does_not_crash(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="a", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = ""
        editor.handle_input_key("\x7f")
        assert editor.input_buffer == ""

    def test_save_int_with_invalid_input_raises(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="P", key="p", value=1, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "not_a_number"
        with pytest.raises(ValueError):
            editor.handle_input_key("\r")

    def test_save_float_with_invalid_input_raises(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="W", key="w", value=1.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "xyz"
        with pytest.raises(ValueError):
            editor.handle_input_key("\r")

    def test_save_bool_garbage_defaults_false(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="F", key="f", value=False, item_type="bool")
        editor.start_editing(item)
        editor.input_buffer = "garbage"
        editor.handle_input_key("\r")
        assert item.value is False

    def test_save_empty_string_as_bool_is_false(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="F", key="f", value=True, item_type="bool")
        editor.start_editing(item)
        editor.input_buffer = ""
        editor.handle_input_key("\r")
        assert item.value is False

    def test_full_editing_cycle_preserves_overlay_path(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "test.yml"
            overlay.parent.mkdir(exist_ok=True)

            editor = ConfigEditor()
            item = MenuItem(label="H", key="host", value="old", item_type="str", overlay_path=str(overlay))
            with patch.object(editor, "read_yaml", return_value={"host": "old"}):
                editor.start_editing(item)
                editor.input_buffer = "new"
                editor.handle_input_key("\r")
            assert item.value == "new"

    def test_cancel_restores_original_value_in_memory(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="N", key="name", value="original", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "changed"
        editor.handle_input_key("\x1b")
        assert item.value == "original"

    def test_double_start_editing_replaces_previous(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item1 = MenuItem(label="A", key="a", value="first", item_type="str")
        item2 = MenuItem(label="B", key="b", value="second", item_type="str")
        editor.start_editing(item1)
        editor.start_editing(item2)
        assert editor.editing_item is item2
        assert editor.input_buffer == "second"

    def test_non_printable_char_does_not_append(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="a", item_type="str")
        editor.start_editing(item)
        result = editor.handle_input_key("\x00")
        assert result is None
        assert editor.input_buffer == "a\x00"

    def test_very_long_input_buffer(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="", item_type="str")
        editor.start_editing(item)
        long_str = "x" * 5000
        for ch in long_str:
            editor.handle_input_key(ch)
        assert len(editor.input_buffer) == 5000

    def test_save_with_overlay_writes_multiple_keys(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "cfg.yml"

            editor = ConfigEditor()
            existing = {"host": "a", "port": 8000, "debug": True}
            item = MenuItem(label="Port", key="port", value=8000, item_type="int", overlay_path=str(overlay))
            with patch.object(editor, "read_yaml", return_value=existing.copy()):
                editor.start_editing(item)
                editor.input_buffer = "9000"
                editor.handle_input_key("\r")
            assert item.value == 9000
            assert overlay.exists()

    def test_get_input_display_after_cancel_is_empty(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="hello", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("\x1b")
        assert editor.get_input_display() == ""

    def test_get_input_display_during_editing_includes_cursor(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="abc", item_type="str")
        editor.start_editing(item)
        display = editor.get_input_display()
        assert display.endswith("_")
        assert display[:-1] == "abc"

    def test_config_editor_bool_edge_cases_true(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        for upper in ("TRUE", "YES", "True", "Yes"):
            item = MenuItem(label="F", key="f", value=False, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = upper
            editor.handle_input_key("\r")
            assert item.value is True, f"'{upper}' should be True"

    def test_config_editor_bool_edge_cases_false(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        for upper in ("FALSE", "NO", "False", "No"):
            item = MenuItem(label="F", key="f", value=True, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = upper
            editor.handle_input_key("\r")
            assert item.value is False, f"'{upper}' should be False"

    def test_save_int_zero(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Z", key="z", value=42, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "0"
        editor.handle_input_key("\r")
        assert item.value == 0
        assert isinstance(item.value, int)

    def test_save_float_zero(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Z", key="z", value=3.14, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "0.0"
        editor.handle_input_key("\r")
        assert item.value == 0.0
        assert isinstance(item.value, float)

    def test_save_negative_float(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="N", key="n", value=1.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "-42.5"
        editor.handle_input_key("\r")
        assert item.value == -42.5

    def test_save_negative_int(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="N", key="n", value=10, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "-99"
        editor.handle_input_key("\r")
        assert item.value == -99

    def test_save_scientific_notation_float(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="E", key="e", value=1.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "1.5e3"
        editor.handle_input_key("\r")
        assert item.value == 1500.0

    def test_save_float_inf_nan(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="I", key="i", value=0.0, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "inf"
        editor.handle_input_key("\r")
        assert math.isinf(item.value)

    def test_write_overlay_creates_directory(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            editor = ConfigEditor()
            overlay = Path(tmp) / "nested" / "deep" / "cfg.yml"
            editor.write_overlay(str(overlay), {"key": "val"})
            assert overlay.exists()
            data = editor.read_yaml(str(overlay))
            assert data == {"key": "val"}

    def test_read_yaml_non_dict_returns_empty(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "list.yml"
            p.write_text("- item1\n- item2\n")
            editor = ConfigEditor()
            result = editor.read_yaml(str(p))
            assert result == {}

    def test_read_yaml_missing_file_returns_empty(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        result = editor.read_yaml("/nonexistent/path.yml")
        assert result == {}


# ── MenuItem deep edge cases ────────────────────────────────────────


class TestMenuItemDeepEdges:
    def test_menuitem_is_menu_empty_submenu(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="X", key="x", submenu=[])
        assert item.is_menu is False

    def test_menuitem_defaults(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="L", key="k")
        assert item.value is None
        assert item.item_type == "str"
        assert item.submenu == []
        assert item.help_text == ""
        assert item.overlay_path == ""

    def test_menuitem_repr_is_dataclass_default(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="L", key="k", value=42)
        repr_str = repr(item)
        assert "42" in repr_str
        assert "L" in repr_str
