#!/usr/bin/env python3
"""Linux automation auditor — linux_automation role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
collect systemd timer, cron, logrotate, and unattended-upgrades state.
Produces a single JSON artifact with structured representations.

Usage:
    python3 linux_automation_audit.py --output /tmp/audit.json
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


def parse_systemctl_list_timers(raw: str) -> list[dict[str, str]]:
    """Parse 'systemctl list-timers --all' output into structured timer dicts.

    Handles variable-width columns by anchoring on the UNIT and ACTIVATES
    columns which are the last two space-separated tokens. systemd timer
    units always end in '.timer' and activated services in '.service'.
    """
    timers: list[dict[str, str]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return timers

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("N timers") or "lists" in stripped:
            continue
        parts = stripped.split()
        if len(parts) < 6:
            continue
        unit = parts[-2]
        activates = parts[-1]
        if not unit.endswith(".timer") and not unit.endswith(".target"):
            continue
        timer: dict[str, str] = {
            "unit": unit,
            "activates": activates,
            "raw": stripped,
        }
        timers.append(timer)
    return timers


def parse_systemctl_list_timers_wide(raw: str) -> list[dict[str, str]]:
    """Parse systemctl list-timers when columns are space-separated wide format.

    Handles the case where NEXT is a single column like 'Wed 2026-07-15'.
    """
    timers: list[dict[str, str]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return timers

    header_seen = False
    for line in lines:
        stripped = line.strip()
        if not header_seen:
            if "NEXT" in stripped and "UNIT" in stripped:
                header_seen = True
            continue
        if not stripped or "timers listed" in stripped:
            continue
        parts = stripped.split()
        if len(parts) < 6:
            continue
        timer: dict[str, str] = {
            "unit": parts[-2] if len(parts) > 6 else parts[4],
            "activates": parts[-1],
            "raw": stripped,
        }
        timers.append(timer)
    return timers


def parse_crontab(raw: str) -> list[dict[str, str]]:
    """Parse crontab file content into structured entries.

    Skips comments (#) and blank lines. Each entry has: minute, hour,
    day, month, weekday, command.
    """
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped and not re.match(
            r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+", stripped
        ):
            continue
        parts = stripped.split()
        if len(parts) < 6:
            continue
        entries.append({
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "weekday": parts[4],
            "command": " ".join(parts[5:]),
        })
    return entries


def parse_cron_directory_listing(raw: str) -> dict[str, list[str]]:
    """Parse a cron directory listing (ls output) into dir → files map.

    The raw output should be prefixed with === dir === markers.
    """
    result: dict[str, list[str]] = {}
    current_dir = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            current_dir = stripped[4:-4].strip()
            result[current_dir] = []
        elif current_dir and stripped and not stripped.startswith("(no"):
            parts = stripped.split()
            for part in parts:
                if not part.startswith("total") and "." in part or "-" in part:
                    result[current_dir].append(part)
    return result


def parse_logrotate_config(raw: str) -> list[dict[str, Any]]:
    """Parse logrotate configuration into list of stanza dicts.

    Each stanza: { paths: [...], directives: {...} }.
    """
    stanzas: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_stanza = False

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "{" in stripped and "}" not in stripped:
            paths_str = stripped.replace("{", "").strip()
            current = {
                "paths": [p.strip() for p in paths_str.split() if p.strip()],
                "directives": {},
            }
            in_stanza = True
        elif stripped == "}":
            if current is not None:
                stanzas.append(current)
            current = None
            in_stanza = False
        elif in_stanza and current is not None:
            parts = stripped.split()
            if parts:
                key = parts[0]
                value = " ".join(parts[1:]) if len(parts) > 1 else "true"
                current["directives"][key] = value
    return stanzas


def parse_unattended_config(raw: str) -> dict[str, Any]:
    """Parse unattended-upgrades configuration into structured dict.

    Handles both APT conf format (key "value";) and INI format.
    """
    result: dict[str, Any] = {
        "format": "unknown",
        "settings": {},
    }
    lines = raw.splitlines()

    apt_pattern = re.compile(r'^\s*([A-Za-z][\w\-/]*?)::(\S+)\s+"([^"]*)"\s*;')
    ini_section = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        apt_match = apt_pattern.match(stripped)
        if apt_match:
            result["format"] = "apt"
            key = f"{apt_match.group(1)}::{apt_match.group(2)}"
            result["settings"][key] = apt_match.group(3)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            result["format"] = "ini"
            ini_section = stripped[1:-1]
            continue
        ini_match = re.match(r"^(\S+)\s*=\s*(.+)$", stripped)
        if ini_match and ini_section:
            result["settings"][f"{ini_section}.{ini_match.group(1)}"] = ini_match.group(2).strip()
    return result


def audit(
    audit_systemd: bool = True,
    audit_cron: bool = True,
    audit_logrotate: bool = True,
    audit_unattended: bool = True,
) -> dict[str, Any]:
    """Audit all Linux automation subsystems.

    Returns dict with keys: systemd_timers, crontab, cron_directories,
    logrotate, unattended_config.
    """
    result: dict[str, Any] = {
        "systemd_timers": [],
        "crontab": [],
        "cron_directories": {},
        "logrotate": [],
        "unattended_config": {},
    }

    if audit_systemd:
        raw = _run(["systemctl", "list-timers", "--all", "--no-pager"])
        result["systemd_timers"] = parse_systemctl_list_timers(raw)

    if audit_cron:
        crontab_raw = _read_file("/etc/crontab")
        result["crontab"] = parse_crontab(crontab_raw)

    if audit_logrotate:
        config_raw = _read_file("/etc/logrotate.conf")
        logrotate_dir = "/etc/logrotate.d"
        if os.path.isdir(logrotate_dir):
            for fname in sorted(os.listdir(logrotate_dir)):
                fpath = os.path.join(logrotate_dir, fname)
                if os.path.isfile(fpath):
                    config_raw += "\n" + _read_file(fpath)
        result["logrotate"] = parse_logrotate_config(config_raw)

    if audit_unattended:
        config_paths = [
            "/etc/apt/apt.conf.d/20auto-upgrades",
            "/etc/apt/apt.conf.d/50unattended-upgrades",
            "/etc/dnf/automatic.conf",
        ]
        combined = ""
        for path in config_paths:
            content = _read_file(path)
            if content:
                combined += f"\n=== {path} ===\n{content}\n"
        result["unattended_config"] = parse_unattended_config(combined)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Linux automation state")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--no-systemd", action="store_true")
    parser.add_argument("--no-cron", action="store_true")
    parser.add_argument("--no-logrotate", action="store_true")
    parser.add_argument("--no-unattended", action="store_true")
    args = parser.parse_args()

    data = audit(
        audit_systemd=not args.no_systemd,
        audit_cron=not args.no_cron,
        audit_logrotate=not args.no_logrotate,
        audit_unattended=not args.no_unattended,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
