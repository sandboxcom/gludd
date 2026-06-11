"""Generic table factory for consolidating TUI table builders."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rich.table import Table


def _make_table(
    title: str,
    columns: list[tuple[str, str, int, int]],
    rows: Sequence[tuple[str, ...]] | None = None,
    *,
    empty_msg: str = "",
    show_header: bool = True,
    selected_idx: int | None = None,
    term_width: int = 80,
    data: list[Any] | None = None,
    row_formatter: Callable[[Any, int, int | None], tuple[str, ...]] | None = None,
) -> Table:
    if rows is not None and data is not None:
        raise ValueError("Specify rows or data, not both")

    t = Table(
        title=title,
        show_header=show_header,
        expand=True,
        title_justify="left",
    )
    for name, style, ratio, min_w in columns:
        t.add_column(name, style=style, no_wrap=True, ratio=ratio, min_width=min_w)

    if data is not None and row_formatter is not None:
        if not data:
            if empty_msg:
                t.add_row(empty_msg, *[""] * (len(columns) - 1))
        else:
            for idx, item in enumerate(data):
                row = row_formatter(item, idx, selected_idx)
                t.add_row(*row)
        return t

    effective_rows = rows if rows is not None else []
    if not effective_rows:
        if empty_msg:
            t.add_row(empty_msg, *[""] * (len(columns) - 1))
        return t

    for idx, row in enumerate(effective_rows):
        if selected_idx is not None and idx == selected_idx:
            sel_row = tuple(
                f"\u25b6 {cell}" if i == 0 else cell
                for i, cell in enumerate(row)
            )
            t.add_row(*sel_row, style="bold reverse")
        else:
            t.add_row(*row)

    return t
