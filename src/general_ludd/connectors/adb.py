"""ADB bridge connector — Android Debug Bridge for diagnostics.

Provides programmatic access to Android devices via ADB for logcat,
dumpsys, getprop, and package management queries.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class AdbConnector:
    """Query Android devices via ADB bridge."""

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        run: Callable[..., RunResult] | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "adb"))
        self._run = run or self._default_run
        self._serial: str | None = self.config.get("serial")

    @staticmethod
    def _default_run(args: list[str], timeout: int | None = None) -> RunResult:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout or 30)
            return RunResult(r.returncode, r.stdout, r.stderr)
        except FileNotFoundError:
            return RunResult(1, "", f"command not found: {args[0]}")

    def _adb_args(self, *extra: str) -> list[str]:
        args = ["adb"]
        if self._serial:
            args.extend(["-s", self._serial])
        args.extend(extra)
        return args

    def health(self) -> dict[str, Any]:
        try:
            ver = self._run(self._adb_args("version"))
            if ver.exit_code != 0:
                return {"ok": False, "detail": "adb not found"}
            dev = self._run(self._adb_args("devices"))
            if dev.exit_code != 0:
                return {"ok": False, "detail": "adb devices failed"}
            lines = dev.stdout.strip().split("\n")[1:]
            devices = [entry for entry in lines if entry.strip() and "\t" in entry]
            if not devices:
                return {"ok": False, "detail": "no device connected"}
            return {"ok": True, "detail": f"{len(devices)} device(s) connected"}
        except Exception as e:
            return {"ok": False, "detail": str(e) if "found" not in str(e).lower() else "adb not found"}

    def shell(self, command: str) -> str:
        try:
            result = self._run(self._adb_args("shell", command))
            return result.stdout if result.exit_code == 0 else ""
        except Exception:
            return ""

    def list_packages(self, flag: str = "-3") -> list[dict[str, Any]]:
        try:
            result = self._run(self._adb_args("shell", "pm", "list", "packages", flag))
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            pkgs: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("package:"):
                    pkgs.append({"package": line[len("package:"):], "flag": flag})
            return pkgs
        except Exception:
            return []

    def getprop(self, key: str | None = None) -> dict[str, Any]:
        try:
            if key:
                result = self._run(self._adb_args("shell", "getprop", key))
            else:
                result = self._run(self._adb_args("shell", "getprop"))
            if result.exit_code != 0 or not result.stdout.strip():
                return {}
            out: dict[str, Any] = {}
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("[") and "]: [" in line:
                    k, v = line[1:].split("]: [", 1)
                    out[k.strip()] = v.rstrip("]").strip()
            return out
        except Exception:
            return {}

    def dumpsys(self, service: str) -> str:
        try:
            result = self._run(self._adb_args("shell", "dumpsys", service))
            return result.stdout if result.exit_code == 0 else ""
        except Exception:
            return ""

    def logcat(self, lines: int = 100) -> list[dict[str, Any]]:
        try:
            result = self._run(self._adb_args(
                "logcat", "-d", "-t", str(lines), "-b", "main", "-v", "threadtime"
            ))
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            entries: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                parts = stripped.split(None, 6)
                if len(parts) >= 7:
                    tag = parts[5][:-1] if parts[5].endswith(":") else parts[5]
                    with contextlib.suppress(ValueError, IndexError):
                        entries.append({
                            "pid": int(parts[2]),
                            "tid": int(parts[3]),
                            "level": parts[4],
                            "tag": tag,
                            "message": parts[6],
                        })
            return entries
        except Exception:
            return []

    def devices(self) -> list[dict[str, Any]]:
        try:
            result = self._run(self._adb_args("devices"))
            if result.exit_code != 0:
                return []
            devs: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n")[1:]:
                stripped = line.strip()
                if not stripped or "\t" not in stripped:
                    continue
                serial, state = stripped.split("\t", 1)
                devs.append({"serial": serial.strip(), "state": state.strip()})
            return devs
        except Exception:
            return []

    def pm_list(self, flag: str = "-s") -> list[str]:
        try:
            result = self._run(self._adb_args("shell", "pm", "list", "packages", flag))
            if result.exit_code != 0 or not result.stdout.strip():
                return []
            pkgs: list[str] = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("package:"):
                    pkgs.append(line[len("package:"):])
            return pkgs
        except Exception:
            return []

    def am_start(self, component: str) -> str:
        try:
            result = self._run(self._adb_args("shell", "am", "start", "-n", component))
            return result.stdout if result.exit_code == 0 else ""
        except Exception:
            return ""

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            action = spec.get("action", "")
            if action == "shell":
                out = self.shell(spec.get("command", ""))
                return [{"action": action, "data": {"output": out}}]
            elif action == "getprop":
                props = self.getprop(key=spec.get("key"))
                return [{"action": action, "data": {"properties": props}}]
            elif action == "devices":
                devs = self.devices()
                return [{"action": action, "data": {"devices": devs}}]
            return []
        except Exception:
            return []


AdbSource = AdbConnector
