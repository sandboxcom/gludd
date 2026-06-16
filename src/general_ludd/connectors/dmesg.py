"""dmesg (kernel ring buffer) host log connector.

Runs ``dmesg --json`` through an injected runner and normalizes each kernel
log entry into a flat log record. The connector never spawns a shell: the
command is built as an argv list and handed to the runner as such;
``shell=True`` is never used. Caller-supplied filter arguments are validated to
reject leading-dash (flag-injection) and shell metacharacters before they are
appended to the argv.

Self-contained: imports nothing from sibling connector modules and defines its
own runner protocol so it can be unit-tested with a canned, injected runner
(no real ``dmesg`` binary or ``/dev/kmsg`` access required).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommandRunner(Protocol):
    """Injectable subprocess runner returning a process result for an argv."""

    def __call__(self, argv: Sequence[str]) -> RunResult: ...


class RunResult(Protocol):
    """Minimal result shape a runner must return."""

    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


_SHELL_METACHARS = re.compile(r"[;&|`$\\<>(){}\[\]!*?~\n\r\x00\s]")

# syslog numeric level -> textual level for normalization.
_LEVEL_NAMES: dict[int, str] = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


class DmesgSource:
    """Collect host logs from the kernel ring buffer via ``dmesg --json``."""

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "dmesg"))
        self._binary: str = str(self.config.get("binary", "dmesg"))
        self._runner: CommandRunner | None = runner

    # -- validation -------------------------------------------------------

    @staticmethod
    def _validate_arg(arg: str) -> str:
        if not isinstance(arg, str):
            raise ValueError("dmesg filter arg must be a string")
        if arg == "":
            raise ValueError("dmesg filter arg must not be empty")
        if arg.startswith("-"):
            raise ValueError("dmesg filter arg must not start with '-'")
        if _SHELL_METACHARS.search(arg):
            raise ValueError("dmesg filter arg contains forbidden characters")
        return arg

    def _build_argv(self, spec: dict[str, Any]) -> list[str]:
        argv = [self._binary, "--json"]
        facility = spec.get("facility")
        if facility is not None:
            # ``-f`` would be flag-injection; we instead validate the value and
            # pass it via the explicit ``--facility=VALUE`` long form so a
            # leading dash cannot smuggle in a new option.
            argv.append(f"--facility={self._validate_arg(str(facility))}")
        level = spec.get("level")
        if level is not None:
            argv.append(f"--level={self._validate_arg(str(level))}")
        return argv

    # -- runner -----------------------------------------------------------

    def _run(self, argv: list[str]) -> RunResult:
        if self._runner is None:
            raise RuntimeError("no runner injected for DmesgSource")
        return self._runner(argv)

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``; never raises."""
        try:
            if self._runner is None and shutil.which(self._binary) is None:
                return {"ok": False, "detail": f"{self._binary} not found on PATH"}
            result = self._run([self._binary, "--json"])
            rc = result.returncode
            if rc != 0:
                detail = (result.stderr or "").strip() or f"exit code {rc}"
                return {"ok": False, "detail": detail}
            return {"ok": True, "detail": "dmesg responded"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ------------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run dmesg and return normalized log records."""
        argv = self._build_argv(spec or {})
        result = self._run(argv)
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"dmesg failed: {detail}")
        entries = self._parse_entries(result.stdout)
        return [self._normalize_entry(entry) for entry in entries]

    @staticmethod
    def _parse_entries(stdout: str) -> list[dict[str, Any]]:
        text = (stdout or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"dmesg returned non-JSON output: {exc}") from exc
        # dmesg --json emits {"dmesg": [ {...}, ... ]}
        if isinstance(data, dict):
            raw_entries = data.get("dmesg", [])
        elif isinstance(data, list):
            raw_entries = data
        else:
            raise RuntimeError("dmesg JSON output had an unexpected shape")
        entries: list[dict[str, Any]] = []
        for item in raw_entries:
            if isinstance(item, dict):
                entries.append(item)
        return entries

    def _normalize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        level = self._level_name(entry.get("level"), entry.get("priority"))
        ts = entry.get("timestamp")
        if isinstance(ts, dict):
            ts = ts.get("usec") or ts.get("sec")
        message = str(entry.get("msg", entry.get("message", "")))
        labels = {
            "facility": self._coerce_str(entry.get("facility")),
            "subsystem": self._coerce_str(entry.get("subsystem") or entry.get("caller")),
        }
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": dict(entry),
        }

    @staticmethod
    def _coerce_str(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _level_name(level: Any, priority: Any) -> str:
        for candidate in (level, priority):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            if isinstance(candidate, int):
                return _LEVEL_NAMES.get(candidate, str(candidate))
        return "info"
