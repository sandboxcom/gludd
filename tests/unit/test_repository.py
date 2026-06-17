from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from general_ludd.db.repository import BenchmarkRepository, _count_by_fields, _scope


def test_benchmark_repository_equivalence():
    assert BenchmarkRepository is not None


# ---------------------------------------------------------------------------
# _scope helper
# ---------------------------------------------------------------------------

class _FakeModel:
    """Minimal stand-in for a SQLAlchemy mapped class."""

    project_id = MagicMock(name="_FakeModel.project_id")


def test_scope_noop_when_project_id_is_none():
    """_scope must return the stmt unmodified when project_id is None."""
    stmt = MagicMock()
    result = _scope(stmt, _FakeModel, None)
    assert result is stmt
    stmt.where.assert_not_called()


def test_scope_appends_where_when_project_id_given():
    """_scope must call stmt.where(model.project_id == project_id) and return it."""
    stmt = MagicMock()
    narrowed = MagicMock()
    stmt.where.return_value = narrowed

    result = _scope(stmt, _FakeModel, "proj-123")

    stmt.where.assert_called_once()
    assert result is narrowed


# ---------------------------------------------------------------------------
# _count_by_fields helper
# ---------------------------------------------------------------------------

def _rows(*specs: dict) -> list[SimpleNamespace]:
    return [SimpleNamespace(**s) for s in specs]


def test_count_by_fields_single_field():
    rows = _rows({"status": "a"}, {"status": "b"}, {"status": "a"})
    (by_status,) = _count_by_fields(rows, "status")
    assert by_status == {"a": 2, "b": 1}


def test_count_by_fields_multiple_fields():
    rows = _rows(
        {"status": "active", "queue": "q1", "work_type": "x"},
        {"status": "active", "queue": "q2", "work_type": "x"},
        {"status": "done",   "queue": "q1", "work_type": "y"},
    )
    by_status, by_queue, by_work_type = _count_by_fields(rows, "status", "queue", "work_type")
    assert by_status   == {"active": 2, "done": 1}
    assert by_queue    == {"q1": 2, "q2": 1}
    assert by_work_type == {"x": 2, "y": 1}


def test_count_by_fields_empty_rows():
    by_status, by_queue = _count_by_fields([], "status", "queue")
    assert by_status == {}
    assert by_queue  == {}


def test_count_by_fields_returns_tuple_length_matches_field_count():
    rows = _rows({"a": "v1", "b": "v2", "c": "v3"})
    result = _count_by_fields(rows, "a", "b", "c")
    assert len(result) == 3
