"""Windows Event Log connector.

A self-contained :class:`WindowsEventSource` that reads a Windows event channel
through an *injected* runner, using one of two backends selected by config:

- ``wevtutil`` — ``wevtutil qe <Log> /f:json`` (the modern ``/f:json`` form).
- ``powershell`` — ``Get-WinEvent -LogName <Log> ... | ConvertTo-Json``.

Both backends emit JSON in *different shapes*; this connector parses both and
normalizes each event into the shared record shape
(``ts``/``source``/``kind``/``level_or_status``/``message``/``value``/``labels``/``raw``).

Safety
------
- **No shell.** The runner is handed an argv *list*; nothing is interpolated
  into a shell string.
- **Channel validation.** The channel/log name is validated against an
  allow-list pattern (letters, digits, space, ``-``, ``/``, ``_``). A leading
  dash or any metacharacter is rejected and the runner is *not* invoked.
- **health() never raises.** Failures become ``{"ok": False, "detail": ...}``.

Stdlib only; imports no sibling connector module.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

KIND = "logs"

_BACKEND_WEVTUTIL = "wevtutil"
_BACKEND_POWERSHELL = "powershell"

# Windows event Level (numeric) -> level name.
_LEVEL_NAMES: dict[int, str] = {
    0: "info",  # LogAlways
    1: "critical",
    2: "error",
    3: "warning",
    4: "info",
    5: "verbose",
}

# Allowed channel-name characters: e.g. "Application", "System",
# "Microsoft-Windows-PowerShell/Operational".
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9 _\-/]+$")
_METACHARACTERS = set(";|&$`<>()!\\\"'*?[]{}\n\r\t")


class CommandRunner(Protocol):
    """Structural contract for the injected command runner.

    Receives an argv *list* and returns captured stdout text. Never a shell
    string. Tests inject a fake that returns canned ``wevtutil``/PowerShell JSON
    and record the argv it was handed.
    """

    def __call__(self, argv: list[str]) -> str:
        ...


class WindowsEventSource:
    """Read + normalize Windows event-log entries (wevtutil or Get-WinEvent)."""

    KIND: str = KIND

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        config = config or {}
        self.name: str = str(config.get("name", "windows_event"))
        self._config = config
        backend = str(config.get("backend", _BACKEND_WEVTUTIL)).lower()
        if backend not in (_BACKEND_WEVTUTIL, _BACKEND_POWERSHELL):
            raise ValueError(f"unknown backend: {backend!r}")
        self._backend = backend
        self._wevtutil = str(config.get("wevtutil_path", "wevtutil"))
        self._powershell = str(config.get("powershell_path", "powershell"))
        self._default_count = int(config.get("count", 200))
        self._runner: CommandRunner = runner if runner is not None else _default_runner

    # -- validation -------------------------------------------------------- #
    def _validate_channel(self, channel: str) -> str:
        if not channel or channel[0] == "-":
            raise ValueError(f"unsafe channel: {channel!r}")
        if any(ch in _METACHARACTERS for ch in channel) or not _CHANNEL_RE.match(channel):
            raise ValueError(f"unsafe channel: {channel!r}")
        return channel

    # -- argv construction ------------------------------------------------- #
    def _build_argv(self, channel: str, count: int) -> list[str]:
        channel = self._validate_channel(channel)
        if self._backend == _BACKEND_WEVTUTIL:
            return [self._wevtutil, "qe", channel, "/f:json", f"/c:{int(count)}", "/rd:true"]
        # PowerShell: a fixed-shape command; the channel is validated above and
        # passed through -LogName as a single argv token (no shell).
        ps_cmd = (
            f"Get-WinEvent -LogName '{channel}' -MaxEvents {int(count)} "
            "| ConvertTo-Json -Depth 4"
        )
        return [self._powershell, "-NoProfile", "-NonInteractive", "-Command", ps_cmd]

    # -- normalization ----------------------------------------------------- #
    @staticmethod
    def _parse_ts(value: Any) -> float | None:
        if value is None:
            return None
        # PowerShell ConvertTo-Json renders DateTime as "/Date(1700000000000)/".
        if isinstance(value, str):
            m = re.search(r"/Date\((\d+)", value)
            if m:
                return int(m.group(1)) / 1000.0
            # wevtutil json TimeCreated is an ISO-8601 string.
            try:
                import datetime as _dt

                return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _level_name(self, raw_level: Any, fallback: Any = None) -> str:
        for candidate in (raw_level, fallback):
            if candidate is None:
                continue
            if isinstance(candidate, str) and not candidate.isdigit():
                return candidate.lower()
            try:
                return _LEVEL_NAMES.get(int(candidate), "info")
            except (TypeError, ValueError):
                continue
        return "info"

    def _normalize_wevtutil(self, ev: dict[str, Any]) -> dict[str, Any]:
        # wevtutil /f:json shape: flat-ish keys (Level, Message, Channel,
        # Provider/ProviderName, EventID, Computer, TimeCreated).
        level = self._level_name(ev.get("Level"), ev.get("LevelDisplayName"))
        labels = {
            "channel": ev.get("Channel"),
            "provider": ev.get("ProviderName") or ev.get("Provider"),
            "event_id": ev.get("EventID") or ev.get("Id"),
            "computer": ev.get("Computer") or ev.get("MachineName"),
        }
        return {
            "ts": self._parse_ts(ev.get("TimeCreated")),
            "source": self.name,
            "kind": KIND,
            "level_or_status": level,
            "message": str(ev.get("Message", "")),
            "value": None,
            "labels": labels,
            "raw": ev,
        }

    def _normalize_powershell(self, ev: dict[str, Any]) -> dict[str, Any]:
        # Get-WinEvent | ConvertTo-Json shape: object-per-event with
        # LevelDisplayName, Message, LogName, ProviderName, Id, MachineName,
        # TimeCreated as "/Date(...)/".
        level = self._level_name(ev.get("LevelDisplayName"), ev.get("Level"))
        labels = {
            "channel": ev.get("LogName") or ev.get("Channel"),
            "provider": ev.get("ProviderName"),
            "event_id": ev.get("Id") or ev.get("EventID"),
            "computer": ev.get("MachineName") or ev.get("Computer"),
        }
        return {
            "ts": self._parse_ts(ev.get("TimeCreated")),
            "source": self.name,
            "kind": KIND,
            "level_or_status": level,
            "message": str(ev.get("Message", "")),
            "value": None,
            "labels": labels,
            "raw": ev,
        }

    def _normalize(self, ev: dict[str, Any]) -> dict[str, Any]:
        # Parse BOTH shapes: a PowerShell payload carries LevelDisplayName /
        # LogName / MachineName; wevtutil carries Channel / Computer.
        if "LevelDisplayName" in ev or "LogName" in ev or "MachineName" in ev:
            return self._normalize_powershell(ev)
        return self._normalize_wevtutil(ev)

    @staticmethod
    def _iter_events(body: str) -> list[dict[str, Any]]:
        body = body.strip()
        if not body:
            return []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Fall back to newline-delimited JSON objects (wevtutil can emit one
            # JSON object per event per line).
            out: list[dict[str, Any]] = []
            for raw_line in body.splitlines():
                line = raw_line.strip().rstrip(",")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
            return out
        # ConvertTo-Json yields a single object for one event, or a list for many.
        if isinstance(data, dict):
            inner = data.get("Events")
            if isinstance(inner, list):
                return [e for e in inner if isinstance(e, dict)]
            return [data]
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        return []

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Read events from the configured channel and return normalized records.

        Raises ``ValueError`` if the channel name is unsafe — *before* the runner
        is invoked, so an injection attempt never reaches the command line.
        """
        spec = spec or {}
        channel = str(spec.get("channel", self._config.get("channel", "System")))
        count = int(spec.get("count", self._default_count))
        argv = self._build_argv(channel, count)  # validates channel; may raise
        out = self._runner(argv)
        return [self._normalize(ev) for ev in self._iter_events(out)]

    def health(self) -> dict[str, Any]:
        """Probe the backend with a 1-event read. MUST NOT raise."""
        try:
            channel = str(self._config.get("channel", "System"))
            argv = self._build_argv(channel, 1)
            out = self._runner(argv)
            ok = out is not None
            return {"ok": ok, "detail": f"{self._backend} reachable" if ok else "no output"}
        except Exception as exc:  # never raise out of health()
            return {"ok": False, "detail": f"{self._backend} unavailable: {exc}"}


def _default_runner(argv: list[str]) -> str:
    """Real runner: execute argv with no shell and return stdout.

    Imported lazily; tests always inject their own runner so this never runs
    during unit tests.
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
