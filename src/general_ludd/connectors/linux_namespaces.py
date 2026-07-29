"""Linux namespace/cgroup connector — namespace isolation and resource control inspection.

Reads /proc/<pid>/ns/*, /proc/<pid>/cgroup, /proc/<pid>/status, /sys/fs/cgroup/*
and optionally runs ``lsns --json``. All commands run via list argv (never shell=True).
File reads use open() with try/except for missing /proc entries.

Self-contained: imports nothing from sibling connector modules and defines its own
runner protocol so it can be unit-tested with a canned, injected runner.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CommandRunner(Protocol):
    """Injectable subprocess runner. Takes argv list, returns result."""

    def __call__(self, argv: Sequence[str]) -> RunResult: ...


class RunResult(Protocol):
    """Minimal result shape a runner must return."""

    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


_SHELL_METACHARS = re.compile(r"[;&|`$\\<>(){}\[\]!*?~\n\r\x00\s]")

CAP_NAMES: dict[int, str] = {
    0: "CAP_CHOWN",
    1: "CAP_DAC_OVERRIDE",
    2: "CAP_DAC_READ_SEARCH",
    3: "CAP_FOWNER",
    4: "CAP_FSETID",
    5: "CAP_KILL",
    6: "CAP_SETGID",
    7: "CAP_SETUID",
    8: "CAP_SETPCAP",
    9: "CAP_NET_BIND_SERVICE",
    10: "CAP_NET_BROADCAST",
    11: "CAP_NET_ADMIN",
    12: "CAP_NET_RAW",
    13: "CAP_IPC_LOCK",
    14: "CAP_IPC_OWNER",
    15: "CAP_SYS_MODULE",
    16: "CAP_SYS_RAWIO",
    17: "CAP_SYS_CHROOT",
    18: "CAP_SYS_PTRACE",
    19: "CAP_SYS_PACCT",
    20: "CAP_SYS_ADMIN",
    21: "CAP_SYS_BOOT",
    22: "CAP_SYS_NICE",
    23: "CAP_SYS_RESOURCE",
    24: "CAP_SYS_TIME",
    25: "CAP_SYS_TTY_CONFIG",
    26: "CAP_MKNOD",
    27: "CAP_LEASE",
    28: "CAP_AUDIT_WRITE",
    29: "CAP_AUDIT_CONTROL",
    30: "CAP_SETFCAP",
    31: "CAP_MAC_OVERRIDE",
    32: "CAP_MAC_ADMIN",
    33: "CAP_SYSLOG",
    34: "CAP_WAKE_ALARM",
    35: "CAP_BLOCK_SUSPEND",
    36: "CAP_AUDIT_READ",
    37: "CAP_PERFMON",
    38: "CAP_BPF",
    39: "CAP_CHECKPOINT_RESTORE",
}


class LinuxNamespacesSource:
    """Query Linux namespaces, cgroups, and capabilities."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "linux_namespaces"))
        self._runner: CommandRunner | None = runner

    # -- validation -------------------------------------------------------

    def _resolve_pid(self, raw: Any) -> int | str:
        if raw is None or raw == "self":
            return "self"
        if isinstance(raw, int):
            if raw < 0:
                raise ValueError(f"pid must not be negative, got {raw}")
            return raw
        if isinstance(raw, str):
            self._validate_string_field(raw, "pid")
            try:
                return int(raw)
            except ValueError as err:
                raise ValueError(f"pid must be an integer, got {raw!r}") from err
        raise ValueError(f"pid must be int or 'self', got {raw!r}")

    @staticmethod
    def _validate_string_field(value: str, field: str) -> str:
        if _SHELL_METACHARS.search(value):
            raise ValueError(f"{field} contains forbidden characters")
        return value

    # -- runner -----------------------------------------------------------

    def _run(self, argv: list[str]) -> RunResult:
        if self._runner is None:
            raise RuntimeError("no runner injected for LinuxNamespacesSource")
        return self._runner(argv)

    # -- file helpers -----------------------------------------------------

    @staticmethod
    def _read_file(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, PermissionError, OSError):
            return None

    @staticmethod
    def _list_dir(path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except (FileNotFoundError, PermissionError, OSError):
            return []

    @staticmethod
    def _readlink(path: str) -> str | None:
        try:
            return os.readlink(path)
        except (OSError, ValueError):
            return None

    # -- capability parsing -----------------------------------------------

    @staticmethod
    def _parse_cap_mask(hex_str: str) -> list[str]:
        hex_str = hex_str.strip()
        if not hex_str or hex_str.lower() == "0000000000000000":
            return []
        try:
            value = int(hex_str, 16)
        except ValueError:
            return []
        names: list[str] = []
        for bit in range(64):
            if value & (1 << bit):
                name = CAP_NAMES.get(bit, f"cap_{bit}")
                names.append(name)
        return names

    # -- /proc readers ----------------------------------------------------

    def _read_ns_links(self, pid: int | str) -> dict[str, str]:
        ns_dir = f"/proc/{pid}/ns"
        result: dict[str, str] = {}
        for entry in self._list_dir(ns_dir):
            link_path = os.path.join(ns_dir, entry)
            target = self._readlink(link_path)
            result[entry] = target if target is not None else ""
        return result

    def _read_cgroup_info(self, pid: int | str) -> list[dict[str, str]]:
        content = self._read_file(f"/proc/{pid}/cgroup")
        if content is None:
            return []
        entries: list[dict[str, str]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) >= 3:
                entries.append({
                    "hierarchy": parts[0],
                    "controllers": parts[1],
                    "cgroup_path": parts[2],
                })
        return entries

    def _read_capabilities(self, pid: int | str) -> dict[str, Any]:
        content = self._read_file(f"/proc/{pid}/status")
        if content is None:
            return {}
        fields: dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key in ("CapBnd", "CapEff", "CapPrm", "CapInh", "CapAmb"):
                fields[key] = val
        result: dict[str, Any] = {}
        for key, hex_val in fields.items():
            result[key] = {
                "hex": hex_val,
                "names": self._parse_cap_mask(hex_val),
            }
        return result

    def _read_cgroup_v2(self, pid: int | str) -> list[dict[str, Any]]:
        content = self._read_file(f"/proc/{pid}/cgroup")
        if content is None:
            return []
        v2_base = "/sys/fs/cgroup"
        cgroup_rel = None
        for line in content.splitlines():
            line = line.strip()
            parts = line.split(":", 2)
            if len(parts) >= 3 and parts[1] == "":
                cgroup_rel = parts[2].lstrip("/")
                break
        if cgroup_rel is None:
            return []
        ctrl_content = self._read_file(os.path.join(v2_base, cgroup_rel, "cgroup.controllers"))
        subtree_content = self._read_file(os.path.join(v2_base, cgroup_rel, "cgroup.subtree_control"))
        events_content = self._read_file(os.path.join(v2_base, cgroup_rel, "cgroup.events"))
        return [{
            "cgroup_path": cgroup_rel,
            "controllers": (ctrl_content or "").strip().split(),
            "subtree_control": (subtree_content or "").strip().split(),
            "events": (events_content or "").strip(),
        }]

    # -- record builder ---------------------------------------------------

    def _make_record(
        self,
        message: str,
        labels: dict[str, str],
        raw: dict[str, Any],
        ts: float | None = None,
    ) -> dict[str, Any]:
        return {
            "ts": ts if ts is not None else time.time(),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "ok",
            "message": message,
            "value": None,
            "labels": labels,
            "raw": raw,
        }

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            # An injected runner is an explicit, deterministic probe boundary.
            # Consult it before the host filesystem so tests and sandboxed
            # callers do not accidentally inherit the coordinator's /proc
            # namespace support.
            if self._runner is not None:
                result = self._run(["unshare", "--help"])
                if result.returncode == 0:
                    return {"ok": True, "detail": "namespace support confirmed via unshare"}
                return {"ok": False, "detail": f"unshare exited {result.returncode}"}
            try:
                os.readlink("/proc/self/ns/pid")
                return {"ok": True, "detail": "namespace support confirmed via /proc"}
            except (FileNotFoundError, PermissionError, OSError):
                pass
            return {"ok": False, "detail": "cannot probe namespace support"}
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ------------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            if not isinstance(spec, dict):
                return []
            target = spec.get("target", "namespaces")
            if not isinstance(target, str):
                return []
            pid = self._resolve_pid(spec.get("pid"))
        except (ValueError, RuntimeError):
            return []
        try:
            if target == "namespaces":
                return self._query_namespaces(pid)
            if target == "cgroups":
                return self._query_cgroups(pid)
            if target == "capabilities":
                return self._query_capabilities(pid)
            if target == "cgroup_v2":
                return self._query_cgroup_v2(pid)
            if target == "ns_list":
                return self._query_ns_list()
            return []
        except Exception:
            return []

    # -- target: namespaces -----------------------------------------------

    def _query_namespaces(self, pid: int | str) -> list[dict[str, Any]]:
        ns_links = self._read_ns_links(pid)
        if not ns_links:
            return []
        ts = time.time()
        records: list[dict[str, Any]] = []
        for ns_type, target in sorted(ns_links.items()):
            records.append(self._make_record(
                message=f"{ns_type}: {target}",
                labels={"ns_type": ns_type, "target": target},
                raw={"ns_type": ns_type, "inode": target},
                ts=ts,
            ))
        return records

    # -- target: cgroups --------------------------------------------------

    def _query_cgroups(self, pid: int | str) -> list[dict[str, Any]]:
        entries = self._read_cgroup_info(pid)
        ts = time.time()
        records: list[dict[str, Any]] = []
        for entry in entries:
            records.append(self._make_record(
                message=(
                    f"hierarchy={entry['hierarchy']} controllers={entry['controllers']}"
                    f" path={entry['cgroup_path']}"
                ),
                labels={
                    "hierarchy": entry["hierarchy"],
                    "controllers": entry["controllers"],
                    "cgroup_path": entry["cgroup_path"],
                },
                raw=dict(entry),
                ts=ts,
            ))
        return records

    # -- target: capabilities ---------------------------------------------

    def _query_capabilities(self, pid: int | str) -> list[dict[str, Any]]:
        caps = self._read_capabilities(pid)
        if not caps:
            return []
        ts = time.time()
        records: list[dict[str, Any]] = []
        for cap_type, cap_data in caps.items():
            names = cap_data.get("names", [])
            hex_val = cap_data.get("hex", "")
            records.append(self._make_record(
                message=f"{cap_type}: {hex_val} ({', '.join(names)})",
                labels={
                    "cap_type": cap_type,
                    "hex": hex_val,
                    "cap_names": ",".join(names),
                },
                raw={"cap_type": cap_type, "hex": hex_val, "names": names},
                ts=ts,
            ))
        return records

    # -- target: cgroup_v2 ------------------------------------------------

    def _query_cgroup_v2(self, pid: int | str) -> list[dict[str, Any]]:
        entries = self._read_cgroup_v2(pid)
        if not entries:
            return []
        ts = time.time()
        records: list[dict[str, Any]] = []
        for entry in entries:
            records.append(self._make_record(
                message=f"cgroup_v2 path={entry['cgroup_path']}",
                labels={
                    "cgroup_path": entry["cgroup_path"],
                    "controllers": ",".join(entry["controllers"]),
                },
                raw=dict(entry),
                ts=ts,
            ))
        return records

    # -- target: ns_list --------------------------------------------------

    def _query_ns_list(self) -> list[dict[str, Any]]:
        try:
            result = self._run(["lsns", "--json"])
        except Exception:
            return []
        if result.returncode != 0:
            return []
        stdout = (result.stdout or "").strip()
        if not stdout:
            return []
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        ns_entries: list[dict[str, Any]] = data.get("namespaces", []) if isinstance(data, dict) else data
        if not isinstance(ns_entries, list):
            return []
        ts = time.time()
        records: list[dict[str, Any]] = []
        for entry in ns_entries:
            if not isinstance(entry, dict):
                continue
            ns_type = str(entry.get("ns", entry.get("type", "")))
            ns_pid = str(entry.get("pid", ""))
            records.append(self._make_record(
                message=f"{ns_type}: pid={ns_pid}",
                labels={"ns_type": ns_type, "pid": ns_pid},
                raw=dict(entry),
                ts=ts,
            ))
        return records
