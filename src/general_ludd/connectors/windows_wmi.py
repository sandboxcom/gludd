"""Windows WMI/CIM connector — WMI queries via PowerShell.

Provides programmatic access to Windows Management Instrumentation
for system information, hardware inventory, and configuration queries.

Self-contained source: imports nothing from sibling connectors. All subprocess
calls use a LIST argv (never ``shell=True``) and an injectable runner so unit
tests never need the real binaries.

Security: all caller-supplied values are validated to reject leading-dash
(option-injection guard) and shell metacharacters before they touch argv.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

Runner = Callable[[list[str]], tuple[int, str, str]]

_SHELL_METACHARS: frozenset[str] = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \"'")

_DEFAULT_TIMEOUT = 30.0

_TARGET_DISPATCH: dict[str, tuple[str, str]] = {
    "os": ("Win32_OperatingSystem", "Operating System"),
    "operating_system": ("Win32_OperatingSystem", "Operating System"),
    "computer_system": ("Win32_ComputerSystem", "Computer System"),
    "hardware": ("Win32_ComputerSystem", "Computer System"),
    "processor": ("Win32_Processor", "Processor"),
    "cpu": ("Win32_Processor", "Processor"),
    "memory": ("Win32_PhysicalMemory", "Physical Memory"),
    "ram": ("Win32_PhysicalMemory", "Physical Memory"),
    "disk": ("Win32_LogicalDisk | Where-Object DriveType -eq 3", "Logical Disk"),
    "storage": ("Win32_LogicalDisk | Where-Object DriveType -eq 3", "Logical Disk"),
    "network": (
        "Win32_NetworkAdapter | Where-Object NetEnabled -eq $true",
        "Network Adapter",
    ),
    "nic": (
        "Win32_NetworkAdapter | Where-Object NetEnabled -eq $true",
        "Network Adapter",
    ),
    "bios": ("Win32_BIOS", "BIOS"),
}

_DEFAULT_CLASS = "Win32_OperatingSystem"
_DEFAULT_KIND_LABEL = "Operating System"


def _validate_arg(value: str, field: str) -> str:
    """Validate a caller-supplied arg or raise ValueError.

    Rejects non-strings, empty values, a leading dash (option-injection
    guard), and any shell metacharacter / control character.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    if value == "":
        raise ValueError(f"{field} must not be empty")
    if value.startswith("-"):
        raise ValueError(f"{field} must not start with '-': {value!r}")
    bad = sorted(set(value) & _SHELL_METACHARS)
    if bad:
        raise ValueError(f"{field} contains disallowed characters {bad!r}: {value!r}")
    return value


def _default_runner(argv: list[str]) -> tuple[int, str, str]:
    """Run argv as a discrete LIST, never ``shell=True``, always time-bound."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {_DEFAULT_TIMEOUT}s")
    except (OSError, ValueError) as exc:
        return (127, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


class WinWmiConnector:
    """Query Windows WMI/CIM providers via PowerShell.

    Parameters
    ----------
    config:
        Arbitrary mapping; ``name`` key sets the log-record source name.
    runner:
        Optional injected command runner ``(argv) -> (rc, stdout, stderr)``.
        Defaults to a ``subprocess.run`` LIST-argv runner (never ``shell=True``).
    """

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "windows_wmi"))
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe via ``Get-CimInstance Win32_OperatingSystem``; return ``{'ok': bool, 'detail': str}``.

        Never raises — all exceptions are caught.
        """
        try:
            cmd = (
                "Get-CimInstance -ClassName Win32_OperatingSystem "
                "| Select-Object Caption,Version | ConvertTo-Json"
            )
            rc, out, err = self._runner([
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                cmd,
            ])
            if rc == 0:
                return {"ok": True, "detail": out.strip()[:200]}
            detail = (err or out or "").strip() or f"exit code {rc}"
            return {"ok": False, "detail": detail}
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ----------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a WMI query and return normalized records.

        ``spec['target']`` selects the probe:

        ====================  =========================================
        target                 WMI class queried
        ====================  =========================================
        ``"os"``               Win32_OperatingSystem
        ``"operating_system"`` Win32_OperatingSystem (alias)
        ``"computer_system"``  Win32_ComputerSystem
        ``"hardware"``         Win32_ComputerSystem (alias)
        ``"processor"``        Win32_Processor
        ``"cpu"``              Win32_Processor (alias)
        ``"memory"``           Win32_PhysicalMemory
        ``"ram"``              Win32_PhysicalMemory (alias)
        ``"disk"``             Win32_LogicalDisk (DriveType==3)
        ``"storage"``          Win32_LogicalDisk (alias)
        ``"network"``          Win32_NetworkAdapter (NetEnabled)
        ``"nic"``              Win32_NetworkAdapter (alias)
        ``"bios"``             Win32_BIOS
        (default)              Win32_OperatingSystem
        ====================  =========================================

        Returns an empty list on non-zero exit or invalid JSON.
        """
        spec = spec or {}
        target = str(spec.get("target", "")).strip().lower()
        if target:
            _validate_arg(target, field="target")

        if target in _TARGET_DISPATCH:
            wmi_class, kind_label = _TARGET_DISPATCH[target]
        else:
            wmi_class = _DEFAULT_CLASS
            kind_label = _DEFAULT_KIND_LABEL

        return self._run_wmi_query(wmi_class, kind_label)

    # -- WMI query runner -----------------------------------------------------

    def _run_wmi_query(self, wmi_class: str, kind_label: str) -> list[dict[str, Any]]:
        cmd = f"Get-CimInstance -ClassName {wmi_class} | ConvertTo-Json -Depth 3"
        rc, out, _err = self._runner([
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            cmd,
        ])
        ts = time.time()

        if rc != 0:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "error",
                "message": f"WMI query failed (exit {rc}): {kind_label}",
                "value": None,
                "labels": {"wmi_class": wmi_class},
                "raw": {"command": cmd, "exit_code": rc},
            }]

        return self._parse_and_normalize(out, ts, kind_label, wmi_class, cmd)

    def _parse_and_normalize(
        self,
        stdout: str,
        ts: float,
        kind_label: str,
        wmi_class: str,
        cmd: str,
    ) -> list[dict[str, Any]]:
        try:
            data = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "error",
                "message": f"WMI JSON decode failed for {kind_label}",
                "value": None,
                "labels": {"wmi_class": wmi_class},
                "raw": {"command": cmd, "stdout_preview": (stdout or "")[:500]},
            }]

        objects: list[dict[str, Any]]
        if isinstance(data, dict):
            objects = [data]
        elif isinstance(data, list):
            objects = data
        else:
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "error",
                "message": f"WMI returned unexpected type {type(data).__name__}",
                "value": None,
                "labels": {"wmi_class": wmi_class},
                "raw": {"command": cmd, "data_type": type(data).__name__},
            }]

        records: list[dict[str, Any]] = []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            records.append(self._normalize_record(ts, obj, kind_label, wmi_class))
        return records

    def _normalize_record(
        self,
        ts: float,
        obj: dict[str, Any],
        kind_label: str,
        wmi_class: str,
    ) -> dict[str, Any]:
        caption = obj.get("Caption", "")
        if isinstance(caption, str):
            caption = caption.strip()
        device_id = obj.get("DeviceID", "")
        name_val = obj.get("Name", "")
        version = obj.get("Version", "")

        message_parts: list[str] = []
        if caption:
            message_parts.append(caption)
        caption_str = caption if isinstance(caption, str) else ""
        if isinstance(name_val, str) and name_val.strip() and name_val.strip() not in caption_str:
            message_parts.append(str(name_val).strip())
        if version:
            message_parts.append(f"v{version}")

        identifier = str(device_id) if device_id else caption or kind_label
        message = f"{kind_label}: {' '.join(message_parts)}" if message_parts else f"{kind_label}: {identifier}"

        labels: dict[str, Any] = {
            "wmi_class": wmi_class,
            "target": kind_label.lower().replace(" ", "_"),
        }
        for key in (
            "Caption", "Name", "Version", "DeviceID", "Manufacturer",
            "Model", "SerialNumber", "Status", "State", "ProcessName",
            "Architecture", "NumberOfCores", "NumberOfLogicalProcessors",
            "MaxClockSpeed", "CurrentClockSpeed", "SocketDesignation",
            "Capacity", "Speed", "PartNumber", "MemoryType", "FormFactor",
            "Size", "FreeSpace", "FileSystem", "VolumeName",
            "MACAddress", "AdapterType", "NetConnectionID", "NetConnectionStatus",
            "SMBIOSBIOSVersion", "ManufacturerName", "ReleaseDate",
            "TotalPhysicalMemory", "NumberOfProcessors", "Domain",
            "DNSHostName", "SystemType", "BootupState",
            "OSArchitecture", "BuildNumber", "RegisteredUser",
            "InstallDate", "LastBootUpTime",
        ):
            val = obj.get(key)
            if val is not None and val != "":
                labels[key.lower()] = val

        value: float | None = None
        with contextlib.suppress(ValueError, TypeError):
            numeric = obj.get("TotalVisibleMemorySize") or obj.get("Size") or obj.get("Capacity")
            if numeric is not None:
                value = float(numeric)

        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": "ok",
            "message": message,
            "value": value,
            "labels": labels,
            "raw": {"command": f"Get-CimInstance -ClassName {wmi_class}", "data": obj},
        }
