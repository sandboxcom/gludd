"""Linux audit framework (auditd) log connector.

Self-contained: no base-class / package / sibling imports. Wraps `ausearch`
(and `auditctl`) behind an injectable command-runner so tests never touch the
real binaries.

Contract:
- class attr KIND = 'logs'
- instance attr `name`
- __init__(config, runner=None) — runner(argv) -> (rc, stdout, stderr); the
  default uses subprocess.run with a LIST argv (never shell=True), time-bound.
- health() -> {'ok', 'detail'} — never raises.
- query(spec) -> list[dict] of normalized records.

All caller-supplied args (start time, message type) are validated to reject a
leading dash or shell metacharacters BEFORE the argv is built; a rejected arg
raises ValueError and the runner is never invoked.
"""

from __future__ import annotations

import csv
import io
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

# A runner takes an argv list and returns (returncode, stdout, stderr).
Runner = Callable[[list[str]], "tuple[int, str, str]"]

# Default time bound for the real subprocess runner (seconds).
_DEFAULT_TIMEOUT = 30.0

# Shell metacharacters / whitespace / control bytes disallowed in argv values.
# We always build a LIST argv (never a shell string); this is defense-in-depth.
_SHELL_METACHARS = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \\\"'")

# CSV columns most ausearch --format csv builds emit. Used to map a header-less
# row, and to know which keys carry the fields we normalize.
_CSV_FIELDS = (
    "node",
    "event_kind",
    "event_type",
    "event_time",
    "serial",
    "uid",
    "exe",
    "success",
    "summary",
)


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run argv as a LIST (shell=False), time-bound; return (rc, out, err)."""
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_DEFAULT_TIMEOUT,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _reject_unsafe(value: str, *, field: str) -> str:
    """Validate a caller-supplied argv value or raise ValueError.

    Rejects a leading dash (so a value can never be parsed as an option flag)
    and any shell metacharacter / whitespace / control byte.
    """
    if value == "":
        raise ValueError(f"{field} must not be empty")
    if value.startswith("-"):
        raise ValueError(f"{field} must not start with '-': {value!r}")
    bad = sorted(set(value) & _SHELL_METACHARS)
    if bad:
        raise ValueError(f"{field} contains disallowed characters {bad!r}: {value!r}")
    return value


class AuditdSource:
    """Read Linux audit events via `ausearch --format csv`."""

    KIND = "logs"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        runner: Runner | None = None,
    ) -> None:
        config = dict(config or {})
        self.name: str = str(config.get("name", "auditd"))
        self._config = config
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return {'ok': bool, 'detail': str}; never raises.

        Tries `auditctl -s` first; falls back to `ausearch --version`.
        """
        for argv in (["auditctl", "-s"], ["ausearch", "--version"]):
            try:
                rc, out, _err = self._runner(argv)
            except Exception:  # health must never raise; try next
                continue
            if rc == 0:
                first = (out or "").strip().splitlines()
                detail = first[0] if first else f"{argv[0]} ok"
                return {"ok": True, "detail": detail}
        return {"ok": False, "detail": "auditd tooling unavailable (auditctl/ausearch)"}

    # -- query ----------------------------------------------------------------

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run ausearch and return normalized audit records.

        spec keys (all optional): start (str), type (str message type).
        """
        spec = dict(spec or {})
        argv = self._build_argv(spec)
        rc, out, _err = self._runner(argv)
        if rc != 0:
            return []
        return [self._normalize(row) for row in self._parse_csv(out or "")]

    def _build_argv(self, spec: Mapping[str, Any]) -> list[str]:
        argv: list[str] = ["ausearch", "--format", "csv"]

        start = spec.get("start")
        if start is not None:
            argv += ["--start", _reject_unsafe(str(start), field="start")]

        msg_type = spec.get("type")
        if msg_type is not None:
            argv += ["-m", _reject_unsafe(str(msg_type), field="type")]

        return argv

    def _parse_csv(self, text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        reader = csv.reader(io.StringIO(text))
        for raw in reader:
            if not raw or all(c.strip() == "" for c in raw):
                continue
            # A header row (first cell is a known field name) is skipped; the
            # data rows are mapped positionally onto _CSV_FIELDS.
            first = raw[0].strip().lower()
            if first in {"node", "event_kind", "event kind"} and "summary" in (
                c.strip().lower() for c in raw
            ):
                continue
            row = {
                _CSV_FIELDS[i]: raw[i].strip()
                for i in range(min(len(raw), len(_CSV_FIELDS)))
            }
            rows.append(row)
        return rows

    def _normalize(self, row: Mapping[str, Any]) -> dict[str, Any]:
        ts = self._parse_time(row.get("event_time"))
        event_type = row.get("event_type") or row.get("event_kind")
        summary = row.get("summary")

        labels = {
            "type": event_type,
            "uid": row.get("uid"),
            "exe": row.get("exe"),
            "success": row.get("success"),
        }

        return {
            "ts": ts,
            "source": self.name,
            "kind": "logs",
            "level_or_status": event_type,
            "message": summary,
            "value": None,
            "labels": labels,
            "raw": dict(row),
        }

    @staticmethod
    def _parse_time(raw: Any) -> float | None:
        """ausearch event time may be an epoch (float/int) or a date string.

        We return a float epoch when the value is numeric, else None (the
        original string is preserved in `raw`).
        """
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
