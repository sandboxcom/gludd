#!/usr/bin/env python3
"""Android security auditor — android_security role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
audit Android security posture: SELinux/SEPolicy, package permissions,
keystore status, and dm-verity. Produces a single JSON artifact.

Usage:
    python3 android_security_audit.py --serial SERIAL --output /tmp/audit.json
"""

from __future__ import annotations

import argparse
import json
import re
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


def parse_sepolicy(raw: str) -> list[dict[str, str]]:
    """Parse SEpolicy rule output (from sesearch or sepolicy-inject).

    Extracts allow/neverallow/deny rules.
    """
    rules: list[dict[str, str]] = []
    rule_re = re.compile(
        r"^(allow|neverallow|deny|auditallow|dontaudit)\s+", re.MULTILINE
    )
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if rule_re.match(stripped):
            rules.append({"type": stripped.split()[0], "raw": stripped})
    return rules


def parse_dumpsys_package_permissions(
    raw: str,
) -> dict[str, dict[str, Any]]:
    """Parse 'dumpsys package' output for per-package permissions.

    Returns dict keyed by package name with 'requested' and 'granted' lists.
    """
    result: dict[str, dict[str, Any]] = {}
    current_pkg: str | None = None
    section: str | None = None

    for line in raw.splitlines():
        stripped = line.strip()

        pkg_match = re.match(r"^Package\s+\[([^\]]+)\]", stripped)
        if pkg_match:
            current_pkg = pkg_match.group(1)
            result[current_pkg] = {"requested": [], "granted": []}
            continue

        if current_pkg is None:
            continue

        if "requested permissions:" in stripped.lower():
            section = "requested"
            continue
        if "install permissions:" in stripped.lower():
            section = "granted"
            continue

        if section == "requested" and stripped.startswith("android.permission."):
            result[current_pkg]["requested"].append(stripped)
        elif section == "granted":
            grant_match = re.match(
                r"^(android\.permission\.\w+):\s*granted=(\w+)", stripped
            )
            if grant_match and grant_match.group(2).lower() == "true":
                result[current_pkg]["granted"].append(grant_match.group(1))

    return result


def parse_keystore_status(raw: str) -> dict[str, Any]:
    """Parse keystore service status from dumpsys output."""
    result: dict[str, Any] = {}
    state_match = re.search(r"State:\s*(\w+)", raw)
    if state_match:
        result["state"] = state_match.group(1)

    auth_match = re.search(r"Auth bound:\s*(\w+)", raw, re.IGNORECASE)
    if auth_match:
        result["auth_bound"] = auth_match.group(1).lower() in ("true", "yes", "1")

    return result


def parse_verity_status(raw: str) -> dict[str, Any]:
    """Parse dm-verity status from adb shell output.

    Looks for 'Verity mode:' lines in verity metadata output.
    """
    result: dict[str, Any] = {}
    mode_match = re.search(r"Verity mode:\s*(\w+)", raw)
    if mode_match:
        result["mode"] = mode_match.group(1)

    if "ENFORCING" in raw.upper():
        result["enforcing"] = True
    elif "DISABLED" in raw.upper():
        result["enforcing"] = False

    return result


def audit(serial: str | None = None) -> dict[str, Any]:
    """Run full Android security audit via ADB.

    Returns dict with keys: sepolicy, permissions, keystore, verity.
    """
    result: dict[str, Any] = {
        "sepolicy": [],
        "permissions": {},
        "keystore": {},
        "verity": {},
        "serial": serial or "default",
    }

    sepolicy_raw = _run_adb(
        ["shell", "getenforce"], serial=serial
    ) + "\n" + _run_adb(
        ["shell", "ls", "/sys/fs/selinux/booleans/"], serial=serial
    )
    result["sepolicy"] = parse_sepolicy(sepolicy_raw)

    dumpsys_pkg = _run_adb(["shell", "dumpsys", "package"], serial=serial)
    result["permissions"] = parse_dumpsys_package_permissions(dumpsys_pkg)

    keystore_raw = _run_adb(
        ["shell", "dumpsys", "keystore"], serial=serial
    )
    result["keystore"] = parse_keystore_status(keystore_raw)

    verity_raw = _run_adb(
        ["shell", "getprop", "partition.system.verified"], serial=serial
    ) + "\n" + _run_adb(
        ["shell", "getprop", "ro.boot.veritymode"], serial=serial
    )
    result["verity"] = parse_verity_status(verity_raw)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Android security via ADB")
    parser.add_argument("--serial", default=None, help="ADB device serial (-s)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    data = audit(serial=args.serial)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote audit to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
