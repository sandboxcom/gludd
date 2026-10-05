"""Safe command and normalization helpers for Windows Defender."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

RunnerResult = tuple[int, str, str] | str
Runner = Callable[[list[str]], RunnerResult]

SHELL_METACHARS: frozenset[str] = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \"'")
DEFAULT_TIMEOUT = 30.0
VALID_TARGETS = frozenset(
    {
        "status",
        "computer_status",
        "preferences",
        "mp_preference",
        "threats",
        "threat_detection",
        "scan",
        "start_scan",
        "exclusions",
        "get_exclusions",
    }
)
VALID_SCAN_TYPES = frozenset({"QuickScan", "FullScan"})


def validate_arg(value: str, field: str) -> str:
    """Reject empty values, option injection, and shell metacharacters."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if value == "":
        raise ValueError(f"{field} must not be empty")
    if value.startswith("-"):
        raise ValueError(f"{field} must not start with '-': {value!r}")
    bad = sorted(set(value) & SHELL_METACHARS)
    if bad:
        raise ValueError(f"{field} contains disallowed characters {bad!r}: {value!r}")
    return value


def default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run a discrete, non-shell argv with a fixed timeout."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {DEFAULT_TIMEOUT}s")
    except (OSError, ValueError) as exc:
        return (127, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


def ps_command(cmdlet: str) -> list[str]:
    """Build a PowerShell argv returning bounded-depth JSON."""
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"{cmdlet} | ConvertTo-Json -Depth 5",
    ]


def parse_json_stdout(stdout: str) -> list[dict[str, Any]]:
    """Parse JSON stdout and normalize a single object to a list."""
    text = (stdout or "").strip()
    if not text:
        return []
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def run(runner: Runner, argv: list[str]) -> tuple[int, str, str]:
    """Normalize injected and production runner result contracts."""
    try:
        result = runner(argv)
    except Exception as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    if isinstance(result, str):
        return 0, result, ""
    if isinstance(result, tuple) and len(result) == 3:
        return int(result[0]), str(result[1] or ""), str(result[2] or "")
    raise TypeError("runner must return stdout or (returncode, stdout, stderr)")


def normalize_record(
    raw_item: dict[str, Any],
    ts: float,
    source: str,
    kind: str,
    *,
    level_or_status: str = "info",
    message: str = "",
    command: str = "",
) -> dict[str, Any]:
    """Normalize one Defender record without discarding its raw evidence."""
    return {
        "ts": ts,
        "source": source,
        "kind": kind,
        "level_or_status": level_or_status,
        "message": message or str(raw_item.get("message", json.dumps(raw_item))),
        "value": None,
        "labels": dict(raw_item),
        "raw": {"command": command, "raw": raw_item},
    }


__all__ = (
    "DEFAULT_TIMEOUT",
    "SHELL_METACHARS",
    "VALID_SCAN_TYPES",
    "VALID_TARGETS",
    "Runner",
    "RunnerResult",
    "default_runner",
    "normalize_record",
    "parse_json_stdout",
    "ps_command",
    "run",
    "validate_arg",
)
