"""Tests for TUI config editor text input mode — enter, type, backspace, save, cancel."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.tui.config_editor import ConfigEditor, MenuItem


def _leaf_item(**kw):
    defaults = {"label": "Host", "key": "host", "value": "localhost", "item_type": "str"}
    defaults.update(kw)
    return MenuItem(**defaults)


class TestEnterEditMode:
    def test_start_editing_sets_flag(self):
        ed = ConfigEditor()
        item = _leaf_item()
        ed.start_editing(item)
        assert ed.editing is True

    def test_start_editing_initializes_buffer_with_current_value(self):
        ed = ConfigEditor()
        item = _leaf_item(value="hello")
        ed.start_editing(item)
        assert ed.input_buffer == "hello"

    def test_start_editing_stores_leaf_reference(self):
        ed = ConfigEditor()
        item = _leaf_item()
        ed.start_editing(item)
        assert ed.editing_item is item

    def test_start_editing_on_non_leaf_is_noop(self):
        ed = ConfigEditor()
        parent = MenuItem(label="X", key="x", submenu=[_leaf_item()])
        ed.start_editing(parent)
        assert ed.editing is False

    def test_start_editing_on_int_item_initializes_buffer_with_str(self):
        ed = ConfigEditor()
        item = _leaf_item(value=5432, item_type="int")
        ed.start_editing(item)
        assert ed.input_buffer == "5432"


class TestTypingCharacters:
    def test_append_single_char(self):
        ed = ConfigEditor()
        item = _leaf_item(value="")
        ed.start_editing(item)
        ed.handle_input_key("a")
        assert ed.input_buffer == "a"

    def test_append_multiple_chars(self):
        ed = ConfigEditor()
        item = _leaf_item(value="")
        ed.start_editing(item)
        for ch in "hello":
            ed.handle_input_key(ch)
        assert ed.input_buffer == "hello"

    def test_append_preserves_existing_value(self):
        ed = ConfigEditor()
        item = _leaf_item(value="abc")
        ed.start_editing(item)
        ed.handle_input_key("d")
        assert ed.input_buffer == "abcd"

    def test_typing_when_not_editing_is_noop(self):
        ed = ConfigEditor()
        ed.handle_input_key("x")
        assert ed.input_buffer == ""


class TestBackspace:
    def test_backspace_removes_last_char(self):
        ed = ConfigEditor()
        item = _leaf_item(value="abc")
        ed.start_editing(item)
        ed.handle_input_key("\x7f")
        assert ed.input_buffer == "ab"

    def test_backspace_on_empty_buffer_stays_empty(self):
        ed = ConfigEditor()
        item = _leaf_item(value="")
        ed.start_editing(item)
        ed.handle_input_key("\x7f")
        assert ed.input_buffer == ""

    def test_backspace_not_editing_is_noop(self):
        ed = ConfigEditor()
        ed.handle_input_key("\x7f")
        assert ed.input_buffer == ""


class TestEnterSaves:
    def test_enter_saves_and_exits_editing(self):
        ed = ConfigEditor()
        item = _leaf_item(value="old", overlay_path="/tmp/fake.yml")
        ed.start_editing(item)
        ed.handle_input_key("x")
        result = ed.handle_input_key("\r")
        assert ed.editing is False
        assert result == "saved"

    def test_enter_calls_write_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "test.yml"
            ed = ConfigEditor(config_dir=tmp)
            item = _leaf_item(
                key="host",
                value="old",
                overlay_path=str(overlay),
            )
            ed.start_editing(item)
            ed.input_buffer = ""
            for ch in "new-host":
                ed.handle_input_key(ch)
            ed.handle_input_key("\r")
            assert overlay.exists()
            data = ed.read_yaml(str(overlay))
            assert data["host"] == "new-host"

    def test_enter_updates_item_value(self):
        ed = ConfigEditor()
        item = _leaf_item(value="old")
        ed.start_editing(item)
        ed.input_buffer = ""
        for ch in "new":
            ed.handle_input_key(ch)
        ed.handle_input_key("\r")
        assert item.value == "new"

    def test_enter_converts_int_type(self):
        ed = ConfigEditor()
        item = _leaf_item(value=8000, item_type="int")
        ed.start_editing(item)
        ed.input_buffer = ""
        for ch in "9999":
            ed.handle_input_key(ch)
        ed.handle_input_key("\r")
        assert item.value == 9999
        assert isinstance(item.value, int)

    def test_enter_converts_float_type(self):
        ed = ConfigEditor()
        item = _leaf_item(value=0.5, item_type="float")
        ed.start_editing(item)
        ed.input_buffer = ""
        for ch in "1.25":
            ed.handle_input_key(ch)
        ed.handle_input_key("\r")
        assert item.value == 1.25
        assert isinstance(item.value, float)

    def test_enter_converts_bool_true_strings(self):
        ed = ConfigEditor()
        item = _leaf_item(value=False, item_type="bool")
        ed.start_editing(item)
        ed.input_buffer = ""
        for ch in "true":
            ed.handle_input_key(ch)
        ed.handle_input_key("\r")
        assert item.value is True

    def test_enter_converts_bool_false_strings(self):
        ed = ConfigEditor()
        item = _leaf_item(value=True, item_type="bool")
        ed.start_editing(item)
        ed.input_buffer = ""
        for ch in "false":
            ed.handle_input_key(ch)
        ed.handle_input_key("\r")
        assert item.value is False


class TestEscapeCancels:
    def test_escape_exits_editing_without_saving(self):
        ed = ConfigEditor()
        item = _leaf_item(value="original")
        ed.start_editing(item)
        for ch in "modified":
            ed.handle_input_key(ch)
        result = ed.handle_input_key("\x1b")
        assert ed.editing is False
        assert item.value == "original"
        assert result == "cancelled"

    def test_escape_clears_buffer(self):
        ed = ConfigEditor()
        item = _leaf_item(value="orig")
        ed.start_editing(item)
        ed.handle_input_key("Z")
        ed.handle_input_key("\x1b")
        assert ed.input_buffer == ""


class TestInputDisplay:
    def test_get_display_text_returns_buffer_with_cursor(self):
        ed = ConfigEditor()
        item = _leaf_item(value="abc")
        ed.start_editing(item)
        assert ed.get_input_display() == "abc_"
        ed.handle_input_key("d")
        assert ed.get_input_display() == "abcd_"

    def test_get_display_text_empty_when_not_editing(self):
        ed = ConfigEditor()
        assert ed.get_input_display() == ""
