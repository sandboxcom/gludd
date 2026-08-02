#!/usr/bin/env python3
"""iOS security auditor — ios_security role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
audit iOS security posture: AMFI/trustcache status, sandbox profiles,
and code-sign integrity. Produces a single JSON artifact.

Usage:
    python3 ios_security_audit.py --udid UDID --output /tmp/audit.json
    python3 ios_security_audit.py --output /tmp/audit.json
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


def _run_idevice(base_cmd: list[str], udid: str | None = None) -> str:
    """Run an idevice* command with optional UDID selector."""
    cmd: list[str] = list(base_cmd)
    if udid:
        cmd.extend(["-u", udid])
    return _run(cmd)


def parse_amfi_status(raw: str) -> dict[str, Any]:
    """Parse AMFI-related ideviceinfo output into structured dict.

    Looks for AppleMobileFileIntegrity properties: trust cache state,
    enforcement mode, and developer mode status.
    """
    result: dict[str, Any] = {
        "enforcing": False,
        "developer_mode": False,
        "properties": {},
    }

    for line in raw.strip().splitlines():
        stripped = line.strip()
        if ": " not in stripped:
            continue
        key, value = stripped.split(": ", 1)
        key = key.strip()
        value = value.strip()
        result["properties"][key] = value

        lower_key = key.lower()
        if "enforce" in lower_key and value.lower() in ("true", "1", "yes"):
            result["enforcing"] = True
        if "developer" in lower_key and "mode" in lower_key:
            if value.lower() in ("true", "1", "yes", "enabled"):
                result["developer_mode"] = True

    return result


def parse_sandbox_profiles(raw: str) -> dict[str, Any]:
    """Parse sandbox profile listing from ideviceinfo or container dump.

    Returns dict with container count, sandbox violation indicators,
    and profile listing.
    """
    result: dict[str, Any] = {
        "container_count": 0,
        "profiles": [],
        "violations_detected": False,
    }

    profiles: list[str] = []
    container_count = 0
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Container:"):
            container_count += 1
            profiles.append(stripped)
        elif stripped.startswith("BundleID:"):
            profiles.append(stripped)
        if "violation" in stripped.lower() or "denied" in stripped.lower():
            result["violations_detected"] = True

    result["container_count"] = container_count
    result["profiles"] = profiles
    return result


def parse_codesign_status(raw: str) -> dict[str, Any]:
    """Parse code-signing certificate and signature validity info.

    Extracts cdhash, team ID, certificate validity, and signature status.
    """
    result: dict[str, Any] = {
        "cdhash": "",
        "team_id": "",
        "valid": False,
        "certificate_info": {},
    }

    cdhash_match = re.search(r"CDHash[:\s]+([0-9a-fA-F]{40})", raw)
    if cdhash_match:
        result["cdhash"] = cdhash_match.group(1)

    team_match = re.search(r"TeamIdentifier[:\s]+([A-Z0-9]+)", raw)
    if team_match:
        result["team_id"] = team_match.group(1)

    if re.search(r"Signature[:\s]+valid", raw, re.IGNORECASE) or re.search(r"CodeDirectory[:\s]+", raw):
        result["valid"] = True

    for line in raw.strip().splitlines():
        stripped = line.strip()
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            lower_key = key.lower()
            if any(
                term in lower_key
                for term in ("cert", "authority", "subject", "serial", "notafter")
            ):
                result["certificate_info"][key.strip()] = value.strip()

    return result


def parse_trustcache_status(raw: str) -> dict[str, Any]:
    """Parse trust cache validation output.

    Trust caches are iOS's mechanism for validating binary integrity.
    """
    result: dict[str, Any] = {
        "loaded": False,
        "entries": 0,
        "version": "",
    }

    if "loaded" in raw.lower() or "trust" in raw.lower():
        result["loaded"] = True

    count_match = re.search(r"(?:entries|count)[:\s]+(\d+)", raw, re.IGNORECASE)
    if count_match:
        result["entries"] = int(count_match.group(1))

    version_match = re.search(r"(?:version|v)[:\s]+([\d.]+)", raw, re.IGNORECASE)
    if version_match:
        result["version"] = version_match.group(1)

    return result


def audit(udid: str | None = None) -> dict[str, Any]:
    """Run full iOS security audit via libimobiledevice.

    Returns dict with keys: amfi, sandbox, codesign, trustcache, device_udids.
    """
    result: dict[str, Any] = {
        "amfi": {},
        "sandbox": {},
        "codesign": {},
        "trustcache": {},
        "device_udids": [],
        "udid": udid or "auto",
    }

    amfi_raw = _run_idevice(["ideviceinfo", "-q", "AMFI"], udid=udid)
    if not amfi_raw:
        amfi_raw = _run_idevice(["ideviceinfo"], udid=udid)
    result["amfi"] = parse_amfi_status(amfi_raw)

    sandbox_raw = _run_idevice(
        ["ideviceinfo", "-q", "com.apple.mobile.iTunes"],
        udid=udid,
    )
    if not sandbox_raw:
        sandbox_raw = _run_idevice(
            ["ideviceinfo", "-k", "ContainerTotal"],
            udid=udid,
        )
    result["sandbox"] = parse_sandbox_profiles(sandbox_raw)

    codesign_raw = _run_idevice(
        ["ideviceinfo", "-k", "DeviceCertificate"],
        udid=udid,
    )
    if not codesign_raw:
        codesign_raw = _run_idevice(["ideviceinfo", "-q", "Lockdown"], udid=udid)
    result["codesign"] = parse_codesign_status(codesign_raw)

    trustcache_raw = _run_idevice(
        ["ideviceinfo", "-k", "BasebandCertId"],
        udid=udid,
    )
    if not trustcache_raw:
        trustcache_raw = amfi_raw
    result["trustcache"] = parse_trustcache_status(trustcache_raw)

    udid_raw = _run(["idevice_id", "-l"])
    result["device_udids"] = [
        u.strip() for u in udid_raw.strip().splitlines() if u.strip()
    ]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit iOS security")
    parser.add_argument("--udid", default=None, help="iOS device UDID (-u)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    data = audit(udid=args.udid)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote audit to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
