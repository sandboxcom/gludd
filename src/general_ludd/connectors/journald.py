"""journald log connector.

A self-contained :class:`JournaldSource` that reads the systemd journal by
shelling out to ``journalctl -o json --no-pager`` through an *injected* runner
(so it is fully testable with canned output and never touches a real journal in
tests). It normalizes each journal entry into the shared record shape
(``ts``/``source``/``kind``/``level_or_status``/``message``/``value``/``labels``/``raw``).

Design notes
------------
- **No shell.** The runner is invoked with an argv *list* (``shell=False``);
  nothing is ever interpolated into a shell string.
- **Filter validation.** Operator-supplied ``unit`` / ``priority`` / ``since``
  filters are validated before they become argv. Any value with a leading dash
  (argv-injection of another flag) or a shell metacharacter is rejected, and on
  rejection the runner is *not* called at all (fail-closed).
- **health() never raises.** Every failure path is converted into
  ``{"ok": False, "detail": ...}``.

This module is intentionally dependency-free (stdlib only) and does not import
any sibling connector module.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

KIND = "logs"

# journald PRIORITY (syslog severity) -> human level name.
_PRIORITY_TO_LEVEL: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}

# Characters that have meaning to a shell or could break out of an argv token.
# Even though we never use a shell, rejecting these keeps filters to the safe
# alphabet journalctl actually expects (unit names, priorities, timestamps).
_METACHARACTERS = set(";|&$`<>()!\\\"'*?[]{}\n\r\t")

# A journalctl priority filter is a single 0..7 digit or a name.
_VALID_PRIORITY_NAMES = frozenset(
    {"emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"}
)

# A conservative allow-list pattern for a --since value (date/time or relative
# expressions journalctl accepts, e.g. "2024-01-01 10:00:00", "-1h", "yesterday").
_SINCE_RE = re.compile(r"^[A-Za-z0-9 :+\-]+$")


class CommandRunner(Protocol):
    """Structural contract for the injected command runner.

    The runner receives an argv *list* and returns the captured stdout text. It
    must never be given a shell string. Tests inject a fake that returns canned
    ``journalctl -o json`` output (and records the argv it was handed).
    """

    def __call__(self, argv: list[str]) -> str:
        ...


class JournaldSource:
    """Read + normalize systemd-journal entries via ``journalctl -o json``."""

    KIND: str = KIND

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        config = config or {}
        self.name: str = str(config.get("name", "journald"))
        self._config = config
        self._journalctl = str(config.get("journalctl_path", "journalctl"))
        # Default read cap so a query is always time/size-bound.
        self._default_lines = int(config.get("lines", 1000))
        self._runner: CommandRunner = runner if runner is not None else _default_runner

    # -- validation -------------------------------------------------------- #
    @staticmethod
    def _reject(value: str) -> bool:
        """Return True if ``value`` is unsafe to place in argv."""
        if not value:
            return True
        if value[0] == "-":  # would be parsed as another flag
            return True
        return any(ch in _METACHARACTERS for ch in value)

    def _validate_unit(self, unit: str) -> str:
        if self._reject(unit):
            raise ValueError(f"unsafe unit filter: {unit!r}")
        return unit

    def _validate_priority(self, priority: str) -> str:
        if self._reject(priority):
            raise ValueError(f"unsafe priority filter: {priority!r}")
        if not (priority.isdigit() and 0 <= int(priority) <= 7) and priority not in _VALID_PRIORITY_NAMES:
            raise ValueError(f"invalid priority filter: {priority!r}")
        return priority

    def _validate_since(self, since: str) -> str:
        if self._reject(since) or not _SINCE_RE.match(since):
            raise ValueError(f"unsafe since filter: {since!r}")
        return since

    # -- argv construction ------------------------------------------------- #
    def _build_argv(self, spec: dict[str, Any]) -> list[str]:
        argv: list[str] = [self._journalctl, "-o", "json", "--no-pager"]

        unit = spec.get("unit")
        if unit is not None:
            argv.append(f"--unit={self._validate_unit(str(unit))}")

        priority = spec.get("priority")
        if priority is not None:
            argv.append(f"--priority={self._validate_priority(str(priority))}")

        since = spec.get("since")
        if since is not None:
            argv.append(f"--since={self._validate_since(str(since))}")

        lines = spec.get("lines", self._default_lines)
        argv.extend(["-n", str(int(lines))])
        return argv

    # -- normalization ----------------------------------------------------- #
    @staticmethod
    def _parse_ts(entry: dict[str, Any]) -> float | None:
        # journald emits __REALTIME_TIMESTAMP as microseconds since the epoch.
        raw_ts = entry.get("__REALTIME_TIMESTAMP")
        if raw_ts is None:
            return None
        try:
            return int(raw_ts) / 1_000_000.0
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _message(entry: dict[str, Any]) -> str:
        msg = entry.get("MESSAGE", "")
        if isinstance(msg, list):  # journald can emit binary MESSAGE as a byte list
            try:
                return bytes(msg).decode("utf-8", "replace")
            except (TypeError, ValueError):
                return str(msg)
        return str(msg)

    def _normalize(self, entry: dict[str, Any]) -> dict[str, Any]:
        priority = entry.get("PRIORITY")
        try:
            level = _PRIORITY_TO_LEVEL.get(int(priority), "info") if priority is not None else "info"
        except (TypeError, ValueError):
            level = "info"

        labels: dict[str, Any] = {
            "unit": entry.get("_SYSTEMD_UNIT") or entry.get("UNIT"),
            "syslog_identifier": entry.get("SYSLOG_IDENTIFIER"),
            "hostname": entry.get("_HOSTNAME"),
            "pid": entry.get("_PID"),
        }

        return {
            "ts": self._parse_ts(entry),
            "source": self.name,
            "kind": KIND,
            "level_or_status": level,
            "message": self._message(entry),
            "value": None,
            "labels": labels,
            "raw": entry,
        }

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a validated ``journalctl`` query and return normalized records.

        Raises ``ValueError`` if a filter is unsafe — *before* the runner is
        invoked, so an injection attempt never reaches the command line.
        """
        spec = spec or {}
        argv = self._build_argv(spec)  # validates filters; may raise ValueError
        out = self._runner(argv)
        records: list[dict[str, Any]] = []
        for raw_line in out.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                records.append(self._normalize(entry))
        return records

    def health(self) -> dict[str, Any]:
        """Probe journald reachability. MUST NOT raise."""
        try:
            argv = [self._journalctl, "-o", "json", "--no-pager", "-n", "1"]
            out = self._runner(argv)
            ok = out is not None
            return {"ok": ok, "detail": "journalctl reachable" if ok else "no output"}
        except Exception as exc:  # never raise out of health()
            return {"ok": False, "detail": f"journalctl unavailable: {exc}"}


def _default_runner(argv: list[str]) -> str:
    """Real runner: execute argv with no shell and return stdout.

    Imported lazily so the module has zero runtime dependencies until an actual
    journald read is performed (tests always inject their own runner).
    """
    import subprocess  # argv list, shell=False, validated inputs

    proc = subprocess.run(  # argv list (never a shell string)
        argv,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.stdout
