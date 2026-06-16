"""osquery host connector.

Runs ``osqueryi --json "<query>"`` through an injected runner and normalizes
each returned row into a flat metric record. The connector never spawns a
shell: the command is always built as an argv list and handed to the runner
as such. ``shell=True`` is never used. The SQL query is validated for shell
metacharacters that could enable argv-injection or shell-chaining before it is
passed as a single argv element.

The connector is intentionally self-contained: it imports nothing from sibling
connector modules and defines its own runner protocol so it can be unit-tested
with a canned, injected runner (no real ``osqueryi`` binary required).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommandRunner(Protocol):
    """Injectable subprocess runner.

    Implementations receive an argv list (never a shell string) and return the
    process result. Tests inject a canned implementation; production wires a
    real subprocess runner. ``shell=True`` must never be honored.
    """

    def __call__(self, argv: Sequence[str]) -> RunResult: ...


class RunResult(Protocol):
    """Minimal result shape a runner must return."""

    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


# Characters that would let a value break out of a single argv element into a
# shell, chain another command, redirect, or expand. osquery SQL legitimately
# uses many punctuation chars (commas, parens, quotes, =, <, >, *) so we do NOT
# blanket-reject SQL punctuation; we reject only the metachars that are
# meaningless inside a SQL string but dangerous if a shell ever sees them.
_SHELL_METACHARS = re.compile(r"[;&|`$\\\n\r\x00]")
# A heuristic for "this looks like an attempt to chain a shell command after
# the SQL" — e.g. a trailing ``; rm -rf``.
_SHELL_CHAIN = re.compile(r";\s*\w")


class OsquerySource:
    """Collect host metrics by running osquery SQL via ``osqueryi --json``."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "osquery"))
        self._binary: str = str(self.config.get("binary", "osqueryi"))
        self._runner: CommandRunner | None = runner

    # -- validation -------------------------------------------------------

    @staticmethod
    def _validate_query(query: str) -> str:
        if not isinstance(query, str):  # defensive: spec may carry junk
            raise ValueError("osquery query must be a string")
        stripped = query.strip()
        if not stripped:
            raise ValueError("osquery query must not be empty")
        if stripped.startswith("-"):
            # A leading dash would be parsed by osqueryi as an option, not a
            # positional query — reject argv-injection of flags.
            raise ValueError("osquery query must not start with '-'")
        if _SHELL_METACHARS.search(stripped):
            raise ValueError("osquery query contains forbidden shell metacharacters")
        if _SHELL_CHAIN.search(stripped):
            raise ValueError("osquery query appears to chain a shell command")
        return stripped

    def _build_argv(self, query: str) -> list[str]:
        return [self._binary, "--json", query]

    # -- runner -----------------------------------------------------------

    def _run(self, argv: list[str]) -> RunResult:
        if self._runner is None:
            raise RuntimeError("no runner injected for OsquerySource")
        return self._runner(argv)

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``; never raises."""
        try:
            if self._runner is None and shutil.which(self._binary) is None:
                return {"ok": False, "detail": f"{self._binary} not found on PATH"}
            result = self._run(self._build_argv("SELECT 1"))
            rc = result.returncode
            if rc != 0:
                detail = (result.stderr or "").strip() or f"exit code {rc}"
                return {"ok": False, "detail": detail}
            return {"ok": True, "detail": "osqueryi responded"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ------------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the spec's SQL and return normalized metric records."""
        sql = self._validate_query(str(spec.get("query", "")))
        table = str(spec.get("table", "")) or self._infer_table(sql)
        argv = self._build_argv(sql)
        result = self._run(argv)
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"osqueryi failed: {detail}")
        rows = self._parse_rows(result.stdout)
        records: list[dict[str, Any]] = []
        for row in rows:
            records.extend(self._normalize_row(table, row, sql))
        return records

    @staticmethod
    def _infer_table(sql: str) -> str:
        match = re.search(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
        return match.group(1) if match else "osquery"

    @staticmethod
    def _parse_rows(stdout: str) -> list[dict[str, Any]]:
        text = (stdout or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"osqueryi returned non-JSON output: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError("osqueryi JSON output was not a list of rows")
        rows: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                rows.append(item)
        return rows

    def _normalize_row(
        self,
        table: str,
        row: dict[str, Any],
        sql: str,
    ) -> list[dict[str, Any]]:
        labels = {str(k): self._coerce_label(v) for k, v in row.items()}
        records: list[dict[str, Any]] = []
        for key, raw_value in row.items():
            value = self._coerce_numeric(raw_value)
            records.append(
                {
                    "ts": None,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "ok",
                    "message": f"{table}.{key}",
                    "value": value,
                    "labels": dict(labels),
                    "raw": {"table": table, "query": sql, "row": dict(row)},
                }
            )
        return records

    @staticmethod
    def _coerce_label(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _coerce_numeric(value: Any) -> float | int | str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text == "":
            return text
        try:
            if re.fullmatch(r"[+-]?\d+", text):
                return int(text)
            return float(text)
        except ValueError:
            return text
