#!/usr/bin/env python3
"""Windows security auditor — windows_security role backend.

Self-contained (stdlib only) auditor that parses JSON artifacts
collected by the ansible role (Defender, firewall, audit policy,
security config, hotfixes) and produces a structured assessment
with findings.

Usage:
    python3 windows_security_audit.py --output /tmp/audit.json
      [--defender-prefs prefs.json] [--defender-status status.json]
      [--defender-threats threats.json] [--firewall-rules rules.json]
      [--firewall-profiles profiles.json] [--auditpol auditpol.txt]
      [--secedit secedit.cfg] [--hotfixes hotfixes.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def parse_defender_prefs(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        prefs = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid JSON"}
    if isinstance(prefs, list) and len(prefs) > 0:
        prefs = prefs[0]
    return {
        "realtime_protection_enabled": not prefs.get("DisableRealtimeMonitoring", True),
        "behavior_monitoring_enabled": not prefs.get("DisableBehaviorMonitoring", True),
        "cloud_protection_enabled": not prefs.get("DisableBlockAtFirstSeen", True),
        "ioav_protection_enabled": not prefs.get("DisableIOAVProtection", True),
        "submit_samples": prefs.get("SubmitSamplesConsent", 0),
        "maps_reporting": prefs.get("MAPSReporting", 0),
    }


def parse_defender_status(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid JSON"}
    if isinstance(status, list) and len(status) > 0:
        status = status[0]
    return {
        "av_enabled": status.get("AntivirusEnabled", False),
        "am_enabled": status.get("AMServiceEnabled", False),
        "rtp_enabled": status.get("RealTimeProtectionEnabled", False),
        "antispyware_enabled": status.get("AntispywareEnabled", False),
    }


def parse_defender_threats(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        threats = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(threats, list):
        return []
    result: list[dict[str, Any]] = []
    for t in threats:
        if not isinstance(t, dict):
            continue
        action = t.get("ActionTaken", "")
        resolved_actions = {"Quarantine", "Remove", "Block", "Clean", "Delete"}
        result.append({
            "threat": t.get("ThreatName", "Unknown"),
            "severity": t.get("Severity", "Unknown"),
            "action": action,
            "resolved": any(a in action for a in resolved_actions),
        })
    return result


def parse_firewall_rules(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        rules = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(rules, list):
        return []
    result: list[dict[str, Any]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        result.append({
            "name": r.get("Name", ""),
            "action": r.get("Action", ""),
            "direction": r.get("Direction", ""),
            "protocol": r.get("Protocol", ""),
            "local_port": r.get("LocalPort", ""),
            "enabled": r.get("Enabled", True),
        })
    return result


def parse_firewall_profiles(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        profiles = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(profiles, list):
        return []
    result: list[dict[str, Any]] = []
    for p in profiles:
        if not isinstance(p, dict):
            continue
        result.append({
            "name": p.get("Name", ""),
            "enabled": p.get("Enabled", False),
            "default_inbound": p.get("DefaultInboundAction", ""),
            "default_outbound": p.get("DefaultOutboundAction", ""),
        })
    return result


def parse_auditpol(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("System audit policy") or line.startswith("Category"):
            continue
        m = re.match(r"(.+?)\s{2,}(.+)", line)
        if m:
            entries.append({
                "category": m.group(1).strip(),
                "setting": m.group(2).strip(),
            })
    return entries


def parse_secedit(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key:
            result[key] = val
    return result


def parse_hotfixes(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        hotfixes = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(hotfixes, list):
        return []
    result: list[dict[str, Any]] = []
    for h in hotfixes:
        if not isinstance(h, dict):
            continue
        result.append({
            "id": h.get("HotFixID", ""),
            "date": h.get("InstalledOn", ""),
            "description": h.get("Description", ""),
        })
    return result


def assess_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    defender_prefs = data.get("defender_prefs", {})
    defender_status = data.get("defender_status", {})

    if not defender_status.get("av_enabled", True):
        findings.append({
            "id": "WSEC-DEF-001",
            "severity": "critical",
            "category": "defender",
            "description": "Windows Defender antivirus is disabled",
        })

    if not defender_status.get("rtp_enabled", True):
        findings.append({
            "id": "WSEC-DEF-002",
            "severity": "critical",
            "category": "defender",
            "description": "Real-time protection is disabled",
        })

    if not defender_prefs.get("behavior_monitoring_enabled", True):
        findings.append({
            "id": "WSEC-DEF-003",
            "severity": "high",
            "category": "defender",
            "description": "Behavior monitoring is disabled",
        })

    if not defender_prefs.get("cloud_protection_enabled", True):
        findings.append({
            "id": "WSEC-DEF-004",
            "severity": "medium",
            "category": "defender",
            "description": "Cloud-delivered protection (Block at First Sight) is disabled",
        })

    defender_threats = data.get("defender_threats", [])
    active_threats = [t for t in defender_threats if not t.get("resolved") and t.get("severity", "") != "Low"]
    if active_threats:
        findings.append({
            "id": "WSEC-DEF-005",
            "severity": "high",
            "category": "defender",
            "description": f"{len(active_threats)} active/quarantined threats detected",
            "threats": [t["threat"] for t in active_threats],
        })

    firewall_profiles = data.get("firewall_profiles", [])
    for fp in firewall_profiles:
        if not fp.get("enabled"):
            findings.append({
                "id": "WSEC-FW-001",
                "severity": "critical",
                "category": "firewall",
                "description": f"Windows Firewall is disabled on {fp.get('name', 'unknown')} profile",
            })
        elif fp.get("default_inbound") == "Allow":
            findings.append({
                "id": "WSEC-FW-002",
                "severity": "high",
                "category": "firewall",
                "description": f"Inbound connections allowed by default on {fp.get('name', 'unknown')} profile",
            })

    auditpol = data.get("auditpol", [])
    no_auditing = [a for a in auditpol if a.get("setting") == "No Auditing"]
    if len(no_auditing) > len(auditpol) * 0.5:
        findings.append({
            "id": "WSEC-AUDIT-001",
            "severity": "medium",
            "category": "audit_policy",
            "description": f"{len(no_auditing)}/{len(auditpol)} audit categories have no auditing configured",
        })

    secedit = data.get("secedit", {})
    pw_complexity = secedit.get("PasswordComplexity", "0")
    pw_minlen = int(secedit.get("MinimumPasswordLength", "0"))
    if pw_complexity == "0":
        findings.append({
            "id": "WSEC-PW-001",
            "severity": "high",
            "category": "password_policy",
            "description": "Password complexity is disabled",
        })
    if pw_minlen < 8:
        findings.append({
            "id": "WSEC-PW-002",
            "severity": "medium",
            "category": "password_policy",
            "description": f"Minimum password length ({pw_minlen}) is below recommended (8)",
        })
    if secedit.get("LockoutBadCount", "0") == "0":
        findings.append({
            "id": "WSEC-LOCKOUT-001",
            "severity": "medium",
            "category": "lockout_policy",
            "description": "Account lockout is disabled (LockoutBadCount=0)",
        })

    hotfixes = data.get("hotfixes", [])
    security_updates = [h for h in hotfixes if "Security" in h.get("description", "")]
    if not hotfixes:
        findings.append({
            "id": "WSEC-PATCH-001",
            "severity": "low",
            "category": "patching",
            "description": "No hotfixes detected — system may lack recent security patches",
        })
    elif not security_updates:
        findings.append({
            "id": "WSEC-PATCH-002",
            "severity": "low",
            "category": "patching",
            "description": "No security-specific hotfixes in recent patch history",
        })

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows security auditor")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    parser.add_argument("--defender-prefs", default=None, help="Path to defender prefs JSON")
    parser.add_argument("--defender-status", default=None, help="Path to defender status JSON")
    parser.add_argument("--defender-threats", default=None, help="Path to defender threats JSON")
    parser.add_argument("--firewall-rules", default=None, help="Path to firewall rules JSON")
    parser.add_argument("--firewall-profiles", default=None, help="Path to firewall profiles JSON")
    parser.add_argument("--auditpol", default=None, help="Path to auditpol output text")
    parser.add_argument("--secedit", default=None, help="Path to secedit config file")
    parser.add_argument("--hotfixes", default=None, help="Path to hotfixes JSON")
    args = parser.parse_args()

    data: dict[str, Any] = {}

    def _read_file(path: str | None) -> str:
        if not path:
            return ""
        try:
            with open(path) as fh:
                return fh.read()
        except (FileNotFoundError, PermissionError):
            return ""

    data["defender_prefs"] = parse_defender_prefs(_read_file(args.defender_prefs))
    data["defender_status"] = parse_defender_status(_read_file(args.defender_status))
    data["defender_threats"] = parse_defender_threats(_read_file(args.defender_threats))
    data["firewall_rules"] = parse_firewall_rules(_read_file(args.firewall_rules))
    data["firewall_profiles"] = parse_firewall_profiles(_read_file(args.firewall_profiles))
    data["auditpol"] = parse_auditpol(_read_file(args.auditpol))
    data["secedit"] = parse_secedit(_read_file(args.secedit))
    data["hotfixes"] = parse_hotfixes(_read_file(args.hotfixes))

    data["findings"] = assess_findings(data)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(data, fh, indent=2)
    else:
        json.dump(data, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
