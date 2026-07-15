#!/usr/bin/env python3
"""iOS diagnostic gatherer — ios_diagnose role backend.

Self-contained (stdlib only) gatherer invoked by the ansible role to
collect ideviceinfo, idevicesyslog, idevicediagnostics, and oslog data
from an iOS device via libimobiledevice. Produces a single JSON artifact.

Usage:
    python3 ios_gather.py --udid UDID --output /tmp/artifact.json
    python3 ios_gather.py --output /tmp/artifact.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


def _run(args: list[str], timeout: int = 30) -> str:
    """Run a command and return stdout (empty string on failure)."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def parse_ideviceinfo(raw: str) -> dict[str, str]:
    """Parse ideviceinfo output into key-value dict.

    Format: Key: Value
    """
    info: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if ": " not in stripped:
            continue
        key, value = stripped.split(": ", 1)
        info[key.strip()] = value.strip()
    return info


def parse_idevicesyslog(raw: str) -> list[dict[str, str]]:
    """Parse idevicesyslog output into structured entries.

    Format: Mon DD HH:MM:SS Hostname Process[PID]: Message
    """
    entries: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 5)
        if len(parts) < 6:
            continue
        process_field = parts[4]
        process_name = process_field
        if "[" in process_name:
            process_name = process_name.split("[")[0]
        message = parts[5]
        if message.startswith("<"):
            if ": " in message:
                message = message.split(": ", 1)[1]
        entries.append({
            "timestamp": f"{parts[0]} {parts[1]} {parts[2]}",
            "hostname": parts[3],
            "process": process_name,
            "message": message,
        })
    return entries


def parse_idevicediagnostics(raw: str) -> dict[str, Any]:
    """Parse idevicediagnostics output into structured dict."""
    if not raw.strip():
        return {}
    result: dict[str, Any] = {"diagnostics": raw.strip(), "diagnostics_type": "Unknown"}

    type_match = re.search(r"DiagnosticsType:\s*(\w+)", raw)
    if type_match:
        result["diagnostics_type"] = type_match.group(1)

    cycle_match = re.search(r"CycleCount:\s*(\d+)", raw)
    if cycle_match:
        result["cycle_count"] = int(cycle_match.group(1))

    return result


def gather(
    udid: str | None = None,
    syslog_lines: int = 100,
) -> dict[str, Any]:
    """Gather all iOS diagnostic data via libimobiledevice.

    Returns a dict with keys: device_info, syslog, diagnostics, device_udids.
    """
    result: dict[str, Any] = {
        "device_info": {},
        "syslog": [],
        "diagnostics": {},
        "device_udids": [],
        "udid": udid or "auto",
    }

    ideviceinfo_args = ["ideviceinfo"]
    if udid:
        ideviceinfo_args.extend(["-u", udid])
    info_raw = _run(ideviceinfo_args)
    result["device_info"] = parse_ideviceinfo(info_raw)

    syslog_args = ["idevicesyslog"]
    if udid:
        syslog_args.extend(["-u", udid])
    syslog_raw = _run(syslog_args)
    all_syslog = parse_idevicesyslog(syslog_raw)
    result["syslog"] = all_syslog[:syslog_lines]

    diag_args = ["idevicediagnostics", "diagnostics", "All"]
    if udid:
        diag_args.extend(["-u", udid])
    diag_raw = _run(diag_args)
    result["diagnostics"] = parse_idevicediagnostics(diag_raw)

    udid_raw = _run(["idevice_id", "-l"])
    result["device_udids"] = [
        u.strip() for u in udid_raw.strip().splitlines() if u.strip()
    ]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gather iOS diagnostics")
    parser.add_argument("--udid", default=None, help="iOS device UDID (-u)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--syslog-lines", type=int, default=100)
    args = parser.parse_args()

    data = gather(udid=args.udid, syslog_lines=args.syslog_lines)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
