"""Generic table factory for consolidating TUI table builders."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from rich.table import Table

# Columns may be 4-tuples (name, style, ratio, min_width) or 5-tuples
# (name, style, ratio, min_width, max_width).  The 5th element is optional;
# passing None or omitting it leaves rich's max_width at its default.
_Column = tuple[str, str, int, int] | tuple[str, str, int, int, int | None]


def _make_table(
    title: str,
    columns: list[_Column],
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
    for col in columns:
        name, style, ratio, min_w = col[0], col[1], col[2], col[3]
        max_w: int | None = col[4] if len(col) == 5 else None
        kwargs: dict[str, Any] = dict(style=style, no_wrap=True, ratio=ratio, min_width=min_w)
        if max_w is not None:
            kwargs["max_width"] = max_w
        t.add_column(name, **kwargs)

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
                f"▶ {cell}" if i == 0 else cell
                for i, cell in enumerate(row)
            )
            t.add_row(*sel_row, style="bold reverse")
        else:
            t.add_row(*row)

    return t
