#!/usr/bin/env python3
"""Run the local quality gate without shell command interpolation."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 600
GATE_FILE = REPO_ROOT / ".gate-status"

Phase = tuple[str, tuple[str, ...]]
PHASES: tuple[Phase, ...] = (
    (
        "lint",
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "src",
            "tests",
            "--output-format",
            "concise",
        ),
    ),
    ("typecheck", (sys.executable, "-m", "mypy", "-p", "general_ludd")),
    (
        "collect",
        (
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "tests",
            "-q",
            "--no-header",
        ),
    ),
    ("hook-runtime", ("make", "--no-print-directory", "test-hook-runtime")),
    (
        "test",
        (
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "-q",
            "--no-header",
        ),
    ),
    ("smoke", ("make", "--no-print-directory", "smoke")),
)


def _write_log(
    log_path: Path,
    stdout: str | None,
    stderr: str | None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text((stdout or "") + (stderr or ""), encoding="utf-8")


def run(
    command: Sequence[str],
    *,
    log: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, subprocess.CompletedProcess[str] | None]:
    """Run one phase and return its success state and completed process.

    Calls without a log inherit the terminal streams so long phases remain
    observable. A caller that requests a log receives captured UTF-8 output.
    """
    log_path = Path(log) if log is not None else None
    try:
        completed = subprocess.run(
            tuple(command),
            cwd=REPO_ROOT,
            capture_output=log_path is not None,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if log_path is not None:
            _write_log(
                log_path,
                f"TIMEOUT after {timeout:g}s\n",
                str(exc),
            )
        return False, None
    except OSError as exc:
        if log_path is not None:
            _write_log(log_path, "", f"{exc}\n")
        return False, None

    if log_path is not None:
        _write_log(log_path, completed.stdout, completed.stderr)
    return completed.returncode == 0, completed


def _record(status_file: Path, message: str) -> None:
    print(message, flush=True)
    with status_file.open("a", encoding="utf-8") as stream:
        stream.write(f"{message}\n")


def main(*, gate_file: Path | None = None) -> int:
    """Run all phases in order and stop at the first failure."""
    status_file = gate_file or GATE_FILE
    status_file.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    status_file.write_text(
        f"=== GATE-LOCAL {started_at} ===\n",
        encoding="utf-8",
    )

    for name, command in PHASES:
        _record(status_file, f"{name}: RUNNING")
        succeeded, _completed = run(command)
        result = "PASS" if succeeded else "FAIL"
        _record(status_file, f"{name}: {result}")
        if not succeeded:
            _record(status_file, "GATE-LOCAL: FAIL")
            return 1

    _record(status_file, "GATE-LOCAL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
