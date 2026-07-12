"""macOS unified-log connector — `MacosLogSource`.

Self-contained host-log connector for gludd. It shells out (via an INJECTED
runner — never a live subprocess inside this module's hot path so it stays
unit-testable) to Apple's `log` tool:

    log show --style json --last <dur>     # historical snapshot
    log stream --style json ...            # bounded live snapshot

and normalizes each record into the gludd connector record shape:

    {ts, source, kind, level_or_status, message, value, labels, raw}

Security / contract notes:
  * KIND class attribute = 'logs'.
  * The runner is injected and receives an ARGV LIST (never a shell string),
    so there is no `shell=True` and no shell-metacharacter surface.
  * Any user-supplied predicate is validated: shell/log metacharacters and
    backticks are REJECTED (fail-closed) and the predicate is passed as a
    single argv element.
  * `health()` NEVER raises — it returns {'ok': bool, 'detail': str}.
  * `query()` is time-bound (a duration is always passed to `log`).

No imports from sibling connectors or any gludd base module.

"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol


class _Runner(Protocol):
    """Injected command runner.

    Implementations execute an argv LIST (no shell) and return a tuple of
    (returncode, stdout, stderr). Tests inject a canned runner; production
    injects a subprocess-backed one. This module never builds a shell string.
    """

    def __call__(self, argv: list[str]) -> tuple[int, str, str]: ...


# Reject anything that could break out of a single argv element if a downstream
# consumer were ever careless, or that indicates an attempt at shell/predicate
# injection. We allow normal predicate syntax (==, letters, quotes, spaces).
_PREDICATE_FORBIDDEN = re.compile(r"[`$;&|<>\n\r\\]|\$\(")

# messageType -> normalized level
_LEVEL_MAP = {
    "default": "info",
    "info": "info",
    "debug": "debug",
    "error": "error",
    "fault": "critical",
}


class PredicateError(ValueError):
    """Raised when a log predicate contains forbidden metacharacters."""


class MacosLogSource:
    """Connector over the macOS unified logging system (`log` tool)."""

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: _Runner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "macos_log"))
        self._runner = runner
        # Default historical window; query(spec) may override.
        self._default_duration: str = str(self.config.get("duration", "5m"))
        # Optional fixed predicate from config (still validated).
        cfg_pred = self.config.get("predicate")
        self._config_predicate: str | None = (
            self._validate_predicate(str(cfg_pred)) if cfg_pred else None
        )
        self._log_binary: str = str(self.config.get("log_binary", "log"))

    # -- predicate validation ------------------------------------------------

    @staticmethod
    def _validate_predicate(predicate: str) -> str:
        """Return predicate unchanged if safe; raise PredicateError otherwise."""
        if _PREDICATE_FORBIDDEN.search(predicate):
            raise PredicateError(
                f"predicate contains forbidden metacharacters: {predicate!r}"
            )
        return predicate

    @staticmethod
    def _validate_duration(duration: str) -> str:
        """A `log --last` duration token: digits + unit (s/m/h/d). Fail-closed."""
        if not re.fullmatch(r"\d+[smhd]", duration):
            raise ValueError(f"invalid duration token: {duration!r}")
        return duration

    # -- argv construction ---------------------------------------------------

    def _build_argv(self, duration: str, predicate: str | None) -> list[str]:
        argv = [
            self._log_binary,
            "show",
            "--style",
            "json",
            "--last",
            duration,
        ]
        if predicate:
            # Single argv element — never interpolated into a shell string.
            argv += ["--predicate", predicate]
        return argv

    # -- runner --------------------------------------------------------------

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        if self._runner is None:
            raise RuntimeError("MacosLogSource requires an injected runner")
        return self._runner(argv)

    # -- normalization -------------------------------------------------------

    def _normalize(self, entry: dict[str, Any]) -> dict[str, Any]:
        message_type = str(entry.get("messageType", "default") or "default").lower()
        level = _LEVEL_MAP.get(message_type, "info")
        labels = {
            "subsystem": entry.get("subsystem"),
            "category": entry.get("category"),
            "process": entry.get("processImagePath") or entry.get("process"),
            "pid": entry.get("processID") or entry.get("pid"),
        }
        return {
            "ts": entry.get("timestamp"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level,
            "message": entry.get("eventMessage", ""),
            "value": None,
            "labels": {k: v for k, v in labels.items() if v is not None},
            "raw": entry,
        }

    @staticmethod
    def _parse_payload(stdout: str) -> list[dict[str, Any]]:
        """`log show --style json` emits a JSON array (sometimes NDJSON-ish)."""
        text = stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fall back to line-delimited JSON objects.
            records: list[dict[str, Any]] = []
            for raw_line in text.splitlines():
                line = raw_line.strip().rstrip(",")
                if not line or line in ("[", "]"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
            return records
        if isinstance(data, dict):
            return [data]
        return [d for d in data if isinstance(d, dict)]

    # -- public API ----------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a time-bound `log show` and return normalized records."""
        spec = dict(spec or {})
        duration = self._validate_duration(
            str(spec.get("duration", self._default_duration))
        )
        predicate = spec.get("predicate")
        validated_predicate = (
            self._validate_predicate(str(predicate))
            if predicate
            else self._config_predicate
        )
        argv = self._build_argv(duration, validated_predicate)
        rc, stdout, _stderr = self._run(argv)
        if rc != 0:
            return []
        return [self._normalize(e) for e in self._parse_payload(stdout)]

    def health(self) -> dict[str, Any]:
        """Probe a tiny `log show` invocation. NEVER raises."""
        try:
            if self._runner is None:
                return {"ok": False, "detail": "no runner injected"}
            argv = [self._log_binary, "show", "--style", "json", "--last", "1s"]
            rc, _stdout, stderr = self._run(argv)
            if rc == 0:
                return {"ok": True, "detail": "log tool reachable"}
            return {"ok": False, "detail": f"log exited rc={rc}: {stderr[:200]}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"probe error: {type(exc).__name__}: {exc}"}


def _secret_from_env(env_key: str | None) -> str | None:
    """Resolve a secret strictly from the named environment variable."""
    if not env_key:
        return None
    return os.environ.get(env_key)
