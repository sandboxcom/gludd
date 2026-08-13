#!/usr/bin/env python3
"""macOS diagnostic gatherer — macos_diagnose role backend.

Self-contained (stdlib only) gatherer invoked by the ansible role to
collect unified log, system_profiler, launchctl, nvram, and pmset data
from a macOS host. Produces a single JSON artifact.

Usage:
    python3 macos_gather.py --output /tmp/artifact.json
    python3 macos_gather.py --output /tmp/artifact.json --log-window 10m
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from contextlib import suppress
from typing import Any

_MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024


def _run(
    args: list[str],
    timeout: int = 30,
    max_output_bytes: int = _MAX_COMMAND_OUTPUT_BYTES,
) -> str:
    """Run a command with disk-spooled, size-bounded stdout.

    Diagnostic commands such as ``log show`` can emit hundreds of megabytes in
    a short window. Spooling to a temporary file prevents ``subprocess.run``
    from retaining the full stream in RAM; only the bounded prefix is decoded
    for the structured artifact.
    """
    if max_output_bytes <= 0:
        return ""
    try:
        with tempfile.TemporaryFile(mode="w+b") as output:
            result = subprocess.run(
                args,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            if result.returncode != 0:
                return ""
            output.seek(0)
            return output.read(max_output_bytes).decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def parse_unified_log(raw: str) -> list[dict[str, Any]]:
    """Parse unified log ndjson output into structured entries.

    Each line is a JSON object with fields like:
    timestamp, eventType, processName, categoryName, message.
    """
    entries: list[dict[str, Any]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def parse_launchctl_list(raw: str) -> list[dict[str, Any]]:
    """Parse launchctl list output into structured service entries.

    Format: PID Status Label
    """
    entries: list[dict[str, Any]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return entries

    for line in lines[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_str = parts[0]
        status_str = parts[1]
        label = parts[2]
        entry: dict[str, Any] = {
            "pid": 0,
            "status": 0,
            "label": label,
        }
        with suppress(ValueError):
            entry["pid"] = int(pid_str) if pid_str != "-" else 0
        with suppress(ValueError):
            entry["status"] = int(status_str) if status_str != "-" else 0
        entries.append(entry)

    return entries


def parse_pmset(raw: str) -> dict[str, Any]:
    """Parse pmset -g output into structured power management data.

    Handles both 'pmset -g' (settings) and 'pmset -g assertions' formats.
    """
    result: dict[str, Any] = {
        "settings": {},
        "assertions": [],
    }

    in_assertions = False
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if "assertions" in stripped.lower() and ":" in stripped:
            in_assertions = True
            continue

        if in_assertions:
            if "pid" in stripped.lower() or "(" in stripped:
                result["assertions"].append(stripped)
            continue

        match = re.match(r"^(\w[\w\s]*?)\s+(\d+)$", stripped)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            result["settings"][key] = int(value)
            continue

        if "\t" in stripped:
            parts = stripped.split("\t")
            if len(parts) >= 2:
                result["settings"][parts[0].strip()] = parts[1].strip()

    return result


def parse_system_profiler(raw: str) -> dict[str, str]:
    """Parse system_profiler text output into key-value dict.

    Extracts hardware/software data type fields.
    """
    result: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                result[key] = value
    return result


def parse_nvram(raw: str) -> dict[str, str]:
    """Parse nvram -p output into key-value dict.

    Format: key\tvalue
    """
    result: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if "\t" in stripped:
            key, value = stripped.split("\t", 1)
            result[key.strip()] = value.strip()
        elif " " in stripped:
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                result[parts[0]] = parts[1]
    return result


def gather(
    log_window: str = "5m",
    gather_log: bool = True,
    gather_profiler: bool = True,
    gather_launchctl: bool = True,
    gather_nvram: bool = True,
    gather_pmset: bool = True,
) -> dict[str, Any]:
    """Gather all macOS diagnostic data.

    Returns dict with keys: unified_log, system_profiler,
    launchctl, nvram, pmset.
    """
    result: dict[str, Any] = {
        "unified_log": [],
        "system_profiler": {},
        "launchctl": [],
        "nvram": {},
        "pmset": {},
    }

    if gather_log:
        log_raw = _run(
            ["log", "show", "--last", log_window, "--style", "ndjson"],
            timeout=60,
        )
        result["unified_log"] = parse_unified_log(log_raw)

    if gather_profiler:
        profiler_raw = _run(
            [
                "system_profiler",
                "SPHardwareDataType",
                "SPSoftwareDataType",
                "SPMemoryDataType",
            ],
            timeout=30,
        )
        result["system_profiler"] = parse_system_profiler(profiler_raw)

    if gather_launchctl:
        launchctl_raw = _run(["launchctl", "list"])
        result["launchctl"] = parse_launchctl_list(launchctl_raw)

    if gather_nvram:
        nvram_raw = _run(["nvram", "-p"])
        result["nvram"] = parse_nvram(nvram_raw)

    if gather_pmset:
        pmset_raw = _run(["pmset", "-g"]) + "\n" + _run(["pmset", "-g", "assertions"])
        result["pmset"] = parse_pmset(pmset_raw)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gather macOS diagnostics")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--log-window", default="5m", help="Log time window (e.g. 5m, 1h)")
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--no-profiler", action="store_true")
    parser.add_argument("--no-launchctl", action="store_true")
    parser.add_argument("--no-nvram", action="store_true")
    parser.add_argument("--no-pmset", action="store_true")
    args = parser.parse_args()

    data = gather(
        log_window=args.log_window,
        gather_log=not args.no_log,
        gather_profiler=not args.no_profiler,
        gather_launchctl=not args.no_launchctl,
        gather_nvram=not args.no_nvram,
        gather_pmset=not args.no_pmset,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
