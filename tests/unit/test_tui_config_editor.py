"""Tests for the TUI configuration editor with menu navigation and overlay-file writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.tui.config_editor import (
    ConfigCategory,
    ConfigEditor,
    MenuItem,
)


class TestConfigMenu:
    def test_menu_item_has_required_fields(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="Database URL", key="db.url", value="sqlite:///test.db", item_type="str")
        assert item.label == "Database URL"
        assert item.key == "db.url"
        assert item.value == "sqlite:///test.db"
        assert item.item_type == "str"

    def test_menu_item_has_nested_children(self):
        from general_ludd.tui.config_editor import MenuItem

        child = MenuItem(label="Port", key="port", value=8000, item_type="int")
        parent = MenuItem(label="Server", key="server", submenu=[child])
        assert parent.is_menu
        assert len(parent.submenu) == 1
        assert parent.submenu[0].key == "port"

    def test_config_category_has_name_and_items(self):
        items = [MenuItem(label="Host", key="host", value="localhost", item_type="str")]
        cat = ConfigCategory(name="Server", menu_items=items, overlay_path="/tmp/test.yml")
        assert cat.name == "Server"
        assert len(cat.menu_items) == 1

    def test_config_editor_builds_all_categories(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        cats = editor.get_categories()
        assert isinstance(cats, list)
        assert len(cats) >= 6
        names = {c.name for c in cats}
        assert "Database" in names
        assert "Model Routing" in names

    def test_config_editor_writes_overlay(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.yml"
            editor = ConfigEditor()
            editor.write_overlay(str(overlay), {"database": {"url": "postgresql://test"}})
            assert overlay.exists()
            content = overlay.read_text()
            assert "postgresql" in content

    def test_config_editor_reads_config_file(self):
        from general_ludd.tui.config_editor import ConfigEditor

        with tempfile.TemporaryDirectory() as tmp:
            cf = Path(tmp) / "test.yml"
            cf.write_text("database:\n  url: sqlite://\n  port: 5432\n")
            editor = ConfigEditor()
            data = editor.read_yaml(str(cf))
            assert data["database"]["url"] == "sqlite://"
            assert data["database"]["port"] == 5432

    def test_menu_navigation_parent_child(self):
        from general_ludd.tui.config_editor import ConfigEditor

        editor = ConfigEditor()
        cats = editor.get_categories()
        db_cat = next(c for c in cats if c.name == "Database")
        assert len(db_cat.menu_items) > 0

    def test_menu_update_value(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        item.value = 9000
        assert item.value == 9000

    def test_bool_menu_item_toggles(self):
        from general_ludd.tui.config_editor import MenuItem

        item = MenuItem(label="Enabled", key="enabled", value=False, item_type="bool")
        item.value = True
        assert item.value is True
        item.value = False
        assert item.value is False


class TestConfigEditorStartEditing:
    def test_start_editing_sets_editing_state_and_buffer(self):
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=5432, item_type="int")
        editor.start_editing(item)
        assert editor.editing is True
        assert editor.editing_item is item
        assert editor.input_buffer == "5432"

    def test_start_editing_refuses_menu_items(self):
        editor = ConfigEditor()
        child = MenuItem(label="Port", key="port", value=5432, item_type="int")
        parent = MenuItem(label="Server", key="server", submenu=[child])
        editor.start_editing(parent)
        assert editor.editing is False
        assert editor.editing_item is None

    def test_start_editing_none_value_produces_empty_buffer(self):
        editor = ConfigEditor()
        item = MenuItem(label="API Key", key="key", value=None, item_type="str")
        editor.start_editing(item)
        assert editor.input_buffer == ""
        assert editor.editing is True

    def test_start_editing_stores_explicit_overlay_path(self):
        editor = ConfigEditor()
        item = MenuItem(label="Host", key="host", value="localhost", item_type="str")
        editor.start_editing(item, overlay_path="/tmp/explicit.yml")
        assert editor._active_overlay_path == "/tmp/explicit.yml"

    def test_start_editing_falls_back_to_item_overlay_path(self):
        editor = ConfigEditor()
        item = MenuItem(label="Host", key="host", value="localhost", item_type="str", overlay_path="/tmp/fallback.yml")
        editor.start_editing(item)
        assert editor._active_overlay_path == "/tmp/fallback.yml"


class TestConfigEditorHandleInputKey:
    def test_backspace_removes_last_char_while_editing(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="hello", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("\x7f")
        assert editor.input_buffer == "hell"

    def test_backspace_does_not_crash_when_not_editing(self):
        editor = ConfigEditor()
        result = editor.handle_input_key("\x7f")
        assert result is None

    def test_enter_saves_and_returns_saved(self):
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item)
        editor.input_buffer = "9000"
        result = editor.handle_input_key("\r")
        assert result == "saved"
        assert editor.editing is False
        assert item.value == 9000

    def test_enter_is_noop_when_not_editing(self):
        editor = ConfigEditor()
        result = editor.handle_input_key("\r")
        assert result is None

    def test_escape_cancels_and_returns_cancelled(self):
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item)
        result = editor.handle_input_key("\x1b")
        assert result == "cancelled"
        assert editor.editing is False
        assert editor.input_buffer == ""
        assert editor.editing_item is None

    def test_escape_is_noop_when_not_editing(self):
        editor = ConfigEditor()
        result = editor.handle_input_key("\x1b")
        assert result is None

    def test_typing_appends_to_buffer_while_editing(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("a")
        editor.handle_input_key("b")
        editor.handle_input_key("c")
        assert editor.input_buffer == "abc"

    def test_typing_is_ignored_when_not_editing(self):
        editor = ConfigEditor()
        result = editor.handle_input_key("x")
        assert result is None
        assert editor.input_buffer == ""


class TestConfigEditorSaveEdit:
    def test_save_coerces_int_type(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "9090"
        editor._save_edit()
        assert item.value == 9090
        assert isinstance(item.value, int)

    def test_save_coerces_float_type(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Cost", key="cost", value=1.0, item_type="float")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "2.5"
        editor._save_edit()
        assert item.value == 2.5
        assert isinstance(item.value, float)

    def test_save_coerces_bool_true_values(self, tmp_path):
        overlay = tmp_path / "config.yml"
        for raw in ("true", "1", "yes", "True", "YES"):
            editor = ConfigEditor()
            item = MenuItem(label="Enabled", key="enabled", value=False, item_type="bool")
            editor.start_editing(item, overlay_path=str(overlay))
            editor.input_buffer = raw
            editor._save_edit()
            assert item.value is True, f"'{raw}' should coerce to True"

    def test_save_coerces_bool_false_values(self, tmp_path):
        overlay = tmp_path / "config.yml"
        for raw in ("false", "0", "no", "False", "anythingelse"):
            editor = ConfigEditor()
            item = MenuItem(label="Enabled", key="enabled", value=True, item_type="bool")
            editor.start_editing(item, overlay_path=str(overlay))
            editor.input_buffer = raw
            editor._save_edit()
            assert item.value is False, f"'{raw}' should coerce to False"

    def test_save_passthrough_str_type(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        item = MenuItem(label="URL", key="url", value="old", item_type="str")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "new_value"
        editor._save_edit()
        assert item.value == "new_value"

    def test_save_writes_overlay_file(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Host", key="host", value="localhost", item_type="str")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "prod.example.com"
        editor._save_edit()
        assert overlay.exists()
        data = editor.read_yaml(str(overlay))
        assert data.get("host") == "prod.example.com"

    def test_save_clears_editing_state(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item, overlay_path=str(overlay))
        editor._save_edit()
        assert editor.editing is False
        assert editor.input_buffer == ""
        assert editor.editing_item is None

    def test_save_without_editing_item_does_not_crash(self):
        editor = ConfigEditor()
        editor._save_edit()
        assert editor.editing is False

    def test_save_no_overlay_path_does_not_write_file(self, tmp_path):
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item, overlay_path="")
        editor.input_buffer = "9999"
        editor._save_edit()
        assert item.value == 9999

    def test_save_merges_into_existing_overlay(self, tmp_path):
        overlay = tmp_path / "config.yml"
        overlay.write_text("existing_key: old_value\n")
        editor = ConfigEditor()
        item = MenuItem(label="New", key="new_key", value="new_val", item_type="str")
        editor.start_editing(item, overlay_path=str(overlay))
        editor._save_edit()
        data = editor.read_yaml(str(overlay))
        assert data["existing_key"] == "old_value"
        assert data["new_key"] == "new_val"


class TestConfigEditorInputDisplay:
    def test_returns_empty_when_not_editing(self):
        editor = ConfigEditor()
        assert editor.get_input_display() == ""

    def test_returns_buffer_with_cursor_when_editing(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="hi", item_type="str")
        editor.start_editing(item)
        assert editor.get_input_display() == "hi_"

    def test_returns_cursor_only_for_empty_buffer(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value=None, item_type="str")
        editor.start_editing(item)
        assert editor.get_input_display() == "_"


class TestConfigEditorCategories:
    def test_all_eight_categories_present(self):
        editor = ConfigEditor()
        cats = editor.get_categories()
        names = {c.name for c in cats}
        expected = {
            "Database",
            "Model Routing",
            "Process Isolation",
            "Binary Paths",
            "Budget",
            "Secrets",
            "AI Provider Keys",
            "Cloud Credentials",
        }
        assert names == expected

    def test_overlay_paths_use_overlay_dir(self):
        editor = ConfigEditor()
        cats = editor.get_categories()
        for cat in cats:
            base = os.path.basename(cat.overlay_path)
            assert base.endswith(".yml")
            assert editor._overlay_dir in cat.overlay_path

    def test_ai_provider_keys_has_submenus(self):
        editor = ConfigEditor()
        cats = editor.get_categories()
        ai_cat = next(c for c in cats if c.name == "AI Provider Keys")
        menu_names = {m.label for m in ai_cat.menu_items}
        expected_providers = {
            "Z.AI",
            "OpenRouter",
            "OpenCode",
            "OpenAI",
            "Anthropic",
            "HuggingFace",
            "Together AI",
            "Slurm",
        }
        assert menu_names >= expected_providers
        for m in ai_cat.menu_items:
            if m.is_menu:
                assert len(m.submenu) >= 2

    def test_cloud_credentials_has_submenus(self):
        editor = ConfigEditor()
        cats = editor.get_categories()
        cloud_cat = next(c for c in cats if c.name == "Cloud Credentials")
        menu_names = {m.label for m in cloud_cat.menu_items}
        assert menu_names >= {"AWS", "Azure", "GCP"}
        for m in cloud_cat.menu_items:
            if m.is_menu:
                assert len(m.submenu) >= 3


class TestConfigEditorOverlayPathFor:
    def test_overlay_path_for_joins_overlay_dir_and_name(self):
        editor = ConfigEditor()
        result = editor._overlay_path_for("database.yml")
        assert result.endswith("database.yml")
        assert editor._overlay_dir in result


class TestConfigEditorReadYaml:
    def test_read_missing_file_returns_empty_dict(self, tmp_path):
        editor = ConfigEditor()
        result = editor.read_yaml(str(tmp_path / "nonexistent.yml"))
        assert result == {}

    def test_read_non_dict_yaml_returns_empty_dict(self, tmp_path):
        p = tmp_path / "list.yml"
        p.write_text("- item1\n- item2\n")
        editor = ConfigEditor()
        result = editor.read_yaml(str(p))
        assert result == {}


class TestConfigEditorWriteOverlay:
    def test_write_creates_parent_directories(self, tmp_path):
        deep = tmp_path / "a" / "b" / "config.yml"
        editor = ConfigEditor()
        editor.write_overlay(str(deep), {"key": "value"})
        assert deep.exists()

    def test_write_yaml_is_roundtrip_preserving(self, tmp_path):
        overlay = tmp_path / "config.yml"
        editor = ConfigEditor()
        data = {"server": {"host": "localhost", "port": 5432}}
        editor.write_overlay(str(overlay), data)
        roundtripped = editor.read_yaml(str(overlay))
        assert roundtripped == data


class TestConfigEditorInit:
    def test_default_config_dir_is_gludd(self):
        editor = ConfigEditor()
        assert "gludd" in editor._config_dir

    def test_custom_config_dir_is_stored(self):
        editor = ConfigEditor(config_dir="/custom/path")
        assert editor._config_dir == "/custom/path"
        assert editor._overlay_dir == "/custom/path/fs"

    def test_overlay_dir_is_config_dir_plus_fs(self):
        editor = ConfigEditor(config_dir="/tmp/myconfig")
        assert editor._overlay_dir == "/tmp/myconfig/fs"


class TestMenuItemIsMenu:
    def test_is_menu_true_with_submenu(self):
        child = MenuItem(label="Child", key="child", value=1, item_type="int")
        parent = MenuItem(label="Parent", key="parent", submenu=[child])
        assert parent.is_menu is True

    def test_is_menu_false_without_submenu(self):
        item = MenuItem(label="Field", key="field", value="hello", item_type="str")
        assert item.is_menu is False

    def test_is_menu_false_with_empty_submenu(self):
        item = MenuItem(label="Field", key="field", value="hello", item_type="str", submenu=[])
        assert item.is_menu is False


class TestMenuFieldsDeeper:
    def test_handles_zero_int_value(self):
        item = MenuItem(label="Count", key="count", value=0, item_type="int")
        assert item.value == 0

    def test_handles_empty_string_value(self):
        item = MenuItem(label="Key", key="api_key", value="", item_type="str")
        assert item.value == ""

    def test_default_help_text_is_empty(self):
        item = MenuItem(label="X", key="x")
        assert item.help_text == ""

    def test_default_overlay_path_is_empty(self):
        item = MenuItem(label="X", key="x")
        assert item.overlay_path == ""

    def test_default_item_type_is_str(self):
        item = MenuItem(label="X", key="x")
        assert item.item_type == "str"


class TestConfigEditorHandleInputKeyDeeper:
    def test_backspace_on_empty_buffer_is_noop(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("\x7f")
        assert editor.input_buffer == ""

    def test_backspace_on_single_char_empties_buffer(self):
        editor = ConfigEditor()
        item = MenuItem(label="Name", key="name", value="X", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("\x7f")
        assert editor.input_buffer == ""

    def test_escape_then_typing_is_ignored_after_cancel(self):
        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="a", item_type="str")
        editor.start_editing(item)
        editor.handle_input_key("\x1b")
        result = editor.handle_input_key("z")
        assert result is None
        assert editor.input_buffer == ""


class TestConfigEditorSaveEditCoercionEdges:
    def test_int_coercion_raises_on_non_numeric(self, tmp_path):
        overlay = tmp_path / "cfg.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Port", key="port", value=8000, item_type="int")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "not_a_number"
        with pytest.raises(ValueError):
            editor._save_edit()

    def test_float_coercion_raises_on_non_numeric(self, tmp_path):
        overlay = tmp_path / "cfg.yml"
        editor = ConfigEditor()
        item = MenuItem(label="Cost", key="cost", value=1.0, item_type="float")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "abc"
        with pytest.raises(ValueError):
            editor._save_edit()

    def test_unknown_item_type_passthrough_as_str(self, tmp_path):
        overlay = tmp_path / "cfg.yml"
        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="v", item_type="list")
        editor.start_editing(item, overlay_path=str(overlay))
        editor.input_buffer = "[1, 2, 3]"
        editor._save_edit()
        assert item.value == "[1, 2, 3]"


class TestConfigEditorReadYamlEdges:
    def test_read_malformed_yaml_raises(self, tmp_path):
        import yaml

        p = tmp_path / "bad.yml"
        p.write_text("key: value\n  bad indent: oops\n")
        editor = ConfigEditor()
        raised = False
        try:
            editor.read_yaml(str(p))
        except yaml.YAMLError:
            raised = True
        assert raised, "Expected yaml.YAMLError for malformed indentation"

    def test_read_empty_file_returns_empty_dict(self, tmp_path):
        p = tmp_path / "empty.yml"
        p.write_text("")
        editor = ConfigEditor()
        result = editor.read_yaml(str(p))
        assert result == {}


class TestConfigEditorWriteOverlayEdges:
    def test_write_empty_dict_produces_empty_yaml(self, tmp_path):
        overlay = tmp_path / "cfg.yml"
        editor = ConfigEditor()
        editor.write_overlay(str(overlay), {})
        assert overlay.exists()
        data = editor.read_yaml(str(overlay))
        assert data == {}

    def test_write_overlay_overwrites_existing_file(self, tmp_path):
        overlay = tmp_path / "cfg.yml"
        overlay.write_text("old: value\n")
        editor = ConfigEditor()
        editor.write_overlay(str(overlay), {"new": "data"})
        data = editor.read_yaml(str(overlay))
        assert data == {"new": "data"}
        assert "old" not in data


class TestConfigEditorCategoriesEdges:
    def test_each_category_has_unique_overlay_path(self):
        editor = ConfigEditor()
        cats = editor.get_categories()
        paths = [c.overlay_path for c in cats]
        assert len(paths) == len(set(paths))


class TestConfigEditorInputDisplayEdges:
    def test_long_buffer_display_includes_cursor(self):
        editor = ConfigEditor()
        item = MenuItem(label="X", key="x", value="a" * 80, item_type="str")
        editor.start_editing(item)
        display = editor.get_input_display()
        assert display.endswith("_")
        assert display.startswith("a" * 80)
