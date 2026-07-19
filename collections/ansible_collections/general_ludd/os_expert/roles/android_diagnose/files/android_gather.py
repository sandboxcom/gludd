#!/usr/bin/env python3
"""Android diagnostic gatherer — android_diagnose role backend.

Self-contained (stdlib only) gatherer invoked by the ansible role to
collect logcat, dumpsys, getprop, and pm list data from an Android device
via ADB. Produces a single JSON artifact.

Usage:
    python3 android_gather.py --serial SERIAL --output /tmp/artifact.json
    python3 android_gather.py --output /tmp/artifact.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def _run_adb(args: list[str], serial: str | None = None, timeout: int = 30) -> str:
    """Run an adb command and return stdout (empty string on failure)."""
    cmd: list[str] = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def parse_logcat(raw: str) -> list[dict[str, Any]]:
    """Parse logcat threadtime output into structured entries.

    Format: MM-DD HH:MM:SS.mmm PID TID LEVEL TAG: MESSAGE
    """
    entries: list[dict[str, Any]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 6)
        if len(parts) < 7:
            continue
        tag_field = parts[5]
        if tag_field.endswith(":"):
            tag_field = tag_field[:-1]
        try:
            entries.append({
                "timestamp": f"{parts[0]} {parts[1]}",
                "pid": int(parts[2]),
                "tid": int(parts[3]),
                "level": parts[4],
                "tag": tag_field,
                "message": parts[6],
            })
        except (ValueError, IndexError):
            continue
    return entries


def parse_getprop(raw: str) -> dict[str, str]:
    """Parse getprop output into key-value dict.

    Format: [key]: [value]
    """
    props: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped.startswith("[") or "]: [" not in stripped:
            continue
        try:
            key_part, value_part = stripped[1:].split("]: [", 1)
            props[key_part.strip()] = value_part.rstrip("]").strip()
        except (ValueError, IndexError):
            continue
    return props


def parse_pm_list(raw: str) -> list[str]:
    """Parse 'pm list packages' output into list of package names."""
    pkgs: list[str] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("package:"):
            pkgs.append(stripped[len("package:"):])
    return pkgs


def parse_dumpsys(raw: str) -> str:
    """Return dumpsys output as-is (freeform text per service)."""
    return raw.strip()


def gather(
    serial: str | None = None,
    logcat_lines: int = 200,
    dumpsys_services: list[str] | None = None,
) -> dict[str, Any]:
    """Gather all Android diagnostic data via ADB.

    Returns a dict with keys: logcat, getprop, packages, dumpsys.
    Each value is structured JSON-compatible data.
    """
    services = dumpsys_services or ["meminfo", "activity", "package"]
    result: dict[str, Any] = {
        "logcat": [],
        "getprop": {},
        "packages": [],
        "dumpsys": {},
        "serial": serial or "default",
    }

    logcat_raw = _run_adb(
        ["logcat", "-d", "-t", str(logcat_lines), "-v", "threadtime"],
        serial=serial,
    )
    result["logcat"] = parse_logcat(logcat_raw)

    getprop_raw = _run_adb(["shell", "getprop"], serial=serial)
    result["getprop"] = parse_getprop(getprop_raw)

    pkgs_raw = _run_adb(["shell", "pm", "list", "packages", "-3"], serial=serial)
    result["packages"] = parse_pm_list(pkgs_raw)

    for svc in services:
        out = _run_adb(["shell", "dumpsys", svc], serial=serial)
        result["dumpsys"][svc] = parse_dumpsys(out)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gather Android diagnostics via ADB")
    parser.add_argument("--serial", default=None, help="ADB device serial (-s)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--logcat-lines", type=int, default=200)
    args = parser.parse_args()

    data = gather(serial=args.serial, logcat_lines=args.logcat_lines)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
