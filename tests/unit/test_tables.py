from __future__ import annotations

from general_ludd.tui.tables import _make_table


def test_make_table_accepts_list_of_tuples():
    table = _make_table(
        title="Test",
        columns=[("Name", "cyan", 20, 10), ("Value", "green", 30, 10)],
        rows=[("foo", "bar")],
    )
    assert table is not None
    assert table.title == "Test"
