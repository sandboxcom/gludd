"""Self-contained Windows Event Log connector.

Reads events from a Windows event channel via a *command runner* and normalizes
each event into a flat record. The runner is **injectable** so the connector can
be driven entirely from canned output in tests — no real Windows, no real
subprocess, no shell.

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* The module imports and runs fine on **non-Windows** hosts: it only execs a
  runner. Tests inject canned ``Get-WinEvent`` / ``wevtutil`` output; the real
  default runner shells out to ``subprocess.run`` with a **list** argv and
  **never** ``shell=True``, time-bound.
* ``health()`` never raises. ``query()`` never raises: transport / parse
  failures are surfaced as a single normalized error record.
* Caller-supplied arguments (channel, level, count, provider, since) are
  validated *before* they ever reach an argv: leading-dash and shell
  metacharacters are rejected; the channel is matched against a safe charset.

Two collectors are supported, selected by ``config["backend"]``:

* ``"powershell"`` (default) builds::

      powershell -NoProfile -NonInteractive -Command
        Get-WinEvent -FilterHashtable @{LogName='<channel>'; Level=<n>}
          -MaxEvents <N> | ConvertTo-Json -Depth 5

* ``"wevtutil"`` builds::

      wevtutil qe <channel> /f:json /c:<N> /rd:true

Record shape (one dict per event)::

    {
        "ts": float | None,          # parsed TimeCreated (unix seconds)
        "source": str,               # connector name
        "kind": "logs",
        "level_or_status": str,      # LevelDisplayName: Error/Warning/Information/...
        "message": str,              # event Message
        "value": float | None,       # numeric Id when available, else None
        "labels": dict[str, str],    # ProviderName, Id, Channel, MachineName
        "raw": Any,                  # original event payload
    }
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

# Injectable runner signature: (argv) -> (returncode, stdout, stderr)
Runner = Callable[[Sequence[str]], "tuple[int, str, str]"]

KIND = "logs"

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_CHANNEL = "System"
_DEFAULT_COUNT = 100
_MAX_COUNT = 10000

# A Windows event channel / log name. Real names look like ``System``,
# ``Application``, ``Security``, ``Microsoft-Windows-PowerShell/Operational``.
# Allow word chars, dash, slash, dot, space — and nothing else.
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,254}$")

# A provider name (ProviderName filter). Same conservative charset, plus it may
# not start with a dash (so it can never be read as an option).
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,254}$")

# Characters that must never appear in any caller-supplied argument: shell
# metacharacters and quoting that could break out of the PowerShell hashtable
# or be re-interpreted by a runner.
_SHELL_METACHARS = set(";&|`$<>(){}[]'\"\\\n\r\t*?!^~%=")

# Map of accepted level names -> Windows numeric level (for PowerShell filter).
_LEVEL_NAME_TO_NUM = {
    "critical": 1,
    "error": 2,
    "warning": 3,
    "information": 4,
    "informational": 4,
    "verbose": 5,
}
# Map of numeric level -> canonical display name (for normalization fallback).
_LEVEL_NUM_TO_NAME = {
    1: "Critical",
    2: "Error",
    3: "Warning",
    4: "Information",
    5: "Verbose",
}


def _default_runner(argv: Sequence[str]) -> tuple[int, str, str]:
    """Real runner: a time-bound, list-argv subprocess. Never ``shell=True``.

    Importing this module does not run anything; this is only invoked when no
    runner is injected *and* ``query()``/``health()`` is actually called.
    """
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _reject_metachars(value: str, field: str) -> None:
    if value and value[0] == "-":
        raise ValueError(f"{field} may not start with '-': {value!r}")
    bad = _SHELL_METACHARS.intersection(value)
    if bad:
        raise ValueError(f"{field} contains forbidden characters {sorted(bad)!r}: {value!r}")


def _validate_channel(channel: Any) -> str:
    if not isinstance(channel, str) or not channel:
        raise ValueError("channel must be a non-empty string")
    _reject_metachars(channel, "channel")
    if not _CHANNEL_RE.match(channel):
        raise ValueError(f"channel is not a safe event-log name: {channel!r}")
    return channel


def _validate_provider(provider: Any) -> str:
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider must be a non-empty string")
    _reject_metachars(provider, "provider")
    if not _PROVIDER_RE.match(provider):
        raise ValueError(f"provider is not a safe provider name: {provider!r}")
    return provider


def _validate_count(count: Any) -> int:
    if isinstance(count, bool):  # bool is an int subclass; reject explicitly
        raise ValueError(f"count must be an integer, not a bool: {count!r}")
    try:
        n = int(count)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"count must be an integer: {count!r}") from exc
    if n < 1:
        raise ValueError(f"count must be >= 1: {n}")
    if n > _MAX_COUNT:
        raise ValueError(f"count must be <= {_MAX_COUNT}: {n}")
    return n


def _validate_level(level: Any) -> int:
    """Return a numeric Windows level for the given level (name or number)."""
    if isinstance(level, bool):  # bool is an int subclass; reject explicitly
        raise ValueError(f"level must be a name or 1-5, not a bool: {level!r}")
    if isinstance(level, int):
        if level not in _LEVEL_NUM_TO_NAME:
            raise ValueError(f"numeric level out of range (1-5): {level!r}")
        return level
    if isinstance(level, str):
        key = level.strip().lower()
        if key.isdigit():
            return _validate_level(int(key))
        if key not in _LEVEL_NAME_TO_NUM:
            raise ValueError(f"unknown level name: {level!r}")
        return _LEVEL_NAME_TO_NUM[key]
    raise ValueError(f"level must be a name or 1-5: {level!r}")


def _validate_since(since: Any) -> str:
    """Validate an ISO-8601-ish 'since' timestamp string (no metachars)."""
    if not isinstance(since, str) or not since:
        raise ValueError("since must be a non-empty string")
    _reject_metachars(since, "since")
    # Be permissive about format but ensure it parses as a datetime so we never
    # forward arbitrary text into an argv.
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"since is not an ISO-8601 timestamp: {since!r}") from exc
    return since


_WINDOWS_TS_RE = re.compile(r"/Date\((\d+)(?:[+-]\d+)?\)/")


def _parse_ts(raw_ts: Any) -> float | None:
    """Parse a TimeCreated value into unix seconds. Returns None on failure.

    Handles three shapes seen in the wild:
    * ConvertTo-Json's ``"/Date(1718000000000)/"`` (milliseconds)
    * an ISO-8601 string (``2026-06-12T10:00:00`` / with offset / ``Z``)
    * an int/float already in unix seconds
    """
    if raw_ts is None:
        return None
    if isinstance(raw_ts, bool):
        return None
    if isinstance(raw_ts, (int, float)):
        return float(raw_ts)
    if isinstance(raw_ts, str):
        m = _WINDOWS_TS_RE.search(raw_ts)
        if m:
            return int(m.group(1)) / 1000.0
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.timestamp()
    return None


def _level_display(event: dict[str, Any]) -> str:
    """Pick a human level: LevelDisplayName, else map numeric Level."""
    name = event.get("LevelDisplayName")
    if isinstance(name, str) and name.strip():
        return name
    lvl = event.get("Level")
    if isinstance(lvl, bool):
        return ""
    if isinstance(lvl, int):
        return _LEVEL_NUM_TO_NAME.get(lvl, "")
    if isinstance(lvl, str) and lvl.isdigit():
        return _LEVEL_NUM_TO_NAME.get(int(lvl), "")
    return ""


class WindowsEventLogSource:
    """A ``logs`` source backed by Get-WinEvent or wevtutil via an injected runner."""

    KIND = KIND

    def __init__(self, config: dict[str, Any], runner: Runner | None = None) -> None:
        config = config or {}
        backend = str(config.get("backend", "powershell")).strip().lower()
        if backend not in ("powershell", "wevtutil"):
            raise ValueError(f"unsupported backend: {backend!r} (powershell|wevtutil)")
        self._backend = backend
        self._runner: Runner = runner if runner is not None else _default_runner
        # Default channel for this source (a spec may override per-query).
        self._default_channel = _validate_channel(config.get("channel", _DEFAULT_CHANNEL))
        self.kind = KIND
        self.name = config.get("name") or f"wineventlog:{self._default_channel}"

    # -- argv builders ----------------------------------------------------

    def _powershell_query_argv(
        self,
        channel: str,
        level_num: int | None,
        count: int,
        provider: str | None,
        since: str | None,
    ) -> list[str]:
        # Build the FilterHashtable body from validated values only.
        parts = [f"LogName='{channel}'"]
        if level_num is not None:
            parts.append(f"Level={level_num}")
        if provider is not None:
            parts.append(f"ProviderName='{provider}'")
        if since is not None:
            parts.append(f"StartTime='{since}'")
        filter_body = "@{" + "; ".join(parts) + "}"
        ps = f"Get-WinEvent -FilterHashtable {filter_body} -MaxEvents {count} | ConvertTo-Json -Depth 5"
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps,
        ]

    def _wevtutil_query_argv(self, channel: str, count: int) -> list[str]:
        return [
            "wevtutil",
            "qe",
            channel,
            "/f:json",
            f"/c:{count}",
            "/rd:true",
        ]

    def _powershell_health_argv(self, channel: str) -> list[str]:
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Get-WinEvent -ListLog '{channel}'",
        ]

    def _wevtutil_health_argv(self, channel: str) -> list[str]:
        return ["wevtutil", "gli", channel]

    # -- helpers ----------------------------------------------------------

    def _error_record(self, message: str, raw: Any) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "error",
            "message": message,
            "value": None,
            "labels": {},
            "raw": raw,
        }

    def _event_record(self, event: dict[str, Any]) -> dict[str, Any]:
        ts = _parse_ts(event.get("TimeCreated"))
        message = event.get("Message")
        message = str(message) if message is not None else ""

        raw_id = event.get("Id")
        value: float | None
        try:
            value = float(raw_id) if raw_id is not None and not isinstance(raw_id, bool) else None
        except (TypeError, ValueError):
            value = None

        labels: dict[str, str] = {}
        for key, src in (
            ("provider", "ProviderName"),
            ("id", "Id"),
            ("machine", "MachineName"),
        ):
            val = event.get(src)
            if val is not None:
                labels[key] = str(val)
        # Channel may appear under either Channel or LogName.
        channel_val = event.get("Channel")
        if channel_val is None:
            channel_val = event.get("LogName")
        if channel_val is not None:
            labels["channel"] = str(channel_val)

        return {
            "ts": ts,
            "source": self.name,
            "kind": KIND,
            "level_or_status": _level_display(event),
            "message": message,
            "value": value,
            "labels": labels,
            "raw": event,
        }

    def _parse_events(self, stdout: str) -> list[dict[str, Any]]:
        """Parse runner stdout (JSON) into a list of event dicts.

        ConvertTo-Json emits a single object when there is exactly one event,
        and a list otherwise. wevtutil ``/f:json`` emits one JSON object per
        line. We tolerate both.
        """
        text = stdout.strip()
        if not text:
            return []
        # Try a single JSON document first (covers ConvertTo-Json list/object).
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            doc = None
        if doc is not None:
            if isinstance(doc, list):
                return [e for e in doc if isinstance(e, dict)]
            if isinstance(doc, dict):
                # wevtutil sometimes wraps under {"Events": [...]}.
                events = doc.get("Events")
                if isinstance(events, list):
                    return [e for e in events if isinstance(e, dict)]
                return [doc]
            raise ValueError(f"unexpected JSON top-level type: {type(doc).__name__}")
        # Fall back to JSON-lines (wevtutil one-object-per-line).
        out: list[dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            obj = json.loads(line)  # may raise -> caught by caller
            if isinstance(obj, dict):
                out.append(obj)
        return out

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect events per ``spec`` and return normalized records.

        Spec keys: ``channel`` (default 'System' / config), ``level``,
        ``count``, ``provider``, ``since``. Never raises — any validation,
        transport, or parse failure becomes a single error record.
        """
        spec = spec or {}
        try:
            channel = _validate_channel(spec.get("channel", self._default_channel))
            count = _validate_count(spec.get("count", _DEFAULT_COUNT))
            level_num: int | None = None
            if spec.get("level") is not None:
                level_num = _validate_level(spec["level"])
            provider: str | None = None
            if spec.get("provider") is not None:
                provider = _validate_provider(spec["provider"])
            since: str | None = None
            if spec.get("since") is not None:
                since = _validate_since(spec["since"])
        except ValueError as exc:
            return [self._error_record(f"invalid query spec: {exc}", {"spec": spec})]

        if self._backend == "powershell":
            argv = self._powershell_query_argv(channel, level_num, count, provider, since)
        else:
            argv = self._wevtutil_query_argv(channel, count)

        try:
            rc, stdout, stderr = self._runner(argv)
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"runner error: {exc}", {"argv": list(argv)})]

        if rc != 0:
            return [
                self._error_record(
                    f"collector exited rc={rc}: {stderr.strip() or '(no stderr)'}",
                    {"argv": list(argv), "rc": rc, "stderr": stderr},
                )
            ]

        try:
            events = self._parse_events(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            return [self._error_record(f"failed to parse collector output: {exc}", {"stdout": stdout})]

        return [self._event_record(e) for e in events]

    def health(self) -> dict[str, Any]:
        """Probe channel reachability via a trivial list-log command. Never raises."""
        try:
            channel = _validate_channel(self._default_channel)
        except ValueError as exc:
            return {"ok": False, "source": self.name, "detail": f"invalid channel: {exc}"}

        if self._backend == "powershell":
            argv = self._powershell_health_argv(channel)
        else:
            argv = self._wevtutil_health_argv(channel)

        try:
            rc, _stdout, stderr = self._runner(argv)
        except Exception as exc:  # health must never raise
            return {"ok": False, "source": self.name, "detail": f"runner error: {exc}"}

        ok = rc == 0
        detail = "ok" if ok else (stderr.strip() or f"list-log rc={rc}")
        return {"ok": ok, "source": self.name, "detail": detail, "channel": channel}
