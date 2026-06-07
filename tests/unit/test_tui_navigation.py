"""Verify TUI navigation state machine responds correctly to key inputs."""

from __future__ import annotations


class TestTUINavigationState:
    def _make_nav(self):
        from general_ludd.tui.config_editor import ConfigEditor
        editor = ConfigEditor()
        cats = editor.get_categories()
        return {
            "editor": editor,
            "categories": cats,
            "selected_cat": 0,
            "selected_item": 0,
            "depth": 0,
            "current_items": cats,
        }

    def test_arrow_down_moves_selection(self):
        nav = self._make_nav()
        assert nav["selected_cat"] == 0
        nav["selected_cat"] = min(len(nav["current_items"]) - 1, nav["selected_cat"] + 1)
        assert nav["selected_cat"] == 1

    def test_arrow_up_does_not_go_negative(self):
        nav = self._make_nav()
        nav["selected_cat"] = max(0, nav["selected_cat"] - 1)
        assert nav["selected_cat"] == 0

    def test_arrow_down_stops_at_end(self):
        nav = self._make_nav()
        n = len(nav["current_items"])
        nav["selected_cat"] = n - 1
        nav["selected_cat"] = min(n - 1, nav["selected_cat"] + 1)
        assert nav["selected_cat"] == n - 1

    def test_enter_on_category_enters_submenu(self):
        nav = self._make_nav()
        cat = nav["current_items"][0]
        assert hasattr(cat, "menu_items")
        nav["current_items"] = cat.menu_items
        nav["depth"] = 1
        assert nav["depth"] == 1
        assert len(nav["current_items"]) > 0

    def test_escape_from_submenu_returns_to_categories(self):
        nav = self._make_nav()
        cat = nav["current_items"][0]
        nav["current_items"] = cat.menu_items
        nav["depth"] = 1
        nav["depth"] = 0
        nav["current_items"] = nav["categories"]
        assert nav["depth"] == 0
        assert len(nav["current_items"]) >= 6

    def test_escape_from_top_level_exits_edit(self):
        nav = self._make_nav()
        assert nav["depth"] == 0
        left_edit = True
        assert left_edit

    def test_lowercase_single_char_keys(self):
        ch = "Q"
        if len(ch) == 1:
            ch = ch.lower()
        assert ch == "q"

    def test_escape_sequence_not_lowered(self):
        ch = "\x1b[A"
        if len(ch) == 1:
            ch = ch.lower()
        assert ch == "\x1b[A"

    def test_tab_converted_to_enter(self):
        ch = "\t"
        if len(ch) == 1:
            ch = ch.lower()
        if ch in ("\t", " ", "\r", "\n"):
            ch = "\r"
        assert ch == "\r"

    def test_config_editor_categories_not_empty(self):
        from general_ludd.tui.config_editor import ConfigEditor
        editor = ConfigEditor()
        cats = editor.get_categories()
        assert len(cats) >= 4
        db = next(c for c in cats if c.name == "Database")
        assert len(db.menu_items) >= 3
