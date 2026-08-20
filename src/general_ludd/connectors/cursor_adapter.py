"""Typed adapter for connectors backed by iterable database cursors."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Protocol, TypeVar

RowT_co = TypeVar("RowT_co", covariant=True)


class IterableCursor(Protocol[RowT_co]):
    """Describe the minimal cursor contract used by metric connectors."""

    def execute(self, statement: str) -> object:
        """Execute a connector-owned read-only statement."""
        ...

    def __iter__(self) -> Iterator[RowT_co]:
        """Iterate over rows from the most recently executed statement."""
        ...


def adapt_iterable_cursor(
    cursor: IterableCursor[RowT_co],
) -> Callable[[str], Sequence[RowT_co]]:
    """Adapt an iterable cursor to the connectors' injected executor contract."""

    def execute(statement: str) -> Sequence[RowT_co]:
        cursor.execute(statement)
        return list(cursor)

    return execute
