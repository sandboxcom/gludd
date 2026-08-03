"""Enhanced TUI component tests: config editor input, type coercion, breadcrumb edges, table edges."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch


class TestConfigEditorInputHandling:
    def test_start_editing_sets_state(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=5432, item_type="int")
        editor.start_editing(item)
        assert editor.editing is True
        assert editor.editing_item is item
        assert editor.input_buffer == "5432"

    def test_start_editing_submenu_is_noop(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        parent = MenuItem(
            label="Server", key="server", submenu=[MenuItem(label="Port", key="port", value=8000, item_type="int")]
        )
        editor.start_editing(parent)
        assert editor.editing is False
        assert editor.editing_item is None

    def test_handle_backspace_in_editing_mode(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Host", key="host", value="localhost", item_type="str")
        editor.start_editing(item)
        result = editor.handle_input_key("\x7f")
        assert result is None
        assert editor.input_buffer == "localhos"

    def test_handle_enter_saves_and_returns_saved(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Host", key="host", value="original", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "modified"
        result = editor.handle_input_key("\r")
        assert result == "saved"
        assert editor.editing is False
        assert item.value == "modified"

    def test_handle_escape_cancels_edit(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="URL", key="url", value="http://old", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "http://new"
        result = editor.handle_input_key("\x1b")
        assert result == "cancelled"
        assert editor.editing is False
        assert editor.input_buffer == ""
        assert editor.editing_item is None
        assert item.value == "http://old"

    def test_handle_normal_char_appends_to_buffer(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="a", item_type="str")
        editor.start_editing(item)
        result = editor.handle_input_key("b")
        assert result is None
        assert editor.input_buffer == "ab"

    def test_handle_key_when_not_editing_returns_none(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        assert editor.handle_input_key("a") is None
        assert editor.handle_input_key("\r") is None
        assert editor.handle_input_key("\x7f") is None
        assert editor.handle_input_key("\x1b") is None

    def test_get_input_display_shows_buffer_with_cursor(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Label", key="label", value="hello", item_type="str")
        editor.start_editing(item)
        display = editor.get_input_display()
        assert display == "hello_"

    def test_get_input_display_empty_when_not_editing(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        assert editor.get_input_display() == ""


class TestConfigEditorTypeCoercion:
    def test_save_int_coerces_value(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=5432, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "9000"
        editor.handle_input_key("\r")
        assert item.value == 9000
        assert isinstance(item.value, int)

    def test_save_float_coerces_value(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Weight", key="weight", value=0.5, item_type="float")
        editor.start_editing(item)
        editor.input_buffer = "1.25"
        editor.handle_input_key("\r")
        assert item.value == 1.25
        assert isinstance(item.value, float)

    def test_save_bool_true_coerces_value(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        for raw in ("true", "True", "1", "yes", "YES"):
            editor = ConfigEditor()
            item = MenuItem(label="Flag", key="flag", value=False, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = raw
            editor.handle_input_key("\r")
            assert item.value is True, f"'{raw}' should coerce to True"

    def test_save_bool_false_coerces_value(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        for raw in ("false", "FALSE", "0", "no", "No"):
            editor = ConfigEditor()
            item = MenuItem(label="Flag", key="flag", value=True, item_type="bool")
            editor.start_editing(item)
            editor.input_buffer = raw
            editor.handle_input_key("\r")
            assert item.value is False, f"'{raw}' should coerce to False"

    def test_save_str_keeps_value(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="URL", key="url", value="http://old", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "http://new/path"
        editor.handle_input_key("\r")
        assert item.value == "http://new/path"
        assert isinstance(item.value, str)

    def test_save_writes_overlay_file(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "test.yml"
            overlay.parent.mkdir(exist_ok=True)
            overlay.write_text("")

            editor = ConfigEditor()
            item = MenuItem(label="Host", key="host", value="oldhost", item_type="str", overlay_path=str(overlay))
            with patch.object(editor, "read_yaml", return_value={"host": "oldhost"}):
                editor.start_editing(item, overlay_path=str(overlay))
                editor.input_buffer = "newhost"
                editor.handle_input_key("\r")
            assert item.value == "newhost"
            assert overlay.exists()

    def test_multiple_edits_preserve_state(self):
        from general_ludd.tui.config_editor import ConfigEditor, MenuItem

        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="first", item_type="str")
        editor.start_editing(item)
        editor.input_buffer = "second"
        editor.handle_input_key("\r")
        assert item.value == "second"

        editor.start_editing(item)
        editor.input_buffer = "third"
        editor.handle_input_key("\r")
        assert item.value == "third"


class TestBreadcrumbEdges:
    def test_push_breadcrumb_idempotent(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb

        state = {"breadcrumb": ["main", "projects"]}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_pop_breadcrumb_returns_last_on_empty(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state = {"breadcrumb": ["main"]}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_breadcrumb_from_empty_returns_main(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb

        state: dict = {}
        result = pop_breadcrumb(state)
        assert result == "main"

    def test_render_breadcrumb_single_item(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        assert render_breadcrumb(["main"]) == "main"

    def test_render_breadcrumb_multi_item(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["main", "projects", "edit"])
        assert result == "main > projects > edit"


class TestTableFactoryEdges:
    def test_make_table_rows_and_data_raises(self):
        import pytest

        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 5)]
        with pytest.raises(ValueError, match="Specify rows or data, not both"):
            _make_table("Test", cols, rows=[("a",)], data=[{"a"}])

    def test_make_table_empty_rows_shows_empty_msg(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Name", "", 1, 5), ("Value", "", 1, 10)]
        table = _make_table("Test", cols, rows=[], empty_msg="No data")
        assert len(table.rows) == 1
        assert table.row_count == 1

    def test_make_table_empty_data_shows_empty_msg(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Name", "", 1, 5)]
        table = _make_table(
            "Test", cols, data=[], empty_msg="Nothing", row_formatter=lambda item, idx, sel: (str(item),)
        )
        assert len(table.rows) == 1

    def test_make_table_selected_row_gets_bold_reverse(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Name", "", 1, 20)]
        rows = [("alpha",), ("beta",), ("gamma",)]
        table = _make_table("Test", cols, rows=rows, selected_idx=1)
        assert len(table.rows) == 3
        found_bold = False
        for row in table.rows:
            style_str = str(getattr(row, "style", ""))
            if "bold" in style_str or "reverse" in style_str:
                found_bold = True
                break
        assert found_bold

    def test_make_table_data_formatter_with_selection(self):
        from general_ludd.tui.tables import _make_table

        def fmt(item, idx, sel_idx):
            marker = ">" if idx == sel_idx else " "
            return (f"{marker} {item['name']}",)

        cols = [("Name", "", 1, 20)]
        data = [{"name": "first"}, {"name": "second"}, {"name": "third"}]
        table = _make_table("Test", cols, data=data, row_formatter=fmt, selected_idx=0)
        assert len(table.rows) == 3

    def test_make_table_show_header_false(self):
        from general_ludd.tui.tables import _make_table

        cols = [("Col", "", 1, 10)]
        table = _make_table("Test", cols, rows=[("x",)], show_header=False)
        assert table.show_header is False
