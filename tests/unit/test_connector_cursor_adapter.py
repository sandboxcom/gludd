"""Regression tests for the shared iterable cursor adapter."""

from collections.abc import Iterator

from general_ludd.connectors.cursor_adapter import adapt_iterable_cursor


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._rows: list[dict[str, object]] = [{"metric": "requests", "value": 3}]

    def execute(self, statement: str) -> None:
        self.calls.append(statement)

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self._rows)


def test_adapter_executes_statement_and_snapshots_rows() -> None:
    cursor = _Cursor()
    execute = adapt_iterable_cursor(cursor)

    rows = execute("SELECT metric, value")
    cursor._rows.clear()

    assert cursor.calls == ["SELECT metric, value"]
    assert rows == [{"metric": "requests", "value": 3}]


def test_adapter_propagates_cursor_execution_errors() -> None:
    class FailingCursor(_Cursor):
        def execute(self, statement: str) -> None:
            raise RuntimeError(f"rejected: {statement}")

    execute = adapt_iterable_cursor(FailingCursor())

    try:
        execute("unsafe query")
    except RuntimeError as exc:
        assert str(exc) == "rejected: unsafe query"
    else:
        raise AssertionError("cursor failure must propagate")
