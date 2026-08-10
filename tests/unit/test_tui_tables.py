"""Deep tests for TUI table factory (_make_table in tables.py)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.tui.tables import _make_table


def _cols(n: int = 3) -> list[tuple[str, str, int, int]]:
    return [(f"Col{i}", "", 1, 5) for i in range(n)]


class TestMakeTableColumns:
    def test_adds_each_column_preserving_order(self):
        cols = [("A", "red", 2, 10), ("B", "blue", 1, 5)]
        t = _make_table("Test", cols)
        assert len(t.columns) == 2
        assert t.columns[0].header == "A"
        assert t.columns[1].header == "B"

    def test_column_style_passed_through(self):
        t = _make_table("T", [("X", "bold green", 1, 3)])
        assert t.columns[0].style == "bold green"

    def test_column_no_wrap_is_always_true(self):
        t = _make_table("T", [("X", "", 1, 3)])
        assert t.columns[0].no_wrap is True

    def test_column_ratio_and_min_width(self):
        t = _make_table("T", [("X", "", 7, 15)])
        assert t.columns[0].ratio == 7
        assert t.columns[0].min_width == 15


class TestMakeTableTitleAndHeader:
    def test_title_set_on_table(self):
        t = _make_table("Hello World", _cols())
        assert "Hello World" in (t.title or "")

    def test_title_justify_is_left(self):
        t = _make_table("T", _cols())
        assert t.title_justify == "left"

    def test_show_header_true_by_default(self):
        t = _make_table("T", _cols())
        assert t.show_header is True

    def test_show_header_false(self):
        t = _make_table("T", _cols(), show_header=False)
        assert t.show_header is False

    def test_expand_is_true(self):
        t = _make_table("T", _cols())
        assert t.expand is True

    def test_width_set_to_term_width(self):
        t = _make_table("T", _cols(), term_width=99)
        assert t.width == 99


class TestMakeTableRows:
    def test_rows_added_directly(self):
        t = _make_table("T", _cols(2), rows=[("r1c1", "r1c2"), ("r2c1", "r2c2")])
        assert t.row_count == 2

    def test_selecting_a_row_bolds_and_reverses(self):
        cols = _cols(2)
        t = _make_table("T", cols, rows=[("a", "b"), ("c", "d")], selected_idx=1)
        rows_list = list(t.rows)
        assert len(rows_list) == 2
        row0, row1 = rows_list
        assert row0.style != "bold reverse"
        assert row1.style == "bold reverse"

    def test_selected_row_gets_triangle_prefix(self):
        t = _make_table("T", _cols(2), rows=[("hello", "world")], selected_idx=0)
        cell_text = str(t.columns[0]._cells[0] if t.columns[0]._cells else "")
        assert "\u25b6" in cell_text
        assert "hello" in cell_text


class TestMakeTableDataRowFormatter:
    @staticmethod
    def _fmt(item: Any, idx: int, selected_idx: int | None) -> tuple[str, ...]:
        return (f"{idx}-{item}", str(item))

    def test_data_with_formatter_produces_rows(self):
        t = _make_table("T", _cols(2), data=[100, 200], row_formatter=self._fmt)
        assert t.row_count == 2

    def test_formatter_receives_index(self):
        seen: list[int] = []

        def fmt(item: Any, idx: int, sel: int | None) -> tuple[str, ...]:
            seen.append(idx)
            return (str(item),)

        _make_table("T", _cols(1), data=["a", "b", "c"], row_formatter=fmt)
        assert seen == [0, 1, 2]

    def test_formatter_receives_selected_idx(self):
        received_sel: list[int | None] = []

        def fmt(item: Any, idx: int, sel: int | None) -> tuple[str, ...]:
            received_sel.append(sel)
            return (str(item),)

        _make_table("T", _cols(1), data=["x"], row_formatter=fmt, selected_idx=5)
        assert received_sel == [5]


class TestMakeTableMutualExclusion:
    def test_rows_and_data_together_raises(self):
        with pytest.raises(ValueError, match="Specify rows or data"):
            _make_table("T", _cols(1), rows=[("x",)], data=[1], row_formatter=lambda *a: ("",))


class TestMakeTableEmpty:
    def test_empty_rows_with_empty_msg_shows_message(self):
        t = _make_table("T", _cols(2), rows=[], empty_msg="Nothing here")
        cells = [r for r in t.columns[0].cells if r]
        assert any("Nothing here" in str(c) for c in cells)

    def test_empty_rows_without_empty_msg_shows_nothing(self):
        t = _make_table("T", _cols(2), rows=[])
        assert t.row_count == 0

    def test_empty_data_with_empty_msg_shows_message(self):
        t = _make_table("T", _cols(2), data=[], row_formatter=lambda *a: ("", ""), empty_msg="No items")
        cells = [r for r in t.columns[0].cells if r]
        assert any("No items" in str(c) for c in cells)

    def test_empty_data_without_empty_msg_shows_nothing(self):
        t = _make_table("T", _cols(2), data=[], row_formatter=lambda *a: ("", ""))
        assert t.row_count == 0

    def test_none_rows_uses_empty_list(self):
        t = _make_table("T", _cols(2), rows=None)
        assert t.row_count == 0


class TestMakeTableEdgeCases:
    def test_single_column_table(self):
        t = _make_table("T", [("Solo", "cyan", 1, 3)], rows=[("one",), ("two",)])
        assert len(t.columns) == 1
        assert t.row_count == 2

    def test_selected_out_of_bounds_no_highlight(self):
        t = _make_table("T", _cols(2), rows=[("a", "b")], selected_idx=999)
        rows_list = list(t.rows)
        assert rows_list[0].style != "bold reverse"

    def test_selected_idx_none_no_highlight(self):
        t = _make_table("T", _cols(2), rows=[("a", "b")], selected_idx=None)
        rows_list = list(t.rows)
        assert rows_list[0].style != "bold reverse"

    def test_zero_columns_does_not_crash(self):
        t = _make_table("T", [])
        assert len(t.columns) == 0

    def test_row_count_matches_input(self):
        rows = [("a", "b"), ("c", "d"), ("e", "f")]
        t = _make_table("T", _cols(2), rows=rows)
        assert t.row_count == 3
