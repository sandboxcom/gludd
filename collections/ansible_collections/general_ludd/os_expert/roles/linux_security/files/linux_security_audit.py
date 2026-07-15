#!/usr/bin/env python3
"""Linux security auditor — linux_security role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
audit Linux security posture: SELinux, AppArmor, firewall, auditd,
PAM, kernel hardening, and listening ports. Produces a single JSON
artifact with structured findings.

Usage:
    python3 linux_security_audit.py --output /tmp/audit.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


def _run(args: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def parse_getenforce(raw: str) -> dict[str, Any]:
    mode = raw.strip().lower()
    if "enforcing" in mode:
        return {"mode": "enforcing"}
    if "permissive" in mode:
        return {"mode": "permissive"}
    if "disabled" in mode:
        return {"mode": "disabled"}
    return {"mode": "unknown", "raw": raw.strip()}


def parse_sestatus(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        val = val.strip()
        if key == "selinux_status":
            result["status"] = val
        elif key == "current_mode":
            result["current_mode"] = val
        elif key == "mode_from_config_file":
            result["config_mode"] = val
        elif key == "loaded_policy_name":
            result["loaded_policy"] = val
        elif key == "policy_mls_status":
            result["mls_status"] = val
        elif key == "policy_deny_unknown_status":
            result["deny_unknown"] = val
        elif key == "max_kernel_policy_version":
            try:
                result["max_policy_version"] = int(val)
            except ValueError:
                result["max_policy_version"] = val
    return result


def parse_semanage_booleans(raw: str) -> list[dict[str, Any]]:
    booleans: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("SELinux boolean"):
            continue
        m = re.match(
            r"^(\S+)\s+\((\S+)\s*,\s*(\S+)\)\s*(.*)",
            line,
        )
        if not m:
            continue
        state_val = m.group(2).strip("(),")
        default_val = m.group(3).strip("(),")
        booleans.append({
            "name": m.group(1),
            "state": "on" in state_val.lower(),
            "default": "on" in default_val.lower(),
            "description": m.group(4).strip(),
        })
    return booleans


def parse_aa_status(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "loaded": False,
        "profiles_loaded": 0,
        "profiles_enforce": 0,
        "profiles_complain": 0,
        "profiles_kill": 0,
        "profiles_unconfined": 0,
        "processes_with_profiles": 0,
        "enforce_profiles": [],
        "complain_profiles": [],
    }
    if not raw.strip():
        return result
    if "is not loaded" in raw:
        return result
    result["loaded"] = "module is loaded" in raw or "profiles are loaded" in raw
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"(\d+) profiles are loaded", line)
        if m:
            result["profiles_loaded"] = int(m.group(1))
            continue
        m = re.match(r"(\d+) profiles are in enforce mode", line)
        if m:
            result["profiles_enforce"] = int(m.group(1))
            continue
        m = re.match(r"(\d+) profiles are in complain mode", line)
        if m:
            result["profiles_complain"] = int(m.group(1))
            continue
        m = re.match(r"(\d+) processes have profiles defined", line)
        if m:
            result["processes_with_profiles"] = int(m.group(1))
            continue
    lines = raw.splitlines()
    in_enforce = False
    in_complain = False
    for line in lines:
        stripped = line.strip()
        if "are in enforce mode" in stripped:
            in_enforce = True
            in_complain = False
            continue
        if "are in complain mode" in stripped:
            in_enforce = False
            in_complain = True
            continue
        if "are in kill mode" in stripped or "are in unconfined mode" in stripped:
            in_enforce = False
            in_complain = False
            continue
        if stripped and not re.match(r"^\d+ ", stripped) and not stripped.startswith("("):
            if in_enforce:
                result["enforce_profiles"].append(stripped)
            elif in_complain:
                result["complain_profiles"].append(stripped)
    return result


def parse_iptables_rules(raw: str, table: str = "filter", chain: str = "INPUT") -> dict[str, Any]:
    result: dict[str, Any] = {
        "table": table,
        "chain": chain,
        "policy": "UNKNOWN",
        "rules": [],
    }
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"Chain (\S+) \(policy (\w+)\)", line)
        if m:
            result["chain"] = m.group(1)
            result["policy"] = m.group(2)
            continue
        if not line or line.startswith("pkts") or line.startswith("Chain "):
            continue
        parts = line.split(None, 7)
        if len(parts) < 4:
            continue
        target = parts[2]
        protocol = parts[3] if parts[3] != "all" else "any"
        rule: dict[str, Any] = {
            "target": target,
            "protocol": protocol,
        }
        if len(parts) > 7:
            extra = parts[7]
            dport_m = re.search(r"dpt:(\d+)", extra)
            if dport_m:
                rule["dport"] = dport_m.group(1)
        result["rules"].append(rule)
    return result


def parse_auditctl_rules(raw: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("-w "):
            parts = line.split()
            path = parts[1] if len(parts) > 1 else ""
            perms = ""
            key = ""
            for i, p in enumerate(parts):
                if p == "-p" and i + 1 < len(parts):
                    perms = parts[i + 1]
                if p == "-k" and i + 1 < len(parts):
                    key = parts[i + 1]
            rules.append({
                "type": "file_watch",
                "path": path,
                "permissions": perms,
                "key": key,
                "raw": line,
            })
        elif line.startswith("-a "):
            syscall = ""
            key = ""
            parts = line.split()
            for i, p in enumerate(parts):
                if p.startswith("-S"):
                    if len(p) > 2:
                        syscall = p[2:]
                    elif i + 1 < len(parts):
                        syscall = parts[i + 1]
                if p == "-k" and i + 1 < len(parts):
                    key = parts[i + 1]
            rules.append({
                "type": "syscall",
                "syscall": syscall,
                "key": key,
                "raw": line,
            })
    return rules


def parse_auditctl_status(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or " " not in line:
            continue
        key, _, val = line.partition(" ")
        key = key.strip()
        val = val.strip()
        if key == "enabled":
            result["enabled"] = val == "1"
        elif key == "flag":
            result["failure_flag"] = val == "1"
        elif key == "pid":
            try:
                result["pid"] = int(val)
            except ValueError:
                result["pid"] = val
        elif key == "rate_limit":
            try:
                result["rate_limit"] = int(val)
            except ValueError:
                result["rate_limit"] = val
        elif key == "backlog_limit":
            try:
                result["backlog_limit"] = int(val)
            except ValueError:
                result["backlog_limit"] = val
        elif key == "lost":
            try:
                result["lost"] = int(val)
            except ValueError:
                result["lost"] = val
        elif key == "backlog":
            try:
                result["backlog"] = int(val)
            except ValueError:
                result["backlog"] = val
    return result


def parse_pam_config(raw: str, filename: str = "unknown") -> dict[str, Any]:
    result: dict[str, Any] = {
        "file": filename,
        "lines": [],
        "has_faillock": False,
        "has_pwquality": False,
        "has_tally2": False,
        "faillock_deny": None,
        "pwquality_minlen": None,
    }
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result["lines"].append(line)
        if "pam_faillock.so" in line:
            result["has_faillock"] = True
            deny_match = re.search(r"deny=(\d+)", line)
            if deny_match:
                result["faillock_deny"] = int(deny_match.group(1))
        if "pam_pwquality.so" in line:
            result["has_pwquality"] = True
            minlen_match = re.search(r"minlen=(\d+)", line)
            if minlen_match:
                result["pwquality_minlen"] = int(minlen_match.group(1))
        if "pam_tally2.so" in line:
            result["has_tally2"] = True
    return result


def parse_kernel_params(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().rsplit("/", 1)[-1]
        val = val.strip()
        if val.upper() == "N/A" or val == "":
            continue
        try:
            result[key] = int(val)
        except ValueError:
            result[key] = val
    return result


def parse_listening_ports(raw: str) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("State") or line.startswith("LISTEN"):
            parts_match = re.match(
                r"LISTEN\s+\d+\s+\d+\s+([\d.*:a-fA-F\[\]]+):(\d+)\s",
                line,
            )
            if not parts_match:
                parts_match = re.match(
                    r"LISTEN\s+\d+\s+\d+\s+\*:(\d+)",
                    line,
                )
            if parts_match:
                if parts_match.lastindex and parts_match.lastindex >= 2:
                    bind_addr = parts_match.group(1)
                    port = int(parts_match.group(2))
                else:
                    bind_addr = "*"
                    port = int(parts_match.group(1))
                ports.append({
                    "bind_address": bind_addr,
                    "port": port,
                })
                continue
        parts = line.split()
        if len(parts) >= 4 and parts[0] in ("tcp", "udp", "tcp6", "udp6"):
            addr_port = parts[3] if parts[0].endswith("6") else parts[3]
            m = re.match(r"([\d.*:a-fA-F\[\]]+):(\d+)", addr_port)
            if m:
                ports.append({
                    "bind_address": m.group(1),
                    "port": int(m.group(2)),
                    "protocol": parts[0],
                })
    return ports


def assess_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    selinux = data.get("selinux", {})
    if selinux.get("mode") == "disabled":
        findings.append({
            "id": "LSEC-SELINUX-001",
            "severity": "high",
            "category": "selinux",
            "description": "SELinux is disabled — mandatory access control unavailable",
        })
    elif selinux.get("mode") == "permissive":
        findings.append({
            "id": "LSEC-SELINUX-002",
            "severity": "medium",
            "category": "selinux",
            "description": "SELinux is in permissive mode — policies logged but not enforced",
        })
    elif selinux.get("mode") == "unknown" and not data.get("apparmor", {}).get("loaded"):
        findings.append({
            "id": "LSEC-MAC-001",
            "severity": "high",
            "category": "mac",
            "description": "No mandatory access control (SELinux or AppArmor) detected",
        })

    apparmor = data.get("apparmor", {})
    if not apparmor.get("loaded"):
        if not selinux.get("mode") or selinux.get("mode") in ("unknown", "disabled"):
            pass
    elif apparmor.get("profiles_complain", 0) > 0:
        findings.append({
            "id": "LSEC-APPARMOR-001",
            "severity": "low",
            "category": "apparmor",
            "description": f"{apparmor['profiles_complain']} AppArmor profiles in complain mode",
        })

    auditd = data.get("auditd", {})
    if not auditd.get("enabled"):
        findings.append({
            "id": "LSEC-AUDITD-001",
            "severity": "medium",
            "category": "auditd",
            "description": "auditd is not enabled — no system-call auditing",
        })

    kernel = data.get("kernel", {})
    if kernel.get("randomize_va_space", 2) < 2:
        findings.append({
            "id": "LSEC-KERNEL-001",
            "severity": "high",
            "category": "kernel",
            "description": f"ASLR not fully enabled (randomize_va_space={kernel.get('randomize_va_space')})",
        })
    if kernel.get("kptr_restrict", 0) < 1:
        findings.append({
            "id": "LSEC-KERNEL-002",
            "severity": "medium",
            "category": "kernel",
            "description": "kptr_restrict not set — kernel pointers visible to userspace",
        })
    if kernel.get("dmesg_restrict", 0) < 1:
        findings.append({
            "id": "LSEC-KERNEL-003",
            "severity": "low",
            "category": "kernel",
            "description": "dmesg_restrict not set — unprivileged users can read kernel log",
        })

    pam_configs = data.get("pam", [])
    for pam in pam_configs:
        if pam.get("has_pwquality") and pam.get("pwquality_minlen", 0) < 8:
            findings.append({
                "id": "LSEC-PAM-001",
                "severity": "medium",
                "category": "pam",
                "description": f"pam_pwquality minlen={pam.get('pwquality_minlen')} is below recommended minimum (8)",
            })
        if not pam.get("has_faillock"):
            findings.append({
                "id": "LSEC-PAM-002",
                "severity": "medium",
                "category": "pam",
                "description": "pam_faillock not configured — no account lockout after failed attempts",
            })

    ports = data.get("ports", [])
    sensitive_ports = {21, 23, 25, 135, 139, 445, 3306, 5432, 6379, 11211, 27017}
    for p in ports:
        if p.get("bind_address") in ("0.0.0.0", "*", "::") and p["port"] in sensitive_ports:
            findings.append({
                "id": "LSEC-PORTS-001",
                "severity": "high",
                "category": "ports",
                "description": f"Port {p['port']} listens on all interfaces — restrict to internal",
            })

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Linux security auditor")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()

    data: dict[str, Any] = {}

    getenforce_raw = _run(["getenforce"])
    data["selinux"] = parse_getenforce(getenforce_raw)
    sestatus_raw = _run(["sestatus"])
    if sestatus_raw:
        data["selinux"].update(parse_sestatus(sestatus_raw))
    semanage_raw = _run(["semanage", "boolean", "-l"])
    if semanage_raw:
        data["selinux"]["booleans"] = parse_semanage_booleans(semanage_raw)

    aa_raw = _run(["aa-status"])
    data["apparmor"] = parse_aa_status(aa_raw)

    iptables_filter = _run(["iptables", "-L", "-n", "-v"])
    data["iptables_input"] = parse_iptables_rules(iptables_filter, "filter", "INPUT")

    auditctl_rules = _run(["sudo", "-n", "auditctl", "-l"])
    auditctl_status = _run(["sudo", "-n", "auditctl", "-s"])
    data["auditd"] = {
        "rules": parse_auditctl_rules(auditctl_rules),
    }
    if auditctl_status:
        data["auditd"].update(parse_auditctl_status(auditctl_status))

    data["pam"] = []
    pam_files = [
        "/etc/pam.d/common-auth",
        "/etc/pam.d/common-password",
        "/etc/pam.d/system-auth",
        "/etc/pam.d/password-auth",
    ]
    for pf in pam_files:
        try:
            with open(pf) as fh:
                data["pam"].append(parse_pam_config(fh.read(), pf))
        except (FileNotFoundError, PermissionError):
            pass

    kernel_raw = (
        f"kptr_restrict: {_read_sysctl('kernel/kptr_restrict')}\n"
        f"dmesg_restrict: {_read_sysctl('kernel/dmesg_restrict')}\n"
        f"yama/ptrace_scope: {_read_sysctl('kernel/yama/ptrace_scope')}\n"
        f"perf_event_paranoid: {_read_sysctl('kernel/perf_event_paranoid')}\n"
        f"unprivileged_bpf_disabled: {_read_sysctl('kernel/unprivileged_bpf_disabled')}\n"
        f"unprivileged_userns_clone: {_read_sysctl('kernel/unprivileged_userns_clone')}\n"
        f"modules_disabled: {_read_sysctl('kernel/modules_disabled')}\n"
        f"randomize_va_space: {_read_sysctl('kernel/randomize_va_space')}\n"
    )
    data["kernel"] = parse_kernel_params(kernel_raw)

    ss_output = _run(["ss", "-tlnp"])
    data["ports"] = parse_listening_ports(ss_output)

    data["findings"] = assess_findings(data)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(data, fh, indent=2)
    else:
        json.dump(data, sys.stdout, indent=2)


def _read_sysctl(path: str) -> str:
    full = f"/proc/sys/{path}"
    try:
        with open(full) as fh:
            return fh.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return "N/A"


if __name__ == "__main__":
    main()
