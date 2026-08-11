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


class TestOverlayPathFor:
    def test_overlay_path_constructed_from_config_dir(self):
        ed = ConfigEditor(config_dir="/home/user/.config/gludd")
        path = ed._overlay_path_for("database.yml")
        assert path == "/home/user/.config/gludd/fs/database.yml"

    def test_overlay_path_default_config_dir(self):

        ed = ConfigEditor()
        path = ed._overlay_path_for("test.yml")
        assert path.endswith("/fs/test.yml")
        assert ".config/gludd" in path

    def test_overlay_path_with_nested_name(self):
        ed = ConfigEditor(config_dir="/cfg")
        path = ed._overlay_path_for("sub/deep.yml")
        assert path == "/cfg/fs/sub/deep.yml"


class TestStartEditingWithOverlayPath:
    def test_explicit_overlay_path_overrides_item_overlay(self):
        ed = ConfigEditor()
        item = _leaf_item(overlay_path="/ignore.yml")
        ed.start_editing(item, overlay_path="/real/path.yml")
        assert ed._active_overlay_path == "/real/path.yml"

    def test_item_overlay_used_when_no_explicit_path(self):
        ed = ConfigEditor()
        item = _leaf_item(overlay_path="/item/path.yml")
        ed.start_editing(item)
        assert ed._active_overlay_path == "/item/path.yml"

    def test_empty_overlay_when_no_paths(self):
        ed = ConfigEditor()
        item = _leaf_item()
        ed.start_editing(item)
        assert ed._active_overlay_path == ""


class TestHandleInputKeyWhenNotEditing:
    def test_enter_when_not_editing_returns_none(self):
        ed = ConfigEditor()
        result = ed.handle_input_key("\r")
        assert result is None
        assert ed.editing is False

    def test_escape_when_not_editing_returns_none(self):
        ed = ConfigEditor()
        result = ed.handle_input_key("\x1b")
        assert result is None
        assert ed.editing is False

    def test_backspace_when_not_editing_is_noop(self):
        ed = ConfigEditor()
        ed.handle_input_key("\x7f")
        assert ed.editing is False
        assert ed.input_buffer == ""


class TestSaveEditTypeCoercion:
    def test_int_coercion_from_string(self):
        ed = ConfigEditor()
        item = _leaf_item(value=0, item_type="int")
        ed.start_editing(item)
        ed.input_buffer = "42"
        ed._save_edit()
        assert item.value == 42
        assert isinstance(item.value, int)

    def test_float_coercion_from_string(self):
        ed = ConfigEditor()
        item = _leaf_item(value=0.0, item_type="float")
        ed.start_editing(item)
        ed.input_buffer = "3.14"
        ed._save_edit()
        assert item.value == 3.14
        assert isinstance(item.value, float)

    def test_bool_coercion_true_variants(self):
        for val in ("true", "1", "yes", "True", "TRUE"):
            ed = ConfigEditor()
            item = _leaf_item(value=False, item_type="bool")
            ed.start_editing(item)
            ed.input_buffer = val
            ed._save_edit()
            assert item.value is True, f"'{val}' should coerce to True"

    def test_bool_coercion_false_default(self):
        ed = ConfigEditor()
        item = _leaf_item(value=True, item_type="bool")
        ed.start_editing(item)
        ed.input_buffer = "nope"
        ed._save_edit()
        assert item.value is False

    def test_str_type_no_coercion(self):
        ed = ConfigEditor()
        item = _leaf_item(value="old", item_type="str")
        ed.start_editing(item)
        ed.input_buffer = "hello world 42"
        ed._save_edit()
        assert item.value == "hello world 42"
        assert isinstance(item.value, str)

    def test_save_clears_editing_state(self):
        ed = ConfigEditor()
        item = _leaf_item(value="x")
        ed.start_editing(item)
        ed._save_edit()
        assert ed.editing is False
        assert ed.input_buffer == ""
        assert ed.editing_item is None

    def test_save_with_no_editing_item_is_noop(self):
        ed = ConfigEditor()
        ed.editing = True
        ed.input_buffer = "garbage"
        ed._save_edit()
        assert ed.editing is True
        assert ed.input_buffer == "garbage"


class TestSaveEditWithOverlay:
    def test_save_writes_new_overlay_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ConfigEditor(config_dir=tmp)
            overlay = Path(tmp) / "fs" / "test.yml"
            item = _leaf_item(key="engine", value="sqlite", item_type="str")
            ed.start_editing(item, overlay_path=str(overlay))
            ed.input_buffer = "postgresql"
            ed._save_edit()
            assert overlay.exists()
            data = ed.read_yaml(str(overlay))
            assert data["engine"] == "postgresql"

    def test_save_merges_into_existing_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ConfigEditor(config_dir=tmp)
            overlay = Path(tmp) / "fs" / "test.yml"
            overlay.parent.mkdir(parents=True, exist_ok=True)
            overlay.write_text("existing_key: old_value\n")
            item = _leaf_item(key="new_key", value="old", item_type="str")
            ed.start_editing(item, overlay_path=str(overlay))
            ed.input_buffer = "new_value"
            ed._save_edit()
            data = ed.read_yaml(str(overlay))
            assert data["existing_key"] == "old_value"
            assert data["new_key"] == "new_value"

    def test_save_int_coerced_in_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ConfigEditor(config_dir=tmp)
            overlay = Path(tmp) / "fs" / "test.yml"
            item = _leaf_item(key="port", value=5432, item_type="int")
            ed.start_editing(item, overlay_path=str(overlay))
            ed.input_buffer = "9000"
            ed._save_edit()
            data = ed.read_yaml(str(overlay))
            assert data["port"] == 9000
            assert isinstance(data["port"], int)

    def test_save_bool_coerced_in_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ConfigEditor(config_dir=tmp)
            overlay = Path(tmp) / "fs" / "test.yml"
            item = _leaf_item(key="enabled", value=False, item_type="bool")
            ed.start_editing(item, overlay_path=str(overlay))
            ed.input_buffer = "true"
            ed._save_edit()
            data = ed.read_yaml(str(overlay))
            assert data["enabled"] is True

    def test_save_overlay_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ConfigEditor(config_dir=tmp)
            overlay = Path(tmp) / "fs" / "db" / "config.yml"
            item = _leaf_item(key="url", value="", item_type="str")
            ed.start_editing(item, overlay_path=str(overlay))
            ed.input_buffer = "sqlite:///db.sqlite3"
            ed._save_edit()
            assert overlay.exists()
            assert overlay.parent.exists()


class TestConfigEditorInit:
    def test_custom_config_dir(self):
        ed = ConfigEditor(config_dir="/custom/config")
        assert ed._config_dir == "/custom/config"
        assert ed._overlay_dir == "/custom/config/fs"

    def test_default_config_dir(self):

        ed = ConfigEditor()
        assert ed._config_dir.endswith(".config/gludd")
        assert ed._overlay_dir.endswith(".config/gludd/fs")

    def test_none_config_dir_uses_default(self):

        ed = ConfigEditor(config_dir=None)
        assert ".config/gludd" in ed._config_dir


class TestCategoryStructure:
    def test_all_categories_have_menu_items(self):
        ed = ConfigEditor()
        cats = ed.get_categories()
        for cat in cats:
            assert len(cat.menu_items) > 0, f"Category {cat.name} has no items"

    def test_submenu_items_have_is_menu_true(self):
        ed = ConfigEditor()
        cats = ed.get_categories()
        ai_providers = next(c for c in cats if c.name == "AI Provider Keys")
        menu_items = [item for item in ai_providers.menu_items if item.is_menu]
        assert len(menu_items) > 0

    def test_submenu_children_are_leaf_items(self):
        ed = ConfigEditor()
        cats = ed.get_categories()
        ai_providers = next(c for c in cats if c.name == "AI Provider Keys")
        zai = next(item for item in ai_providers.menu_items if item.key == "zai")
        assert zai.is_menu
        for child in zai.submenu:
            assert not child.is_menu

    def test_budget_category_has_numeric_items(self):
        ed = ConfigEditor()
        cats = ed.get_categories()
        budget = next(c for c in cats if c.name == "Budget")
        types = {item.item_type for item in budget.menu_items}
        assert "float" in types
        assert "int" in types

    def test_cloud_creds_have_nested_providers(self):
        ed = ConfigEditor()
        cats = ed.get_categories()
        cloud = next(c for c in cats if c.name == "Cloud Credentials")
        provider_keys = {item.key for item in cloud.menu_items}
        assert "aws" in provider_keys
        assert "azure" in provider_keys
        assert "gcp" in provider_keys
