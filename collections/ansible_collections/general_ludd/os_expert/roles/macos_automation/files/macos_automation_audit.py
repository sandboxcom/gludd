#!/usr/bin/env python3
"""macOS automation auditor — macos_automation role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
collect launchd, Homebrew, defaults, softwareupdate, and configuration
profile state from a macOS host. Produces a single JSON artifact.

Usage:
    python3 macos_automation_audit.py --output /tmp/audit.json
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


def _read_file(path: str) -> str:
    """Read a file, returning empty string on any error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def parse_launchctl_list(raw: str) -> list[dict[str, Any]]:
    """Parse 'launchctl list' output into structured service entries.

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
            "running": False,
        }
        try:
            entry["pid"] = int(pid_str) if pid_str != "-" else 0
            entry["running"] = entry["pid"] > 0
        except ValueError:
            pass
        try:
            entry["status"] = int(status_str) if status_str != "-" else 0
        except ValueError:
            pass
        entries.append(entry)
    return entries


def parse_launchctl_print(raw: str) -> dict[str, Any]:
    """Parse 'launchctl print' output into structured dict.

    Extracts key services and their states from the nested output.
    """
    result: dict[str, Any] = {
        "services": [],
        "state": "",
    }
    current_section = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("services") or stripped.endswith("= {"):
            current_section = stripped
            continue
        match = re.match(r"^([\w\.\-]+)\s*=\s*(.+)$", stripped)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            if current_section and key not in ("state",):
                result["services"].append({
                    "name": key,
                    "state": value,
                })
            if key == "state":
                result["state"] = value
    return result


def parse_brew_list(raw: str) -> list[dict[str, str]]:
    """Parse 'brew list --versions' output into package dicts.

    Format: name version1 version2 ...
    """
    packages: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if not parts:
            continue
        entry: dict[str, str] = {
            "name": parts[0],
            "version": parts[1] if len(parts) > 1 else "",
            "versions": " ".join(parts[1:]) if len(parts) > 1 else "",
        }
        packages.append(entry)
    return packages


def parse_brew_outdated(raw: str) -> list[dict[str, str]]:
    """Parse 'brew outdated' output into list of outdated package names."""
    result: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        result.append({"name": stripped})
    return result


def parse_brew_taps(raw: str) -> list[str]:
    """Parse 'brew tap' output into list of tap names."""
    result: list[str] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if stripped:
            result.append(stripped)
    return result


def parse_brew_casks(raw: str) -> list[str]:
    """Parse 'brew list --casks' output into list of cask names."""
    result: list[str] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if stripped:
            result.append(stripped)
    return result


def parse_defaults(raw: str) -> dict[str, Any]:
    """Parse 'defaults read' output into structured dict.

    Handles the plist-style output with nested braces.
    """
    result: dict[str, Any] = {"raw": raw.strip(), "keys": {}}
    for line in raw.splitlines():
        stripped = line.strip()
        match = re.match(r"^\s*([\w\-\.]+)\s*=\s*(.+);$", stripped)
        if match:
            key = match.group(1)
            value = match.group(2).strip().strip('"').strip(";")
            result["keys"][key] = value
    return result


def parse_softwareupdate_list(raw: str) -> list[dict[str, str]]:
    """Parse 'softwareupdate --list' output into update entries."""
    updates: list[dict[str, str]] = []
    in_updates = False
    for line in raw.splitlines():
        stripped = line.strip()
        if "Software Update Tool" in stripped:
            continue
        if "Finding available software" in stripped:
            continue
        match = re.match(r"^\s*\*\s*Label:\s*(.+)", stripped)
        if match:
            label = match.group(1).strip()
            updates.append({"label": label, "recommended": True})
            continue
        match = re.match(r"^\*\s+(.+)", stripped)
        if match:
            updates.append({"label": match.group(1).strip(), "recommended": False})
            continue
        title_match = re.match(r'^Title:\s*(.+),\s*Version:\s*(.+)', stripped)
        if title_match and updates:
            updates[-1]["title"] = title_match.group(1).strip()
            updates[-1]["version"] = title_match.group(2).strip()
    return updates


def parse_softwareupdate_history(raw: str) -> list[dict[str, str]]:
    """Parse 'softwareupdate --history' output into install history entries."""
    entries: list[dict[str, str]] = []
    in_history = False
    for line in raw.splitlines():
        stripped = line.strip()
        if "Display Name" in stripped and "Version" in stripped:
            in_history = True
            continue
        if not in_history or not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 4:
            entries.append({
                "display_name": " ".join(parts[:-3]),
                "version": parts[-3],
                "date": " ".join(parts[-2:]),
            })
    return entries


def parse_profiles_list(raw: str) -> list[dict[str, str]]:
    """Parse 'profiles list' output into profile entries."""
    profiles: list[dict[str, str]] = []
    lines = raw.strip().splitlines()
    in_profiles = False
    for line in lines:
        stripped = line.strip()
        if "_computerlevel" in stripped or "identifier" in stripped.lower():
            in_profiles = True
            continue
        if not in_profiles or not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            profiles.append({
                "identifier": parts[0],
                "status": parts[1] if len(parts) > 1 else "",
            })
    return profiles


def parse_profiles_status(raw: str) -> dict[str, str]:
    """Parse 'profiles status -type enrollment' output into status dict."""
    result: dict[str, str] = {"enrollment_status": "", "raw": raw.strip()}
    for line in raw.splitlines():
        stripped = line.strip()
        if "Enrolled" in stripped or "Not Enrolled" in stripped:
            result["enrollment_status"] = stripped.split(":")[-1].strip()
    return result


def audit(
    audit_launchd: bool = True,
    audit_homebrew: bool = True,
    audit_defaults: bool = True,
    audit_softwareupdate: bool = True,
    audit_profiles: bool = True,
) -> dict[str, Any]:
    """Audit all macOS automation subsystems.

    Returns dict with keys: launchd, homebrew, defaults, softwareupdate, profiles.
    """
    result: dict[str, Any] = {
        "launchd": {"services": [], "print_system": {}, "print_user": {}},
        "homebrew": {
            "installed": [],
            "outdated": [],
            "taps": [],
            "casks": [],
            "version": "",
        },
        "defaults": {},
        "softwareupdate": {"updates": [], "history": []},
        "profiles": {"profiles": [], "enrollment": {}},
    }

    if audit_launchd:
        result["launchd"]["services"] = parse_launchctl_list(
            _run(["launchctl", "list"])
        )
        result["launchd"]["print_system"] = parse_launchctl_print(
            _run(["launchctl", "print", "system"])
        )

    if audit_homebrew:
        result["homebrew"]["version"] = _run(["brew", "--version"]).splitlines()[0] if _run(["brew", "--version"]) else ""
        result["homebrew"]["installed"] = parse_brew_list(
            _run(["brew", "list", "--versions"])
        )
        result["homebrew"]["outdated"] = parse_brew_outdated(
            _run(["brew", "outdated"])
        )
        result["homebrew"]["taps"] = parse_brew_taps(_run(["brew", "tap"]))
        result["homebrew"]["casks"] = parse_brew_casks(
            _run(["brew", "list", "--casks"])
        )

    if audit_defaults:
        result["defaults"]["nsglobaldomain"] = parse_defaults(
            _run(["defaults", "read", "NSGlobalDomain"])
        )

    if audit_softwareupdate:
        result["softwareupdate"]["updates"] = parse_softwareupdate_list(
            _run(["softwareupdate", "--list"])
        )
        result["softwareupdate"]["history"] = parse_softwareupdate_history(
            _run(["softwareupdate", "--history"])
        )

    if audit_profiles:
        result["profiles"]["profiles"] = parse_profiles_list(
            _run(["profiles", "list"])
        )
        result["profiles"]["enrollment"] = parse_profiles_status(
            _run(["profiles", "status", "-type", "enrollment"])
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit macOS automation state")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--no-launchd", action="store_true")
    parser.add_argument("--no-homebrew", action="store_true")
    parser.add_argument("--no-defaults", action="store_true")
    parser.add_argument("--no-softwareupdate", action="store_true")
    parser.add_argument("--no-profiles", action="store_true")
    args = parser.parse_args()

    data = audit(
        audit_launchd=not args.no_launchd,
        audit_homebrew=not args.no_homebrew,
        audit_defaults=not args.no_defaults,
        audit_softwareupdate=not args.no_softwareupdate,
        audit_profiles=not args.no_profiles,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
