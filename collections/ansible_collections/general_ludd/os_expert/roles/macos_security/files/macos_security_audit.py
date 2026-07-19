#!/usr/bin/env python3
"""macOS security auditor — macos_security role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
collect csrutil (SIP), spctl (Gatekeeper), XProtect/MRT, tccutil (TCC),
and managed plist policy state from a macOS host. Produces a single
JSON artifact.

Usage:
    python3 macos_security_audit.py --output /tmp/audit.json
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


def parse_csrutil_status(raw: str) -> dict[str, Any]:
    """Parse 'csrutil status' output into structured dict.

    Example output:
        System Integrity Protection status: enabled.
        Configuration: Apple Internal, Developer Tools, Root Leak Detection
    """
    result: dict[str, Any] = {
        "sip_enabled": False,
        "config": [],
        "raw": raw.strip(),
    }
    for line in raw.splitlines():
        stripped = line.strip()
        if "System Integrity Protection status:" in stripped:
            status_part = stripped.split(":", 1)[1].strip().rstrip(".")
            result["sip_enabled"] = "enabled" in status_part.lower()
        elif stripped.startswith("Configuration:"):
            config_part = stripped.split(":", 1)[1].strip()
            result["config"] = [c.strip() for c in config_part.split(",") if c.strip()]
    return result


def parse_spctl_status(raw: str) -> dict[str, Any]:
    """Parse 'spctl --status' output into structured dict.

    Example:
        assessments enabled
        or
        assessments disabled
    """
    result: dict[str, Any] = {
        "assessments_enabled": False,
        "gatekeeper_active": False,
        "raw": raw.strip(),
    }
    for line in raw.splitlines():
        stripped = line.strip()
        if "assessments" in stripped:
            result["assessments_enabled"] = "enabled" in stripped.lower()
            result["gatekeeper_active"] = "enabled" in stripped.lower()
    return result


def parse_spctl_assess(raw: str) -> list[dict[str, str]]:
    """Parse 'spctl --assess -v' output into assessment results."""
    results: list[dict[str, str]] = []
    current_path = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("/"):
            current_path = stripped.split(":")[0].strip()
            verdict = ""
            if ":" in stripped:
                verdict = stripped.split(":", 1)[1].strip()
            elif "accepted" in stripped.lower():
                verdict = "accepted"
            results.append({"path": current_path, "verdict": verdict})
        elif "accepted" in stripped.lower() and current_path:
            if results:
                results[-1]["verdict"] = "accepted"
        elif "rejected" in stripped.lower() and current_path:
            if results:
                results[-1]["verdict"] = "rejected"
    return results


def parse_xprotect(raw: str) -> dict[str, Any]:
    """Parse XProtect.meta.plist defaults output into structured dict.

    Extracts version info and threat definitions.
    """
    result: dict[str, Any] = {
        "version": "",
        "threats": [],
        "raw": raw.strip(),
    }
    in_threats = False
    for line in raw.splitlines():
        stripped = line.strip()
        version_match = re.match(r"^\s*version\s*=\s*(\d+)\s*;", stripped)
        if version_match:
            result["version"] = version_match.group(1)
        threat_match = re.match(r"^\s*([\w\-]+)\s*=\s*\{", stripped)
        if threat_match:
            in_threats = True
            result["threats"].append({"name": threat_match.group(1)})
        elif stripped == "}":
            in_threats = False
    return result


def parse_tccutil(raw: str) -> dict[str, list[dict[str, str]]]:
    """Parse tccutil list output for multiple services.

    The role iterates Camera, Microphone, Accessibility, FullDiskAccess.
    The raw output combines all sections with '=== tccutil list <Service> ===' markers.
    """
    result: dict[str, list[dict[str, str]]] = {}
    current_service = ""

    for line in raw.splitlines():
        stripped = line.strip()
        service_match = re.match(r"^===\s*tccutil list (\w+)\s*===", stripped)
        if service_match:
            current_service = service_match.group(1)
            result[current_service] = []
            continue
        if not current_service or not stripped:
            continue
        if "No" in stripped and "permissions" in stripped.lower():
            continue
        client_match = re.match(r'^\s*([\w\.]+)\s*(.*)$', stripped)
        if client_match:
            result.setdefault(current_service, []).append({
                "client": client_match.group(1),
                "status": client_match.group(2).strip(),
            })
    return result


def parse_plist_policy(raw: str) -> dict[str, Any]:
    """Parse managed preference plist policy output.

    The raw output has '=== <plist_name> ===' sections followed by
    plutil -p output.
    """
    result: dict[str, Any] = {"profiles": {}}
    current_plist = ""

    for line in raw.splitlines():
        stripped = line.strip()
        section_match = re.match(r"^===\s+(.+\.plist)\s+===", stripped)
        if section_match:
            current_plist = section_match.group(1)
            result["profiles"][current_plist] = {}
            continue
        if not current_plist or not stripped:
            continue
        kv_match = re.match(r'^\s*"([\w\-\.]+)"\s*=\s*(.+)$', stripped)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip().rstrip(";")
            result["profiles"][current_plist][key] = value.strip('"')
    return result


def audit(
    audit_csrutil: bool = True,
    audit_spctl: bool = True,
    audit_xprotect: bool = True,
    audit_tccutil: bool = True,
    audit_plist_policy: bool = True,
) -> dict[str, Any]:
    """Audit all macOS security subsystems.

    Returns dict with keys: csrutil, spctl, xprotect, tccutil, plist_policy.
    """
    result: dict[str, Any] = {
        "csrutil": {},
        "spctl": {},
        "xprotect": {},
        "tccutil": {},
        "plist_policy": {},
    }

    if audit_csrutil:
        result["csrutil"] = parse_csrutil_status(
            _run(["csrutil", "status"])
        )

    if audit_spctl:
        status_raw = _run(["spctl", "--status"])
        result["spctl"] = parse_spctl_status(status_raw)

    if audit_xprotect:
        xprotect_raw = _run([
            "defaults", "read",
            "/System/Library/CoreServices/XProtect.bundle/Contents/Resources/XProtect.meta.plist",
        ])
        result["xprotect"] = parse_xprotect(xprotect_raw)

    if audit_tccutil:
        combined = ""
        for service in ("Camera", "Microphone", "Accessibility", "FullDiskAccess"):
            combined += f"=== tccutil list {service} ===\n"
            combined += _run(["tccutil", "list", service])
            combined += "\n"
        result["tccutil"] = parse_tccutil(combined)

    if audit_plist_policy:
        combined = ""
        import glob
        import os
        for pattern in (
            "/Library/Managed Preferences/*.plist",
            os.path.expanduser("~/Library/Managed Preferences/*.plist"),
        ):
            for plist in sorted(glob.glob(pattern)):
                combined += f"=== {os.path.basename(plist)} ===\n"
                combined += _run(["plutil", "-p", plist])
                combined += "\n"
        result["plist_policy"] = parse_plist_policy(combined)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit macOS security state")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--no-csrutil", action="store_true")
    parser.add_argument("--no-spctl", action="store_true")
    parser.add_argument("--no-xprotect", action="store_true")
    parser.add_argument("--no-tccutil", action="store_true")
    parser.add_argument("--no-plist", action="store_true")
    args = parser.parse_args()

    data = audit(
        audit_csrutil=not args.no_csrutil,
        audit_spctl=not args.no_spctl,
        audit_xprotect=not args.no_xprotect,
        audit_tccutil=not args.no_tccutil,
        audit_plist_policy=not args.no_plist,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
