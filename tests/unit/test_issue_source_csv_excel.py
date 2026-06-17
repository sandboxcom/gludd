"""Unit tests for the CSV/Excel issue source.

CSV uses the stdlib reader/writer. The XLSX path's openpyxl import is *guarded*;
the import-failure branch is exercised by monkeypatching the import to raise, so
the test does not require openpyxl to be installed.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from general_ludd.issue_sources.base import Transition
from general_ludd.issue_sources.csv_excel import CsvExcelSource

_CSV = """\
id,title,status,priority,assignee,labels
T-1,First item,open,High,alice,"bug,urgent"
T-2,Second item,in progress,Low,bob,
T-3,Third item,done,,,
"""


def _write_csv(tmp_path: Path, text: str = _CSV) -> Path:
    p = tmp_path / "issues.csv"
    p.write_text(text, encoding="utf-8")
    return p


def _make(path: Path, **cfg: Any) -> CsvExcelSource:
    return CsvExcelSource({"path": str(path), **cfg})


def test_source_name() -> None:
    assert CsvExcelSource.SOURCE == "csv_excel"


def test_requires_path() -> None:
    with pytest.raises(ValueError):
        CsvExcelSource({})


def test_fetch_csv_normalizes(tmp_path: Path) -> None:
    src = _make(_write_csv(tmp_path))
    records = src.fetch({})
    assert [r["external_id"] for r in records] == ["T-1", "T-2", "T-3"]
    first = records[0]
    assert first["title"] == "First item"
    assert first["status"] == "open"
    assert first["priority"] == "High"
    assert first["assignee"] == "alice"
    assert first["labels"] == ["bug", "urgent"]


def test_fetch_csv_empty_labels(tmp_path: Path) -> None:
    src = _make(_write_csv(tmp_path))
    second = src.fetch({})[1]
    assert second["labels"] == []
    assert second["assignee"] == "bob"


def test_fetch_csv_missing_id_falls_back_to_ordinal(tmp_path: Path) -> None:
    text = "title,status\nNo id row,open\n"
    src = _make(_write_csv(tmp_path, text))
    records = src.fetch({})
    assert records[0]["external_id"] == "row-1"


def test_fetch_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    assert _make(p).fetch({}) == []


def test_custom_column_mapping(tmp_path: Path) -> None:
    text = "Key,Summary,State\nK-9,Custom mapped,open\n"
    p = _write_csv(tmp_path, text)
    src = _make(p, columns={"Key": "external_id", "Summary": "title", "State": "status"})
    rec = src.fetch({})[0]
    assert rec["external_id"] == "K-9"
    assert rec["title"] == "Custom mapped"
    assert rec["status"] == "open"


def test_write_back_csv_done(tmp_path: Path) -> None:
    p = _write_csv(tmp_path)
    src = _make(p)
    assert src.write_back("T-1", Transition.DONE) is True
    after = next(r for r in src.fetch({}) if r["external_id"] == "T-1")
    assert after["status"] == "done"


def test_write_back_csv_claim(tmp_path: Path) -> None:
    p = _write_csv(tmp_path)
    src = _make(p)
    assert src.write_back("T-1", Transition.CLAIM) is True
    after = next(r for r in src.fetch({}) if r["external_id"] == "T-1")
    assert after["status"] == "in progress"


def test_write_back_csv_idempotent(tmp_path: Path) -> None:
    p = _write_csv(tmp_path)
    src = _make(p)
    # T-3 is already 'done'
    before = p.read_text()
    assert src.write_back("T-3", Transition.DONE) is True
    assert p.read_text() == before


def test_write_back_csv_unknown_id_false(tmp_path: Path) -> None:
    src = _make(_write_csv(tmp_path))
    assert src.write_back("T-999", Transition.DONE) is False


def test_write_back_csv_status_words_override(tmp_path: Path) -> None:
    p = _write_csv(tmp_path)
    src = _make(p, status_words={"done": "closed"})
    src.write_back("T-1", Transition.DONE)
    after = next(r for r in src.fetch({}) if r["external_id"] == "T-1")
    assert after["status"] == "closed"


def test_xlsx_guarded_import_raises_when_openpyxl_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "issues.xlsx"
    p.write_bytes(b"not really xlsx")  # never parsed: import fails first
    src = _make(p)

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openpyxl":
            raise ImportError("no openpyxl")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(RuntimeError, match="openpyxl"):
        src.fetch({})


def test_xlsx_path_uses_openpyxl_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The .xlsx read path delegates to openpyxl — verified with a fake module."""
    import sys
    import types

    rows = [
        ("id", "title", "status"),
        ("X-1", "Excel item", "open"),
    ]

    class _FakeWS:
        def iter_rows(self, values_only: bool = False) -> list[tuple[Any, ...]]:
            return rows

    class _FakeWB:
        active = _FakeWS()

        def __getitem__(self, _name: str) -> _FakeWS:
            return self.active

    fake_openpyxl = types.ModuleType("openpyxl")
    fake_openpyxl.load_workbook = lambda *a, **k: _FakeWB()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openpyxl", fake_openpyxl)

    p = tmp_path / "issues.xlsx"
    p.write_bytes(b"x")
    src = _make(p)
    records = src.fetch({})
    assert len(records) == 1
    assert records[0]["external_id"] == "X-1"
    assert records[0]["title"] == "Excel item"
