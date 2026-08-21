"""Regression tests for the shared iterable cursor adapter."""

from collections.abc import Iterator

from general_ludd.connectors.cursor_adapter import adapt_iterable_cursor
from general_ludd.connectors.registry import ConnectorRegistry


def test_cursor_adapter_is_not_operator_selectable_as_a_source() -> None:
    """The shared adapter must not enter the connector Source allowlist."""
    assert (
        "general_ludd.connectors.cursor_adapter"
        not in ConnectorRegistry.source_module_paths()
    )


def test_cursor_adapter_module_selector_fails_closed_before_import() -> None:
    """Operator config cannot select an infrastructure helper as a Source."""
    registry = ConnectorRegistry.from_config(
        [{"name": "adapter", "kind": "metrics", "module": "cursor_adapter"}]
    )

    assert registry.list_sources() == []
    errors = registry.errors()
    assert [error["name"] for error in errors] == ["adapter"]
    assert "not in the connector allowlist" in errors[0]["error"]


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
