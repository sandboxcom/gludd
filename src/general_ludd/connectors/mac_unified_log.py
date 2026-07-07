"""macOS unified-log connector for general-ludd.

Self-contained source: this module deliberately imports nothing from sibling
connectors, a base class, or the package ``__init__`` — it depends only on the
Python standard library so it can be reasoned about (and security-reviewed) in
isolation.

The connector shells out to Apple's ``log`` tool, but ONLY through an injectable
command runner whose default implementation uses ``subprocess.run`` with a LIST
argv (never ``shell=True``, always time-bound). Tests inject a canned runner so
no real ``log`` process is ever spawned.

Security note — the ``--predicate`` flag accepts an NSPredicate expression that
is powerful enough to be dangerous if an attacker can smuggle shell metacharacters
or a leading-dash option-injection through it. Because we always pass argv as a
discrete list (never a shell string, never interpolated), shell metacharacters are
inert; we additionally validate the predicate/level/duration and reject anything
that looks like option-injection or a shell escape, failing closed BEFORE the
runner is ever called.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping

# A runner takes an argv list and returns (returncode, stdout, stderr).
Runner = Callable[[list[str]], "tuple[int, str, str]"]

# Dangerous characters that must never appear in a caller-supplied predicate.
#
# Because we always pass the predicate as a discrete argv element (the shell
# never sees it), quoting characters that are LEGITIMATE inside an NSPredicate
# string literal — double quote, single quote, backslash — are inert and MUST be
# permitted (e.g. `subsystem == "com.apple.kernel"`). What we reject are the
# command-chaining / redirection / substitution metacharacters plus any control
# characters: these have no place in a real NSPredicate and are the hallmark of
# an attacker probing for a shell breakout. (Newlines/CR/tab are also caught by
# the control-character check below.)
_SHELL_METACHARS = set(";|&$`<>()\n\r\t")

# messageType values emitted by `log show --style ndjson`. Anything else in a
# caller-supplied --level is rejected (it would otherwise be passed straight to
# the `log` CLI).
_VALID_LEVELS = frozenset({"default", "info", "debug", "error", "fault"})

# Accept a compact duration like 5m, 30s, 2h, 1d, or a bare integer (seconds).
_DURATION_RE = re.compile(r"^\d+[smhd]?$")

# Default lookback window when the caller does not specify one.
_DEFAULT_LAST = "5m"

# Hard ceiling on how long the injected/default runner may block.
_RUNNER_TIMEOUT_S = 30.0


class PredicateValidationError(ValueError):
    """Raised when a caller-supplied predicate / level / duration is rejected.

    Validation happens before the command runner is invoked, so a rejected
    argument guarantees no ``log`` process (real or injected) is ever spawned.
    """


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run ``argv`` as a discrete list via subprocess.

    Never uses ``shell=True``; always time-bound. This is the only place a real
    process is spawned, and tests replace it wholesale via the ``runner`` kwarg.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_RUNNER_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {_RUNNER_TIMEOUT_S}s")
    except (OSError, ValueError) as exc:  # e.g. `log` not present, bad argv
        return (127, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


def _validate_duration(value: object) -> str:
    if not isinstance(value, str):
        raise PredicateValidationError(f"last must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate:
        raise PredicateValidationError("last must not be empty")
    if candidate.startswith("-"):
        raise PredicateValidationError(f"last must not start with '-': {value!r}")
    if not _DURATION_RE.match(candidate):
        raise PredicateValidationError(f"invalid duration: {value!r}")
    return candidate


def _validate_level(value: object) -> str:
    if not isinstance(value, str):
        raise PredicateValidationError(f"level must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if candidate.startswith("-"):
        raise PredicateValidationError(f"level must not start with '-': {value!r}")
    if candidate.lower() not in _VALID_LEVELS:
        raise PredicateValidationError(
            f"invalid level {value!r}; expected one of {sorted(_VALID_LEVELS)}"
        )
    return candidate.lower()


def _validate_predicate(value: object) -> str:
    if not isinstance(value, str):
        raise PredicateValidationError(
            f"predicate must be a string, got {type(value).__name__}"
        )
    candidate = value.strip()
    if not candidate:
        raise PredicateValidationError("predicate must not be empty")
    # Option-injection guard: a leading dash would let an attacker smuggle an
    # extra `log` flag (e.g. `--predicate "-x"` becoming a new option).
    if candidate.startswith("-"):
        raise PredicateValidationError(
            f"predicate must not start with '-' (option-injection guard): {value!r}"
        )
    bad = sorted(_SHELL_METACHARS.intersection(candidate))
    if bad:
        raise PredicateValidationError(
            f"predicate contains forbidden characters {bad}: {value!r}"
        )
    # Reject any non-printable / control character (covers newline, CR, tab, NUL,
    # and any other smuggled control byte).
    ctrl = [c for c in candidate if ord(c) < 0x20 or ord(c) == 0x7F]
    if ctrl:
        raise PredicateValidationError(
            f"predicate contains control characters: {value!r}"
        )
    return candidate


def _parse_timestamp(record: Mapping[str, object]) -> str | None:
    """Pull the event timestamp out of an ndjson record.

    `log show --style ndjson` uses the ``timestamp`` key; we keep the raw string
    (callers/normalizers downstream parse it) but fall back to None when absent.
    """
    ts = record.get("timestamp")
    if isinstance(ts, str) and ts.strip():
        return ts
    return None


def _extract_process(record: Mapping[str, object]) -> str | None:
    """Best-effort process identity from any of the keys `log` may emit."""
    for key in ("processImagePath", "process", "processID"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, int):
            return str(value)
    return None


class MacUnifiedLogSource:
    """Read records from the macOS unified logging system via ``log show``.

    Parameters
    ----------
    config:
        Arbitrary mapping; reserved for future options (e.g. a default
        predicate). Unknown keys are ignored so callers can pass a superset.
    runner:
        Optional injected command runner ``(argv) -> (rc, stdout, stderr)``.
        Defaults to a ``subprocess.run`` LIST-argv runner (never ``shell=True``).
    """

    KIND = "logs"

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config: dict[str, object] = dict(config or {})
        self.name: str = str(self.config.get("name", "mac_unified_log"))
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- command construction -------------------------------------------------

    def _build_argv(self, last: str, level: str | None, predicate: str | None) -> list[str]:
        argv = ["log", "show", "--style", "ndjson", "--last", last]
        if level is not None:
            argv += ["--level", level]
        if predicate is not None:
            # Passed as a single discrete argv element — NEVER interpolated into
            # a shell string. The list form is what makes metacharacters inert.
            argv += ["--predicate", predicate]
        return argv

    # -- public API -----------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Best-effort liveness probe. Never raises.

        Tries ``log stats`` first; if that errors out at the process level, falls
        back to a tiny ``log show`` window. ``ok`` is True iff a probe returned
        rc == 0.
        """
        try:
            rc, _out, err = self._runner(["log", "stats"])
            if rc == 0:
                return {"ok": True, "detail": "log stats rc=0"}
            rc2, _out2, err2 = self._runner(
                ["log", "show", "--last", "1s", "--style", "ndjson"]
            )
            if rc2 == 0:
                return {"ok": True, "detail": "log show probe rc=0"}
            detail = (err2 or err or "").strip() or f"log stats rc={rc}, show rc={rc2}"
            return {"ok": False, "detail": detail}
        except Exception as exc:  # health() must never raise
            return {"ok": False, "detail": f"probe raised: {exc!r}"}

    def query(self, spec: Mapping[str, object] | None = None) -> list[dict[str, object]]:
        """Run ``log show`` per ``spec`` and return normalized records.

        spec keys (all optional):
          last:      duration string (default '5m'), e.g. '30s', '2h'
          level:     one of default/info/debug/error/fault
          predicate: NSPredicate string (validated; shell-metachar/dash-rejected)

        Raises PredicateValidationError on bad args (runner is NOT called).
        """
        spec = spec or {}

        last = _validate_duration(spec["last"]) if "last" in spec else _DEFAULT_LAST
        level = _validate_level(spec["level"]) if spec.get("level") is not None else None
        predicate = (
            _validate_predicate(spec["predicate"])
            if spec.get("predicate") is not None
            else None
        )

        argv = self._build_argv(last, level, predicate)
        rc, stdout, _stderr = self._runner(argv)
        if rc != 0:
            # Surface failures as an empty result rather than raising; query()
            # callers treat an empty list as "no records / unavailable".
            return []

        records: list[dict[str, object]] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except (ValueError, TypeError):
                # Malformed ndjson line — skip it, keep parsing the rest.
                continue
            if not isinstance(raw, dict):
                continue
            records.append(self._normalize(raw))
        return records

    # -- normalization --------------------------------------------------------

    def _normalize(self, raw: Mapping[str, object]) -> dict[str, object]:
        labels = {
            "subsystem": raw.get("subsystem"),
            "category": raw.get("category"),
            "process": _extract_process(raw),
            "senderImagePath": raw.get("senderImagePath"),
        }
        message = raw.get("eventMessage")
        return {
            "ts": _parse_timestamp(raw),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": raw.get("messageType"),
            "message": message if isinstance(message, str) else None,
            "value": None,
            "labels": labels,
            "raw": dict(raw),
        }
