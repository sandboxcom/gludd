"""libimobiledevice connector — iOS device diagnostics bridge.

Provides programmatic access to iOS devices via libimobiledevice tools:
ideviceinfo, idevicesyslog, idevicediagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class IDeviceConnector:
    """Query iOS devices via libimobiledevice."""

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        run: Callable[..., RunResult] | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "libimobiledevice"))
        self._run = run or self._default_run
        self._udid: str | None = self.config.get("udid")

    @staticmethod
    def _default_run(args: list[str], timeout: int | None = None) -> RunResult:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout or 30)
            return RunResult(r.returncode, r.stdout, r.stderr)
        except FileNotFoundError:
            return RunResult(1, "", f"command not found: {args[0]}")

    def health(self) -> dict[str, Any]:
        try:
            result = self._run(["idevice_id", "-l"])
            if result.exit_code != 0 or not result.stdout.strip():
                return {"ok": False, "detail": "no device found"}
            udid = result.stdout.strip().split("\n")[0]
            return {"ok": True, "detail": f"device connected: {udid}"}
        except Exception as e:
            return {"ok": False, "detail": str(e)}

    def ideviceinfo(self, domain: str | None = None) -> dict[str, Any]:
        try:
            args = ["ideviceinfo"]
            if self._udid:
                args.extend(["-u", self._udid])
            if domain:
                args.extend(["-q", domain])
            result = self._run(args)
            if result.exit_code != 0:
                return {}
            out: dict[str, Any] = {}
            for line in result.stdout.strip().split("\n"):
                if ": " in line:
                    k, v = line.split(": ", 1)
                    out[k.strip()] = v.strip()
            return out
        except Exception:
            return {}

    def idevicesyslog(self, lines: int = 100) -> list[dict[str, Any]]:
        try:
            args = ["idevicesyslog"]
            if self._udid:
                args.extend(["-u", self._udid])
            result = self._run(args)
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            entries: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n")[:lines]:
                stripped = line.strip()
                if not stripped:
                    continue
                parts = stripped.split(None, 5)
                if len(parts) >= 6:
                    process_name = parts[4]
                    if "[" in process_name:
                        process_name = process_name.split("[")[0]
                    message = parts[5]
                    if ": " in message and message.startswith("<"):
                        message = message.split(": ", 1)[1]
                    entries.append({
                        "process": process_name,
                        "message": message,
                    })
            return entries
        except Exception:
            return []

    def idevicediagnostics(self, diagnostic_type: str = "All") -> str:
        try:
            args = ["idevicediagnostics", "diagnostics", diagnostic_type]
            if self._udid:
                args.extend(["-u", self._udid])
            result = self._run(args)
            return result.stdout if result.exit_code == 0 else ""
        except Exception:
            return ""

    def idevice_id(self) -> list[str]:
        try:
            result = self._run(["idevice_id", "-l"])
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            return [u for u in result.stdout.strip().split("\n") if u.strip()]
        except Exception:
            return []

    def idevicepair(self, action: str = "validate") -> dict[str, Any]:
        try:
            args = ["idevicepair", action]
            if self._udid:
                args.extend(["-u", self._udid])
            result = self._run(args)
            return {
                "action": action,
                "success": result.exit_code == 0,
                "output": result.stdout,
            }
        except Exception:
            return {"action": action, "success": False, "output": ""}

    def oslog(self, predicate: str | None = None, lines: int = 100) -> list[dict[str, Any]]:
        try:
            args = ["log", "stream", "--style", "compact"]
            if predicate:
                args.extend(["--predicate", predicate])
            result = self._run(args)
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            entries: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n")[:lines]:
                stripped = line.strip()
                if not stripped:
                    continue
                entries.append({"message": stripped})
            return entries
        except Exception:
            return []

    def installed_profiles(self) -> list[dict[str, Any]]:
        try:
            args = ["ideviceprovision", "list"]
            if self._udid:
                args.extend(["-u", self._udid])
            result = self._run(args)
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            entries: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                stripped = line.strip()
                if stripped:
                    entries.append({"profile": stripped})
            return entries
        except Exception:
            return []

    def disk_usage(self) -> dict[str, Any]:
        try:
            info = self.ideviceinfo()
            return info if info else {}
        except Exception:
            return {}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            action = spec.get("action", "")
            if action == "ideviceinfo":
                info = self.ideviceinfo(domain=spec.get("domain"))
                return [{"action": action, "data": {"info": info}}]
            elif action == "device_id":
                udids = self.idevice_id()
                return [{"action": action, "data": {"udids": udids}}]
            elif action == "pair":
                pair_result = self.idevicepair(action=spec.get("pair_action", "validate"))
                return [{"action": action, "data": {"success": pair_result["success"]}}]
            elif action == "disk_usage":
                usage = self.disk_usage()
                return [{"action": action, "data": {"usage": usage}}]
            return []
        except Exception:
            return []


IDeviceSource = IDeviceConnector
