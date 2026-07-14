"""systemd-journal (journald) log connector.

Self-contained: no base-class / package / sibling imports. Wraps `journalctl`
behind an injectable command-runner so tests never touch the real binary.

Contract:
- class attr KIND = 'logs'
- instance attr `name`
- __init__(config, runner=None) — runner(argv) -> (rc, stdout, stderr); the
  default uses subprocess.run with a LIST argv (never shell=True), time-bound.
- health() -> {'ok', 'detail'} — never raises.
- query(spec) -> list[dict] of normalized records.

All caller-supplied args (unit, since, priority) are validated to reject a
leading dash or shell metacharacters BEFORE the argv is built; a rejected arg
raises ValueError and the runner is never invoked.

"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from typing import TypedDict, cast

# A runner takes an argv list and returns (returncode, stdout, stderr).
Runner = Callable[[list[str]], "tuple[int, str, str]"]

# Default time bound for the real subprocess runner (seconds).
_DEFAULT_TIMEOUT = 30.0

# Characters that must never appear in a caller-supplied argv value: classic
# shell metacharacters plus whitespace and control bytes. We build a LIST argv
# (never a shell string), so this is defense-in-depth, not the only guard.
_SHELL_METACHARS = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \\\"'")

# syslog priority number -> name (journald PRIORITY field, 0..7).
_PRIORITY_NAMES: dict[int, str] = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


# Caller-supplied __init__ config. Only `name` is read; total=False so any
# subset (including `{}`) is valid.
class JournaldConfig(TypedDict, total=False):
    name: str


# Caller-supplied query spec. All keys optional; lines/priority accept str or
# int because the body coerces via int().
class JournaldQuerySpec(TypedDict, total=False):
    lines: int | str
    unit: str
    since: str
    priority: int | str


# health() return shape: {'ok': bool, 'detail': str}.
class JournaldHealthResult(TypedDict, total=False):
    ok: bool
    detail: str


# One parsed `journalctl -o json` line. Values are heterogeneous (str, int, or
# list[int] byte-array for MESSAGE) so each field is typed `object`; consumers
# narrow via isinstance or cast at the call site.
class JournaldEntry(TypedDict, total=False):
    __REALTIME_TIMESTAMP: object
    MESSAGE: object
    PRIORITY: object
    _SYSTEMD_UNIT: object
    _HOSTNAME: object
    SYSLOG_IDENTIFIER: object
    _PID: object


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


class JournaldSource:
    """Read systemd-journal entries via `journalctl -o json`."""

    KIND = "logs"

    def __init__(
        self,
        config: JournaldConfig | None = None,
        runner: Runner | None = None,
    ) -> None:
        cfg: dict[str, object] = dict(config or {})
        self.name: str = str(cfg.get("name", "journald"))
        self._config = cfg
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> JournaldHealthResult:
        """Return {'ok': bool, 'detail': str}; never raises."""
        try:
            rc, out, err = self._runner(["journalctl", "--version"])
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"journalctl unavailable: {exc}"}
        if rc == 0:
            first = (out or "").strip().splitlines()
            detail = first[0] if first else "journalctl ok"
            return {"ok": True, "detail": detail}
        return {"ok": False, "detail": (err or out or f"rc={rc}").strip()}

    # -- query ----------------------------------------------------------------

    def query(self, spec: JournaldQuerySpec | None = None) -> list[dict[str, object]]:
        """Run journalctl and return normalized log records.

        spec keys (all optional): lines (int, default 100), unit (str),
        since (str), priority (int 0..7).
        """
        spec_dict: dict[str, object] = dict(spec or {})
        argv = self._build_argv(spec_dict)
        rc, out, _err = self._runner(argv)
        if rc != 0:
            return []
        records: list[dict[str, object]] = []
        for line in (out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(entry, dict):
                records.append(self._normalize(cast("JournaldEntry", entry)))
        return records

    def _build_argv(self, spec: Mapping[str, object]) -> list[str]:
        argv: list[str] = ["journalctl", "-o", "json", "--no-pager"]

        lines = spec.get("lines", 100)
        try:
            n = int(cast("int | float | str | bool", lines))
        except (ValueError, TypeError):
            n = 100
        if n < 1:
            n = 1
        argv += ["-n", str(n)]

        unit = spec.get("unit")
        if unit is not None:
            argv += ["-u", _reject_unsafe(str(unit), field="unit")]

        since = spec.get("since")
        if since is not None:
            argv += ["--since", _reject_unsafe(str(since), field="since")]

        priority = spec.get("priority")
        if priority is not None:
            try:
                p = int(cast("int | float | str | bool", priority))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"priority must be an int 0..7: {priority!r}") from exc
            if not 0 <= p <= 7:
                raise ValueError(f"priority must be in 0..7, got {p}")
            argv += ["-p", str(p)]

        return argv

    def _normalize(self, entry: JournaldEntry) -> dict[str, object]:
        raw_ts = entry.get("__REALTIME_TIMESTAMP")
        ts = self._parse_realtime(cast(int | str | None, raw_ts))

        prio_raw = entry.get("PRIORITY")
        level = self._priority_name(cast(int | str | None, prio_raw))

        message = entry.get("MESSAGE")
        if isinstance(message, list):  # journald may emit a byte-array MESSAGE
            try:
                message = bytes(message).decode("utf-8", "replace")
            except (ValueError, TypeError):
                message = str(message)
        elif message is not None:
            message = str(message)

        labels: dict[str, object] = {
            "_SYSTEMD_UNIT": entry.get("_SYSTEMD_UNIT"),
            "_HOSTNAME": entry.get("_HOSTNAME"),
            "SYSLOG_IDENTIFIER": entry.get("SYSLOG_IDENTIFIER"),
            "_PID": entry.get("_PID"),
        }

        return {
            "ts": ts,
            "source": self.name,
            "kind": "logs",
            "level_or_status": level,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": dict(entry),
        }

    @staticmethod
    def _parse_realtime(raw: int | str | None) -> float | None:
        """__REALTIME_TIMESTAMP is microseconds-since-epoch -> seconds float."""
        if raw is None:
            return None
        try:
            return int(raw) / 1_000_000.0
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _priority_name(raw: int | str | None) -> str | None:
        if raw is None:
            return None
        try:
            return _PRIORITY_NAMES.get(int(raw), str(raw))
        except (ValueError, TypeError):
            return str(raw)
