"""Tests for generic _make_table factory consolidating 26 table builders."""

from __future__ import annotations

import pytest
from rich.table import Table


def _import_make_table():
    from general_ludd.tui.tables import _make_table
    return _make_table


class TestMakeTableFactory:
    def test_basic_two_column_table(self):
        make = _import_make_table()
        t = make(
            title="Test",
            columns=[("Name", "cyan", 2, 6), ("Value", "green", 3, 8)],
            rows=[("foo", "bar"), ("baz", "qux")],
        )
        assert isinstance(t, Table)
        assert t.title == "Test"
        assert len(t.columns) == 2
        assert t.columns[0].header == "Name"
        assert t.columns[1].header == "Value"

    def test_empty_rows_with_placeholder(self):
        make = _import_make_table()
        t = make(
            title="Empty",
            columns=[("Col", "cyan", 1, 4)],
            rows=[],
            empty_msg="No items",
        )
        assert isinstance(t, Table)

    def test_empty_rows_without_placeholder(self):
        make = _import_make_table()
        t = make(
            title="Empty",
            columns=[("Col", "cyan", 1, 4)],
            rows=[],
        )
        assert isinstance(t, Table)

    def test_show_header_default_true(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
        )
        assert t.show_header is True

    def test_show_header_false(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
            show_header=False,
        )
        assert t.show_header is False

    def test_expand_true(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
        )
        assert t.expand is True

    def test_title_justify_left(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
        )
        assert t.title_justify == "left"

    def test_column_ratio_set(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 2, 6), ("B", "green", 3, 8)],
            rows=[("x", "y")],
        )
        assert t.columns[0].ratio == 2
        assert t.columns[1].ratio == 3

    def test_column_no_wrap(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
        )
        assert t.columns[0].no_wrap is True

    def test_column_min_width(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 10)],
            rows=[("x",)],
        )
        assert t.columns[0].min_width == 10

    def test_selected_idx_adds_marker(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("Name", "cyan", 2, 6)],
            rows=[("a",), ("b",), ("c",)],
            selected_idx=1,
        )
        assert isinstance(t, Table)

    def test_selected_idx_none_no_marker(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("Name", "cyan", 2, 6)],
            rows=[("a",), ("b",)],
            selected_idx=None,
        )
        assert isinstance(t, Table)

    def test_row_formatter_callback(self):
        make = _import_make_table()
        rows_data = [{"n": "x", "s": "ok"}, {"n": "y", "s": "err"}]

        def formatter(item: dict, idx: int, sel: int | None) -> tuple[str, ...]:
            style = "[green]ok[/]" if item["s"] == "ok" else "[red]err[/]"
            return (item["n"], style)

        t = make(
            title="T",
            columns=[("Name", "cyan", 2, 6), ("Status", "green", 1, 4)],
            data=rows_data,
            row_formatter=formatter,
        )
        assert isinstance(t, Table)

    def test_data_and_rows_mutually_exclusive(self):
        make = _import_make_table()
        with pytest.raises(ValueError, match="rows or data"):
            make(
                title="T",
                columns=[("A", "cyan", 1, 4)],
                rows=[("x",)],
                data=[{"a": "x"}],
            )

    def test_term_width_accepted(self):
        make = _import_make_table()
        t = make(
            title="T",
            columns=[("A", "cyan", 1, 4)],
            rows=[("x",)],
            term_width=120,
        )
        assert isinstance(t, Table)
