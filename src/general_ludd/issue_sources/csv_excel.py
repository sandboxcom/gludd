"""CSV / Excel issue source.

Reads work items from a tabular file — a ``.csv`` (stdlib :mod:`csv`) or an
``.xlsx`` (``openpyxl``, guarded import). Each non-header row becomes one
:class:`~general_ludd.issue_sources.base.IssueRecord`.

Column mapping is config-driven via ``config['columns']`` (header-name ->
record-field), with sensible defaults (``id`` / ``title`` / ``status`` / ...).
The ``external_id`` comes from the mapped id column, falling back to the 1-based
row ordinal when absent.

This is a *file*-based source: no ``base_url``, no network transport.
``write_back`` rewrites the status cell in place for the matching row (CSV: full
rewrite; XLSX: openpyxl cell update). ``CLAIM`` writes ``"in progress"``,
``DONE`` writes ``"done"`` (overridable via ``config['status_words']``). Rewrites
are idempotent (writing the value already present is a no-op success).
"""

from __future__ import annotations

import csv
import os
from typing import Any

from general_ludd.issue_sources.base import (
    IssueRecord,
    IssueSource,
    SourceTransport,
    Transition,
    new_issue_record,
    parse_iso_ts,
)
from general_ludd.security.sanitize import confine_path_multi, workspace_roots

# Default header-name -> IssueRecord-field mapping.
_DEFAULT_COLUMNS: dict[str, str] = {
    "id": "external_id",
    "title": "title",
    "body": "body",
    "status": "status",
    "priority": "priority",
    "assignee": "assignee",
    "labels": "labels",
    "url": "url",
    "updated_at": "updated_at",
}

_DEFAULT_STATUS_WORDS: dict[Transition, str] = {
    Transition.CLAIM: "in progress",
    Transition.DONE: "done",
}


class CsvExcelSource(IssueSource):
    """Issue source backed by a CSV or XLSX tabular file."""

    SOURCE = "csv_excel"

    def __init__(self, config: dict[str, Any], transport: SourceTransport | None = None) -> None:
        """Configure the source from ``config['path']`` plus optional column mapping."""
        super().__init__(config, transport, require_base_url=False)
        path = config.get("path")
        if not path:
            raise ValueError("config['path'] is required for the csv/excel source")
        self.root = self._resolve_root(config)
        confined = confine_path_multi(str(path), workspace_roots(self.root))
        if confined is None:
            raise ValueError(f"refusing csv/excel path outside the allowed workspace jail: {path!r}")
        self.path: str = confined
        cols = config.get("columns")
        self.columns: dict[str, str] = dict(cols) if isinstance(cols, dict) else dict(_DEFAULT_COLUMNS)
        words = config.get("status_words") or {}
        self.status_words: dict[Transition, str] = dict(_DEFAULT_STATUS_WORDS)
        for t in (Transition.CLAIM, Transition.DONE):
            if t.value in words:
                self.status_words[t] = str(words[t.value])
        self.sheet: str | None = str(config["sheet"]) if config.get("sheet") else None

    # -- path jail --------------------------------------------------------- #

    @staticmethod
    def _resolve_root(config: dict[str, Any]) -> str | None:
        """Resolve the workspace-jail root: ``config['root']`` then env, else None.

        Prefers an explicit ``config['root']``, falling back to the
        ``GLUDD_WORKSPACE_ROOT`` environment variable. Whitespace-only values are
        treated as absent so the jail defaults to cwd + temp (via
        :func:`workspace_roots`).
        """
        candidate = config.get("root") or os.environ.get("GLUDD_WORKSPACE_ROOT")
        if candidate is None:
            return None
        stripped = str(candidate).strip()
        return stripped or None

    # -- format detection -------------------------------------------------- #

    def _is_xlsx(self) -> bool:
        return self.path.lower().endswith((".xlsx", ".xlsm"))

    # -- raw row loading --------------------------------------------------- #

    def _load_rows(self) -> tuple[list[str], list[list[str]]]:
        """Return (header, rows) as lists of strings."""
        if self._is_xlsx():
            return self._load_xlsx_rows()
        return self._load_csv_rows()

    def _load_csv_rows(self) -> tuple[list[str], list[list[str]]]:
        with open(self.path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            all_rows = [list(r) for r in reader]
        if not all_rows:
            return [], []
        return all_rows[0], all_rows[1:]

    def _load_xlsx_rows(self) -> tuple[list[str], list[list[str]]]:
        try:
            import importlib

            openpyxl = importlib.import_module("openpyxl")  # openpyxl: optional, only for .xlsx parsing
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError(
                "reading .xlsx requires the optional 'openpyxl' dependency; install it or convert the file to .csv"
            ) from exc
        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        ws = wb[self.sheet] if self.sheet else wb.active
        rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c) for c in row])
        if not rows:
            return [], []
        return rows[0], rows[1:]

    # -- read -------------------------------------------------------------- #

    def _row_to_record(self, header: list[str], row: list[str], ordinal: int) -> IssueRecord:
        # field-name -> cell value, via the configured header mapping
        by_field: dict[str, str] = {}
        for col_index, col_name in enumerate(header):
            field = self.columns.get(col_name)
            if field is None:
                continue
            value = row[col_index] if col_index < len(row) else ""
            by_field[field] = value
        labels_raw = by_field.get("labels", "")
        labels = [x.strip() for x in labels_raw.split(",") if x.strip()] if labels_raw else []
        external_id = by_field.get("external_id", "").strip() or f"row-{ordinal}"
        return new_issue_record(
            external_id=external_id,
            title=by_field.get("title", ""),
            body=by_field.get("body", ""),
            status=by_field.get("status", ""),
            priority=by_field.get("priority") or None,
            assignee=by_field.get("assignee") or None,
            labels=labels,
            url=by_field.get("url") or f"file://{self.path}#row{ordinal}",
            updated_at=parse_iso_ts(by_field.get("updated_at")),
            raw={"row": row, "header": header, "ordinal": ordinal},
        )

    def fetch(self, spec: dict[str, Any] | None = None) -> list[IssueRecord]:
        """Read every non-header row and convert it to an IssueRecord."""
        header, rows = self._load_rows()
        if not header:
            return []
        records: list[IssueRecord] = []
        for i, row in enumerate(rows, start=1):
            records.append(self._row_to_record(header, row, i))
        return records

    # -- write-back -------------------------------------------------------- #

    def _status_header(self, header: list[str]) -> str | None:
        for col_name in header:
            if self.columns.get(col_name) == "status":
                return col_name
        return None

    def _id_header(self, header: list[str]) -> str | None:
        for col_name in header:
            if self.columns.get(col_name) == "external_id":
                return col_name
        return None

    def write_back(self, external_id: str, transition: Transition) -> bool:
        """Rewrite the status cell for the matching row; idempotent no-op success."""
        word = self.status_words[transition]
        if self._is_xlsx():
            return self._write_back_xlsx(external_id, word)
        return self._write_back_csv(external_id, word)

    def _write_back_csv(self, external_id: str, word: str) -> bool:
        with open(self.path, newline="", encoding="utf-8") as fh:
            all_rows = [list(r) for r in csv.reader(fh)]
        if not all_rows:
            return False
        header = all_rows[0]
        status_col = self._status_header(header)
        id_col = self._id_header(header)
        if status_col is None:
            return False
        status_index = header.index(status_col)
        id_index = header.index(id_col) if id_col else None
        changed = False
        for ordinal, row in enumerate(all_rows[1:], start=1):
            row_id = (row[id_index].strip() if id_index is not None and id_index < len(row) else "") or f"row-{ordinal}"
            if row_id != external_id:
                continue
            while len(row) <= status_index:
                row.append("")
            if row[status_index] == word:
                return True  # idempotent no-op
            row[status_index] = word
            changed = True
            break
        if not changed:
            return False
        with open(self.path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(all_rows)
        return True

    def _write_back_xlsx(self, external_id: str, word: str) -> bool:
        try:
            import importlib

            openpyxl = importlib.import_module("openpyxl")
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError("writing .xlsx requires the optional 'openpyxl' dependency") from exc
        if not os.path.exists(self.path):
            return False
        wb = openpyxl.load_workbook(self.path)
        ws = wb[self.sheet] if self.sheet else wb.active
        rows = list(ws.iter_rows())
        if not rows:
            return False
        header = [("" if c.value is None else str(c.value)) for c in rows[0]]
        status_col = self._status_header(header)
        id_col = self._id_header(header)
        if status_col is None:
            return False
        status_index = header.index(status_col)
        id_index = header.index(id_col) if id_col else None
        for ordinal, row in enumerate(rows[1:], start=1):
            row_id = ""
            if id_index is not None and id_index < len(row):
                cell_val = row[id_index].value
                row_id = "" if cell_val is None else str(cell_val).strip()
            row_id = row_id or f"row-{ordinal}"
            if row_id != external_id:
                continue
            cell = row[status_index]
            if ("" if cell.value is None else str(cell.value)) == word:
                return True  # idempotent no-op
            cell.value = word
            wb.save(self.path)
            return True
        return False
