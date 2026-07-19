#!/usr/bin/env python3
"""Windows automation auditor — windows_automation role backend.

Self-contained (stdlib only) auditor that parses PowerShell/JSON output
captured by the ansible role. The role runs PowerShell commands on the
target Windows host and passes the JSON output to this parser. Produces
a single JSON artifact with structured representations.

Usage:
    python3 windows_automation_audit.py --output /tmp/audit.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def _safe_json_parse(raw: str) -> Any:
    """Try to parse JSON, returning empty list/dict on failure."""
    if not raw or not raw.strip():
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []


def parse_wsman_test(raw: str) -> dict[str, Any]:
    """Parse Test-WSMan JSON output into structured dict.

    Expected fields from ConvertTo-Json: ProductVersion, ConfigVersion, etc.
    """
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        return {
            "product_version": parsed.get("ProductVersion", ""),
            "config_version": parsed.get("ConfigVersion", ""),
            "raw": parsed,
        }
    if isinstance(parsed, list) and parsed:
        first = parsed[0] if isinstance(parsed[0], dict) else {}
        return {
            "product_version": first.get("ProductVersion", ""),
            "config_version": first.get("ConfigVersion", ""),
            "raw": parsed,
        }
    return {
        "product_version": "",
        "config_version": "",
        "raw": parsed,
    }


def parse_winrm_service(raw: str) -> dict[str, Any]:
    """Parse Get-Service WinRM JSON output into structured dict."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        return {
            "name": parsed.get("Name", ""),
            "status": parsed.get("Status", ""),
            "start_type": parsed.get("StartType", ""),
        }
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        svc = parsed[0]
        return {
            "name": svc.get("Name", ""),
            "status": svc.get("Status", ""),
            "start_type": svc.get("StartType", ""),
        }
    return {"name": "", "status": "", "start_type": ""}


def parse_dsc_status(raw: str) -> list[dict[str, Any]]:
    """Parse Get-DscConfigurationStatus JSON output into list of status dicts."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    results: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        results.append({
            "status": item.get("Status", ""),
            "start_date": item.get("StartDate", ""),
            "type": item.get("Type", ""),
            "mode": item.get("Mode", ""),
            "number_of_resources": len(item.get("ResourcesInDesiredState", []))
            + len(item.get("ResourcesNotInDesiredState", [])),
        })
    return results


def parse_dsc_test(raw: str) -> dict[str, Any]:
    """Parse Test-DscConfiguration output into InDesiredState dict."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        return {
            "in_desired_state": parsed.get("InDesiredState", parsed.get("value", False)),
        }
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return {
            "in_desired_state": parsed[0].get("InDesiredState", False),
        }
    return {"in_desired_state": False}


def parse_scheduled_tasks(raw: str) -> list[dict[str, str]]:
    """Parse Get-ScheduledTask JSON output into list of task dicts."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    tasks: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        tasks.append({
            "task_name": item.get("TaskName", ""),
            "state": item.get("State", ""),
            "task_path": item.get("TaskPath", ""),
        })
    return tasks


def parse_schtasks_raw(raw: str) -> list[dict[str, str]]:
    """Parse raw 'schtasks /query /fo LIST /v' output into task entries.

    Handles non-JSON LIST format with Folder/HostName/TaskName fields.
    """
    tasks: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if current and "task_name" in current:
                tasks.append(current)
                current = {}
            continue
        match = re.match(r"^(.+?):\s*(.*)$", stripped)
        if match:
            key = match.group(1).strip().replace(" ", "_").lower()
            value = match.group(2).strip()
            if key in ("taskname", "task_name"):
                current["task_name"] = value
            elif key in ("status", "next_run_time", "last_run_time"):
                current[key] = value
            elif key == "hostname":
                current["hostname"] = value
    if current and "task_name" in current:
        tasks.append(current)
    return tasks


def parse_installed_software(raw: str) -> list[dict[str, str]]:
    """Parse Get-ItemProperty uninstall registry JSON into software list."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    software: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("DisplayName", "")
        if not name:
            continue
        software.append({
            "name": name,
            "version": item.get("DisplayVersion", ""),
            "publisher": item.get("Publisher", ""),
        })
    return software


def parse_unattend_detection(raw: str) -> list[dict[str, Any]]:
    """Parse unattend.xml detection JSON output."""
    parsed = _safe_json_parse(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    results: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        results.append({
            "path": item.get("Path", ""),
            "exists": bool(item.get("Exists", False)),
        })
    return results


def audit(
    wsman_raw: str = "",
    winrm_raw: str = "",
    dsc_status_raw: str = "",
    dsc_test_raw: str = "",
    schtasks_json_raw: str = "",
    schtasks_list_raw: str = "",
    software_64_raw: str = "",
    software_32_raw: str = "",
    unattend_raw: str = "",
) -> dict[str, Any]:
    """Audit Windows automation subsystems from captured PowerShell JSON.

    All inputs are raw JSON strings captured by the ansible role.
    """
    result: dict[str, Any] = {
        "psremoting": parse_wsman_test(wsman_raw),
        "winrm_service": parse_winrm_service(winrm_raw),
        "dsc_status": parse_dsc_status(dsc_status_raw),
        "dsc_test": parse_dsc_test(dsc_test_raw),
        "scheduled_tasks": parse_scheduled_tasks(schtasks_json_raw),
        "schtasks_raw": parse_schtasks_raw(schtasks_list_raw),
        "installed_software_64bit": parse_installed_software(software_64_raw),
        "installed_software_32bit": parse_installed_software(software_32_raw),
        "unattend_detection": parse_unattend_detection(unattend_raw),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Windows automation state")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--wsman", default="", help="Test-WSMan JSON output")
    parser.add_argument("--winrm", default="", help="Get-Service WinRM JSON output")
    parser.add_argument("--dsc-status", default="", help="Get-DscConfigurationStatus JSON")
    parser.add_argument("--dsc-test", default="", help="Test-DscConfiguration JSON")
    parser.add_argument("--schtasks-json", default="", help="Get-ScheduledTask JSON")
    parser.add_argument("--schtasks-list", default="", help="schtasks /query LIST output")
    parser.add_argument("--software-64", default="", help="64-bit registry software JSON")
    parser.add_argument("--software-32", default="", help="32-bit registry software JSON")
    parser.add_argument("--unattend", default="", help="unattend.xml detection JSON")
    args = parser.parse_args()

    data = audit(
        wsman_raw=args.wsman,
        winrm_raw=args.winrm,
        dsc_status_raw=args.dsc_status,
        dsc_test_raw=args.dsc_test,
        schtasks_json_raw=args.schtasks_json,
        schtasks_list_raw=args.schtasks_list,
        software_64_raw=args.software_64,
        software_32_raw=args.software_32,
        unattend_raw=args.unattend,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
