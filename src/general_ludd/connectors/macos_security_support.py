"""Validated command execution support for the macOS security connector."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

RunnerResult = tuple[int, str, str] | str
Runner = Callable[[list[str]], RunnerResult]

SHELL_METACHARS: frozenset[str] = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \"'")
DEFAULT_TIMEOUT = 30.0


def validate_arg(value: str, field: str) -> str:
    """Validate a caller-supplied argv value against option and shell injection."""
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
    """Run list-form argv without a shell and with a finite timeout."""
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


def run(runner: Runner, argv: list[str]) -> tuple[int, str, str]:
    """Normalize canned stdout runners and production tuple runners."""
    result = runner(argv)
    if isinstance(result, str):
        return 0, result, ""
    if isinstance(result, tuple) and len(result) == 3:
        rc, stdout, stderr = result
        return int(rc), str(stdout or ""), str(stderr or "")
    raise TypeError("runner must return stdout or (returncode, stdout, stderr)")


__all__ = (
    "DEFAULT_TIMEOUT",
    "SHELL_METACHARS",
    "Runner",
    "RunnerResult",
    "default_runner",
    "run",
    "validate_arg",
)
