#!/usr/bin/env python3
"""Linux kernel auditor — linux_kernel role backend.

Self-contained (stdlib only) auditor invoked by the ansible role to
collect kernel module, sysctl, cgroup, namespace, and eBPF state from
a Linux host. Produces a single JSON artifact with structured data.

Usage:
    python3 linux_kernel_audit.py --output /tmp/audit.json
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


def parse_lsmod(raw: str) -> list[dict[str, Any]]:
    """Parse lsmod output into list of module dicts.

    Format: Module Size Used Count [Dependent Modules]
    The third column is the used-by count (integer). If there are
    dependent modules, they follow as a comma-separated list.
    """
    modules: list[dict[str, Any]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return modules

    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        used_count_str = parts[2]
        used_by: list[str] = []
        if len(parts) > 3:
            dep_str = " ".join(parts[3:])
            used_by = [d.strip() for d in dep_str.split(",") if d.strip()]
        modules.append({
            "module": parts[0],
            "size": int(parts[1]) if parts[1].isdigit() else 0,
            "used_by": used_by,
            "used_count": int(used_count_str) if used_count_str.isdigit() else 0,
        })
    return modules


def parse_modinfo(raw: str) -> dict[str, Any]:
    """Parse modinfo output into structured module info dict.

    Format: fieldname: value
    """
    result: dict[str, Any] = {
        "filename": "",
        "version": "",
        "license": "",
        "description": "",
        "depends": [],
        "properties": {},
    }
    for line in raw.splitlines():
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "filename":
            result["filename"] = value
        elif key == "version":
            result["version"] = value
        elif key == "license":
            result["license"] = value
        elif key == "description":
            result["description"] = value
        elif key == "depends":
            result["depends"] = [d.strip() for d in value.split(",") if d.strip() and d.strip() != "-"]
        else:
            result["properties"][key] = value
    return result


def parse_proc_cgroups(raw: str) -> list[dict[str, Any]]:
    """Parse /proc/cgroups into list of controller dicts.

    Format: #subsys_name hierarchy num_cgroups enabled
    """
    controllers: list[dict[str, Any]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return controllers

    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        controllers.append({
            "subsystem": parts[0],
            "hierarchy": int(parts[1]) if parts[1].isdigit() else 0,
            "num_cgroups": int(parts[2]) if parts[2].isdigit() else 0,
            "enabled": int(parts[3]) if parts[3].isdigit() else 0,
        })
    return controllers


def parse_proc_pid_cgroup(raw: str) -> list[dict[str, str]]:
    """Parse /proc/<pid>/cgroup into list of cgroup membership entries.

    Format: hierarchy_id:controllers:path
    """
    entries: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(":")
        if len(parts) < 3:
            continue
        entries.append({
            "hierarchy_id": parts[0],
            "controllers": parts[1],
            "path": parts[2],
        })
    return entries


def parse_lsns(raw: str) -> list[dict[str, Any]]:
    """Parse 'lsns --json' output into list of namespace dicts."""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    namespaces_list = data.get("namespaces", [])
    if not isinstance(namespaces_list, list):
        return []
    result: list[dict[str, Any]] = []
    for ns in namespaces_list:
        if not isinstance(ns, dict):
            continue
        result.append({
            "ns_type": ns.get("type", ""),
            "nstype": ns.get("nstype", ""),
            "path": ns.get("path", ""),
            "nprocs": ns.get("nprocs", 0),
            "pid": ns.get("pid", 0),
            "command": ns.get("command", ""),
            "uid": ns.get("uid", 0),
        })
    return result


def parse_proc_ns_listing(raw: str) -> list[dict[str, str]]:
    """Parse 'ls -la /proc/<pid>/ns/' output into namespace link entries.

    Format: lrwxrwxrwx ... net -> net:[4026532000]
    """
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if "->" not in stripped:
            continue
        parts = stripped.split("->")
        if len(parts) < 2:
            continue
        link_part = parts[0].strip()
        target_part = parts[1].strip()
        name_match = re.search(r"(\w+)\s*$", link_part)
        ns_type = name_match.group(1) if name_match else ""
        ns_inode = target_part
        type_match = re.match(r"(\w+):\[", target_part)
        ns_type_from_target = type_match.group(1) if type_match else ns_type
        entries.append({
            "type": ns_type_from_target,
            "name": ns_type,
            "inode": ns_inode,
        })
    return entries


def parse_bpftool_prog(raw: str) -> list[dict[str, Any]]:
    """Parse 'bpftool prog list' output into structured program dicts.

    Format:
        <id>: <type>  tag <tag>  gpl
            loaded_at <date>  uid <uid>
            xlated <bytes>  jited <bytes>  mem <bytes>
            btf_id <id>  pids <pid>(<comm>)
    """
    programs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        prog_match = re.match(r"^(\d+):\s+(\S+)\s+tag\s+(\S+)", stripped)
        if prog_match:
            if current is not None:
                programs.append(current)
            current = {
                "id": int(prog_match.group(1)),
                "type": prog_match.group(2),
                "tag": prog_match.group(3),
                "license": "",
                "loaded_at": "",
                "uid": "",
                "bytes_xlated": 0,
                "bytes_jited": 0,
            }
            if "gpl" in stripped:
                current["license"] = "GPL"
        elif current is not None:
            if "loaded_at" in stripped:
                loaded_match = re.search(r"loaded_at\s+(\S+)", stripped)
                if loaded_match:
                    current["loaded_at"] = loaded_match.group(1)
                uid_match = re.search(r"uid\s+(\S+)", stripped)
                if uid_match:
                    current["uid"] = uid_match.group(1)
            elif "xlated" in stripped:
                xlated_match = re.search(r"xlated\s+(\d+)\s*B", stripped)
                if xlated_match:
                    current["bytes_xlated"] = int(xlated_match.group(1))
                jited_match = re.search(r"jited\s+(\d+)\s*B", stripped)
                if jited_match:
                    current["bytes_jited"] = int(jited_match.group(1))
    if current is not None:
        programs.append(current)
    return programs


def parse_findmnt_cgroup(raw: str) -> list[dict[str, str]]:
    """Parse 'findmnt -t cgroup,cgroup2' output into mount entries."""
    entries: list[dict[str, str]] = []
    lines = raw.strip().splitlines()
    if len(lines) < 2:
        return entries

    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        entries.append({
            "target": parts[0],
            "source": parts[1] if len(parts) > 1 else "",
            "fstype": parts[2] if len(parts) > 2 else "",
            "options": parts[3] if len(parts) > 3 else "",
        })
    return entries


def parse_sysctl(raw: str) -> dict[str, str]:
    """Parse 'sysctl -a' output into key-value dict."""
    result: dict[str, str] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if " = " in stripped:
            key, value = stripped.split(" = ", 1)
            result[key.strip()] = value.strip()
    return result


def audit(
    audit_modules: bool = True,
    audit_sysctl: bool = True,
    audit_cgroups: bool = True,
    audit_namespaces: bool = True,
    audit_ebpf: bool = True,
) -> dict[str, Any]:
    """Audit all Linux kernel subsystems.

    Returns dict with keys: modules, sysctl, cgroups, namespaces, ebpf.
    """
    result: dict[str, Any] = {
        "modules": [],
        "sysctl": {},
        "cgroups": {"controllers": [], "init_cgroup": [], "mounts": []},
        "namespaces": {"lsns": [], "init_ns": []},
        "ebpf": {"programs": []},
    }

    if audit_modules:
        lsmod_raw = _run(["lsmod"])
        result["modules"] = parse_lsmod(lsmod_raw)

    if audit_sysctl:
        result["sysctl"] = parse_sysctl(_run(["sysctl", "-a"]))

    if audit_cgroups:
        result["cgroups"]["controllers"] = parse_proc_cgroups(
            _read_file("/proc/cgroups")
        )
        result["cgroups"]["init_cgroup"] = parse_proc_pid_cgroup(
            _read_file("/proc/1/cgroup")
        )
        result["cgroups"]["mounts"] = parse_findmnt_cgroup(
            _run(["findmnt", "-t", "cgroup,cgroup2"])
        )

    if audit_namespaces:
        lsns_raw = _run(["lsns", "--json"])
        result["namespaces"]["lsns"] = parse_lsns(lsns_raw)
        init_ns_raw = _run(["ls", "-la", "/proc/1/ns/"])
        result["namespaces"]["init_ns"] = parse_proc_ns_listing(init_ns_raw)

    if audit_ebpf:
        bpftool_raw = _run(["bpftool", "prog", "list"])
        result["ebpf"]["programs"] = parse_bpftool_prog(bpftool_raw)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Linux kernel state")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--no-modules", action="store_true")
    parser.add_argument("--no-sysctl", action="store_true")
    parser.add_argument("--no-cgroups", action="store_true")
    parser.add_argument("--no-namespaces", action="store_true")
    parser.add_argument("--no-ebpf", action="store_true")
    args = parser.parse_args()

    data = audit(
        audit_modules=not args.no_modules,
        audit_sysctl=not args.no_sysctl,
        audit_cgroups=not args.no_cgroups,
        audit_namespaces=not args.no_namespaces,
        audit_ebpf=not args.no_ebpf,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} sections to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
