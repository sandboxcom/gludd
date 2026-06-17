"""Unit tests for ExcelCsvSource — real temp files, no network.

Covers the issue-source contract for .csv (stdlib csv) and .xlsx (openpyxl):
- parse -> normalize rows with configurable column mapping
- update_status writes the status cell back; a re-read reflects it
- add_comment
- health() never raises (and reports 'openpyxl unavailable' for xlsx w/o openpyxl)
- path outside the configured root is REFUSED (ValueError)
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from general_ludd.issue_sources.excel_csv import ExcelCsvSource

try:  # pragma: no cover - availability probe for skips
    import openpyxl  # noqa: F401

    HAVE_OPENPYXL = True
except ImportError:  # pragma: no cover
    HAVE_OPENPYXL = False


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "root"
    base.mkdir()
    return base


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _csv_src(root: Path, rel: str, **mapping: str) -> ExcelCsvSource:
    cfg = {"path": rel, "root": str(root)}
    cfg.update(mapping)
    return ExcelCsvSource(cfg)


def test_system_and_name_attrs(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(p, [{"id": "1", "title": "a", "status": "open"}], ["id", "title", "status"])
    src = _csv_src(root, "issues.csv")
    assert src.SYSTEM == "excel"
    assert isinstance(src.name, str)
    assert src.name


def test_csv_parse_normalizes_rows(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(
        p,
        [
            {"id": "T-1", "title": "first", "status": "open", "assignee": "amy", "priority": "high"},
            {"id": "T-2", "title": "second", "status": "done", "assignee": "bob", "priority": "low"},
        ],
        ["id", "title", "status", "assignee", "priority"],
    )
    src = _csv_src(
        root,
        "issues.csv",
        id_col="id",
        title_col="title",
        status_col="status",
        assignee_col="assignee",
        priority_col="priority",
    )
    issues = src.fetch_issues({})
    assert len(issues) == 2
    by_id = {i["external_id"]: i for i in issues}
    assert by_id["T-1"]["title"] == "first"
    assert by_id["T-1"]["status"] == "open"
    assert by_id["T-1"]["assignee"] == "amy"
    assert by_id["T-1"]["priority"] == "high"
    assert by_id["T-2"]["status"] == "done"
    sample = issues[0]
    for key in (
        "external_id",
        "source",
        "title",
        "description",
        "status",
        "assignee",
        "labels",
        "priority",
        "url",
        "updated_ts",
        "raw",
    ):
        assert key in sample
    assert sample["source"] == "excel"
    assert isinstance(sample["labels"], list)
    assert str(root) in sample["url"]


def test_csv_default_column_names(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(p, [{"id": "X", "title": "t", "status": "open"}], ["id", "title", "status"])
    src = _csv_src(root, "issues.csv")  # rely on default mapping
    (issue,) = src.fetch_issues({})
    assert issue["external_id"] == "X"
    assert issue["title"] == "t"
    assert issue["status"] == "open"


def test_csv_update_status_writes_back(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(
        p,
        [
            {"id": "A", "title": "alpha", "status": "open"},
            {"id": "B", "title": "beta", "status": "open"},
        ],
        ["id", "title", "status"],
    )
    src = _csv_src(root, "issues.csv")
    result = src.update_status("A", "done")
    assert result["external_id"] == "A"
    assert result["status"] == "done"

    reread = {i["external_id"]: i for i in _csv_src(root, "issues.csv").fetch_issues({})}
    assert reread["A"]["status"] == "done"
    assert reread["B"]["status"] == "open"


def test_csv_update_status_unknown_id_raises(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(p, [{"id": "A", "title": "alpha", "status": "open"}], ["id", "title", "status"])
    src = _csv_src(root, "issues.csv")
    with pytest.raises(KeyError):
        src.update_status("ZZZ", "done")


def test_csv_add_comment(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(p, [{"id": "A", "title": "alpha", "status": "open"}], ["id", "title", "status"])
    src = _csv_src(root, "issues.csv")
    result = src.add_comment("A", "a remark")
    assert result["external_id"] == "A"


def test_health_ok_for_readable_csv(root: Path) -> None:
    p = root / "issues.csv"
    _write_csv(p, [{"id": "A", "title": "t", "status": "open"}], ["id", "title", "status"])
    src = _csv_src(root, "issues.csv")
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_never_raises_for_missing_file(root: Path) -> None:
    src = _csv_src(root, "absent.csv")
    h = src.health()
    assert h["ok"] is False
    assert isinstance(h["detail"], str)


def test_path_escape_with_dotdot_refused(root: Path) -> None:
    with pytest.raises(ValueError):
        ExcelCsvSource({"path": "../escape.csv", "root": str(root)})


def test_absolute_path_outside_root_refused(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.csv"
    _write_csv(outside, [{"id": "A", "title": "t", "status": "open"}], ["id", "title", "status"])
    with pytest.raises(ValueError):
        ExcelCsvSource({"path": str(outside), "root": str(root)})


@pytest.mark.skipif(not HAVE_OPENPYXL, reason="openpyxl not installed")
def test_xlsx_parse_and_update(root: Path) -> None:
    import openpyxl

    p = root / "issues.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "title", "status"])
    ws.append(["A", "alpha", "open"])
    ws.append(["B", "beta", "done"])
    wb.save(p)

    src = _csv_src(root, "issues.xlsx")
    issues = src.fetch_issues({})
    by_id = {i["external_id"]: i for i in issues}
    assert by_id["A"]["status"] == "open"
    assert by_id["B"]["status"] == "done"

    src.update_status("A", "done")
    reread = {i["external_id"]: i for i in _csv_src(root, "issues.xlsx").fetch_issues({})}
    assert reread["A"]["status"] == "done"
    assert reread["B"]["status"] == "done"


def test_health_reports_openpyxl_unavailable_for_xlsx(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate openpyxl being unavailable regardless of the real environment.
    import general_ludd.issue_sources.excel_csv as mod

    monkeypatch.setattr(mod, "openpyxl", None)
    p = root / "issues.xlsx"
    p.write_bytes(b"\x00")  # presence only; not parsed when openpyxl is None
    src = _csv_src(root, "issues.xlsx")
    h = src.health()
    assert h["ok"] is False
    assert "openpyxl" in h["detail"].lower()
