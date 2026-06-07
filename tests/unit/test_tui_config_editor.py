"""Tests for the TUI configuration editor with menu navigation and overlay-file writes."""

from __future__ import annotations

import tempfile
from pathlib import Path


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
        from general_ludd.tui.config_editor import ConfigCategory, MenuItem
        items = [MenuItem(label="Host", key="host", value="localhost", item_type="str")]
        cat = ConfigCategory(name="Server", menu_items=items, overlay_path="/tmp/test.yml")
        assert cat.name == "Server"
        assert len(cat.menu_items) == 1

    def test_config_editor_builds_all_categories(self):
        from general_ludd.tui.config_editor import ConfigEditor
        editor = ConfigEditor()
        cats = editor.get_categories()
        assert isinstance(cats, list)
        assert len(cats) >= 4
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
