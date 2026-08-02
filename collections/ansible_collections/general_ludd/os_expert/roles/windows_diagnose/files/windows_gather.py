#!/usr/bin/env python3
"""Windows diagnostic gatherer — windows_diagnose role backend.

Self-contained (stdlib only) gatherer that parses Windows diagnostic
artifacts: WMI JSON (Win32_OperatingSystem, Win32_ComputerSystem,
Win32_Processor), EventLog JSON (System, Application), registry query
output, and service controller output. Produces a single JSON artifact.

Usage:
    python3 windows_gather.py --output /tmp/artifact.json
    python3 windows_gather.py --output /tmp/artifact.json --wmi-dir ./wmi
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _read_json(path: str) -> Any:
    """Read a JSON file (return empty dict/list on failure)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# ── WMI parsers ───────────────────────────────────────────────────────────

def parse_wmi_os(raw: str) -> dict[str, Any]:
    """Parse Win32_OperatingSystem JSON into structured dict."""
    data = {"caption": "", "version": "", "build_number": "", "install_date": ""}
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        data["caption"] = obj.get("Caption", "")
        data["version"] = obj.get("Version", "")
        data["build_number"] = obj.get("BuildNumber", "")
        data["install_date"] = obj.get("InstallDate", "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return data


def parse_wmi_computersystem(raw: str) -> dict[str, Any]:
    """Parse Win32_ComputerSystem JSON into structured dict."""
    data = {
        "manufacturer": "", "model": "", "total_physical_memory": 0,
        "number_of_processors": 0, "number_of_logical_processors": 0,
        "domain": "", "dns_host_name": "",
    }
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        data["manufacturer"] = obj.get("Manufacturer", "")
        data["model"] = obj.get("Model", "")
        data["total_physical_memory"] = int(obj.get("TotalPhysicalMemory", 0))
        data["number_of_processors"] = int(obj.get("NumberOfProcessors", 0))
        data["number_of_logical_processors"] = int(obj.get("NumberOfLogicalProcessors", 0))
        data["domain"] = obj.get("Domain", "")
        data["dns_host_name"] = obj.get("DNSHostName", "")
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
        pass
    return data


def parse_wmi_processor(raw: str) -> list[dict[str, Any]]:
    """Parse Win32_Processor JSON into structured list."""
    processors: list[dict[str, Any]] = []
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        items = obj if isinstance(obj, list) else [obj]
        for p in items:
            processors.append({
                "name": p.get("Name", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "max_clock_speed": p.get("MaxClockSpeed", 0),
                "number_of_cores": p.get("NumberOfCores", 0),
                "number_of_logical_processors": p.get("NumberOfLogicalProcessors", 0),
                "architecture": p.get("Architecture", 0),
                "address_width": p.get("AddressWidth", 0),
            })
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return processors


# ── EventLog parsers ──────────────────────────────────────────────────────

def parse_eventlog(raw: str, source: str = "") -> list[dict[str, Any]]:
    """Parse Get-WinEvent JSON output into structured entries."""
    entries: list[dict[str, Any]] = []
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        events = obj if isinstance(obj, list) else [obj]
        for evt in events:
            if not isinstance(evt, dict):
                continue
            entries.append({
                "id": evt.get("Id", 0),
                "level_display_name": evt.get("LevelDisplayName", ""),
                "provider_name": evt.get("ProviderName", ""),
                "time_created": str(evt.get("TimeCreated", "")),
                "message": str(evt.get("Message", ""))[:500],
                "source": source,
            })
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return entries


# ── Registry parser ───────────────────────────────────────────────────────

def parse_reg_query(raw: str, key: str = "") -> list[dict[str, Any]]:
    """Parse reg query output into structured list.

    Format (Windows reg query plain-text):
        HKEY_LOCAL_MACHINE\\Path
            ValueName    REG_TYPE    ValueData
    """
    entries: list[dict[str, Any]] = []
    current_section = ""
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("HKEY"):
            current_section = stripped
            continue
        parts = stripped.split(None, 2)
        if len(parts) >= 3 and current_section:
            entries.append({
                "section": current_section,
                "key": key,
                "value_name": parts[0],
                "type": parts[1],
                "data": parts[2],
            })
    return entries


# ── Service parsers ───────────────────────────────────────────────────────

def parse_sc_query(raw: str) -> list[dict[str, Any]]:
    """Parse sc query state= all output into structured list.

    Format:
        SERVICE_NAME: <name>
        DISPLAY_NAME: <display>
                TYPE               : <type>
                STATE              : <state>
    """
    services: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                services.append(current)
                current = {}
            continue
        for label, field in [
            ("SERVICE_NAME:", "service_name"),
            ("DISPLAY_NAME:", "display_name"),
            ("TYPE", "type"),
            ("STATE", "state"),
            ("WIN32_EXIT_CODE", "exit_code"),
            ("SERVICE_EXIT_CODE", "service_exit_code"),
            ("CHECKPOINT", "checkpoint"),
            ("WAIT_HINT", "wait_hint"),
        ]:
            if stripped.upper().startswith(label) or stripped.startswith(label):
                val = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split(None, 2)[-1].strip()
                if field in ("exit_code", "service_exit_code", "checkpoint", "wait_hint"):
                    try:
                        current[field] = int(val.split()[0] if val.split() else 0)
                    except ValueError:
                        current[field] = val
                else:
                    current[field] = val
                break

    if current:
        services.append(current)
    return services


def parse_get_service(raw: str) -> list[dict[str, Any]]:
    """Parse Get-Service JSON output into structured list."""
    services: list[dict[str, Any]] = []
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        items = obj if isinstance(obj, list) else [obj]
        for svc in items:
            if not isinstance(svc, dict):
                continue
            services.append({
                "name": svc.get("Name", ""),
                "display_name": svc.get("DisplayName", ""),
                "status": svc.get("Status", ""),
                "start_type": str(svc.get("StartType", "")),
            })
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return services


# ── Gather orchestrator ───────────────────────────────────────────────────

def gather(
    wmi_dir: str = "",
    eventlog_dir: str = "",
    reg_uninstall_path: str = "",
    reg_services_path: str = "",
    reg_policies_path: str = "",
    sc_query_path: str = "",
    get_service_path: str = "",
) -> dict[str, Any]:
    """Gather all Windows diagnostic data from collected artifact files.

    Returns dict with keys: wmi, eventlog, registry, services.
    """
    result: dict[str, Any] = {
        "wmi": {"os": {}, "computersystem": {}, "processors": []},
        "eventlog": [],
        "registry": {"uninstall": [], "services": [], "policies": []},
        "services": {"sc_query": [], "get_service": []},
    }

    if wmi_dir:
        os_raw = _read_json(f"{wmi_dir}/win32_operatingsystem.json")
        if os_raw:
            result["wmi"]["os"] = parse_wmi_os(json.dumps(os_raw))

        cs_raw = _read_json(f"{wmi_dir}/win32_computersystem.json")
        if cs_raw:
            result["wmi"]["computersystem"] = parse_wmi_computersystem(json.dumps(cs_raw))

        cpu_raw = _read_json(f"{wmi_dir}/win32_processor.json")
        if cpu_raw:
            result["wmi"]["processors"] = parse_wmi_processor(json.dumps(cpu_raw))

    if eventlog_dir:
        for log_name in ["system", "application"]:
            raw = _read_json(f"{eventlog_dir}/{log_name}.json")
            if raw:
                result["eventlog"].extend(
                    parse_eventlog(json.dumps(raw), source=log_name)
                )

    for reg_key, reg_path in [
        ("uninstall", reg_uninstall_path),
        ("services", reg_services_path),
        ("policies", reg_policies_path),
    ]:
        if reg_path:
            raw = _read_json(reg_path)
            if raw:
                result["registry"][reg_key] = parse_reg_query(
                    json.dumps(raw) if isinstance(raw, (dict, list)) else str(raw),
                    key=reg_key,
                )

    if sc_query_path:
        raw = _read_json(sc_query_path)
        if raw:
            result["services"]["sc_query"] = parse_sc_query(
                str(raw) if isinstance(raw, str) else json.dumps(raw)
            )

    if get_service_path:
        raw = _read_json(get_service_path)
        if raw:
            result["services"]["get_service"] = parse_get_service(json.dumps(raw))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gather Windows diagnostics from artifacts")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--wmi-dir", default="", help="Path to WMI JSON artifact directory")
    parser.add_argument("--eventlog-dir", default="", help="Path to EventLog JSON artifact directory")
    parser.add_argument("--reg-uninstall", default="", help="Path to registry uninstall JSON")
    parser.add_argument("--reg-services", default="", help="Path to registry services JSON")
    parser.add_argument("--reg-policies", default="", help="Path to registry policies JSON")
    parser.add_argument("--sc-query", default="", help="Path to sc query output file")
    parser.add_argument("--get-service", default="", help="Path to Get-Service JSON file")
    args = parser.parse_args()

    data = gather(
        wmi_dir=args.wmi_dir,
        eventlog_dir=args.eventlog_dir,
        reg_uninstall_path=args.reg_uninstall,
        reg_services_path=args.reg_services,
        reg_policies_path=args.reg_policies,
        sc_query_path=args.sc_query,
        get_service_path=args.get_service,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    section_count = sum(
        1 for v in data.values()
        if (isinstance(v, dict) and any(vv for vv in v.values() if vv))
        or (isinstance(v, list) and v)
    )
    print(f"Wrote {section_count} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
