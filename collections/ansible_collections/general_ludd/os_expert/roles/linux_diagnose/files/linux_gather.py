#!/usr/bin/env python3
"""Linux diagnostic gatherer — linux_diagnose role backend.

Self-contained (stdlib only) gatherer invoked by the ansible role to
collect /proc/*, sysfs, dmesg, lsmod, and sysctl data from a Linux host.
Produces a single JSON artifact with structured representations.

Usage:
    python3 linux_gather.py --output /tmp/artifact.json
    python3 linux_gather.py --output /tmp/artifact.json --dmesg-lines 500
"""

from __future__ import annotations

import argparse
import json
import os
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


def _read_file(path: str) -> str:
    """Read a file, returning empty string on any error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def parse_proc_cpuinfo(raw: str) -> dict[str, Any]:
    """Parse /proc/cpuinfo into structured CPU data.

    Returns dict with processor count, model name, cores, and flags.
    """
    result: dict[str, Any] = {
        "processor_count": 0,
        "model_name": "",
        "cores_per_socket": 0,
        "sockets": 0,
        "flags": [],
    }
    processors: list[dict[str, str]] = []

    for block in raw.strip().split("\n\n"):
        entry: dict[str, str] = {}
        for line in block.splitlines():
            stripped = line.strip()
            if ": " in stripped:
                key, value = stripped.split(": ", 1)
                entry[key.strip()] = value.strip()
        if entry:
            processors.append(entry)

    result["processor_count"] = len(processors)
    if processors:
        first = processors[0]
        result["model_name"] = first.get("model name", "")
        result["cores_per_socket"] = int(first.get("cpu cores", "0") or "0")
        result["sockets"] = int(first.get("physical id", "0") or "0") + 1
        flags_str = first.get("flags", "")
        result["flags"] = flags_str.split() if flags_str else []

    return result


def parse_proc_meminfo(raw: str) -> dict[str, int]:
    """Parse /proc/meminfo into key-value dict with byte values."""
    result: dict[str, int] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        match = re.match(r"^(\w+):\s+(\d+)", stripped)
        if match:
            result[match.group(1)] = int(match.group(2))
    return result


def parse_proc_version(raw: str) -> dict[str, str]:
    """Parse /proc/version into kernel version components."""
    result: dict[str, str] = {
        "raw": raw.strip(),
        "kernel_version": "",
        "compiler": "",
    }
    match = re.match(
        r"Linux version (\S+)\s+\(([^)]+)\)\s+(.*)", raw.strip()
    )
    if match:
        result["kernel_version"] = match.group(1)
        result["compiler"] = match.group(3)
    return result


def parse_lsmod(raw: str) -> list[dict[str, Any]]:
    """Parse lsmod output into list of module dicts."""
    modules: list[dict[str, Any]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return modules

    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        modules.append({
            "module": parts[0],
            "size": int(parts[1]) if parts[1].isdigit() else 0,
            "used_by": parts[2].split(",") if parts[2] != "0" else [],
        })
    return modules


def parse_df(raw: str) -> list[dict[str, str]]:
    """Parse df -h output into list of filesystem dicts."""
    filesystems: list[dict[str, str]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return filesystems

    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        filesystems.append({
            "filesystem": parts[0],
            "size": parts[1],
            "used": parts[2],
            "available": parts[3],
            "use_percent": parts[4],
            "mount": parts[5],
        })
    return filesystems


def parse_sysctl(raw: str) -> dict[str, str]:
    """Parse sysctl -a output into key-value dict."""
    result: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if " = " in stripped:
            key, value = stripped.split(" = ", 1)
            result[key.strip()] = value.strip()
    return result


def parse_dmesg(raw: str) -> list[dict[str, str]]:
    """Parse dmesg output into structured entries.

    Format: [timestamp] subsystem: message
    """
    entries: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        entry: dict[str, str] = {"raw": stripped}
        ts_match = re.match(r"\[\s*([\d.]+)\]\s*(.*)", stripped)
        if ts_match:
            entry["timestamp"] = ts_match.group(1)
            rest = ts_match.group(2)
        else:
            rest = stripped
        if ": " in rest:
            subsystem, message = rest.split(": ", 1)
            entry["subsystem"] = subsystem
            entry["message"] = message
        else:
            entry["message"] = rest
        entries.append(entry)
    return entries


def gather(
    proc: bool = True,
    sysfs: bool = True,
    dmesg_lines: int = 500,
    gather_lsmod: bool = True,
    gather_sysctl: bool = True,
) -> dict[str, Any]:
    """Gather all Linux diagnostic data.

    Returns dict with keys: cpuinfo, meminfo, version, lsmod, df,
    dmesg, sysctl.
    """
    result: dict[str, Any] = {
        "cpuinfo": {},
        "meminfo": {},
        "version": {},
        "lsmod": [],
        "df": [],
        "dmesg": [],
        "sysctl": {},
    }

    if proc:
        result["cpuinfo"] = parse_proc_cpuinfo(_read_file("/proc/cpuinfo"))
        result["meminfo"] = parse_proc_meminfo(_read_file("/proc/meminfo"))
        result["version"] = parse_proc_version(_read_file("/proc/version"))

    if sysfs:
        lsblk_raw = _run(["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE", "--json"])
        result["lsblk"] = lsblk_raw if lsblk_raw else ""
        result["df"] = parse_df(_run(["df", "-h"]))

    if gather_lsmod:
        result["lsmod"] = parse_lsmod(_run(["lsmod"]))

    dmesg_raw = _run(["dmesg", "-T"])
    if dmesg_raw:
        all_dmesg = parse_dmesg(dmesg_raw)
        result["dmesg"] = all_dmesg[-dmesg_lines:] if dmesg_lines > 0 else all_dmesg

    if gather_sysctl:
        result["sysctl"] = parse_sysctl(_run(["sysctl", "-a"]))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gather Linux diagnostics")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--dmesg-lines", type=int, default=500)
    parser.add_argument("--no-proc", action="store_true")
    parser.add_argument("--no-sysfs", action="store_true")
    parser.add_argument("--no-lsmod", action="store_true")
    parser.add_argument("--no-sysctl", action="store_true")
    args = parser.parse_args()

    data = gather(
        proc=not args.no_proc,
        sysfs=not args.no_sysfs,
        gather_lsmod=not args.no_lsmod,
        gather_sysctl=not args.no_sysctl,
        dmesg_lines=args.dmesg_lines,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
