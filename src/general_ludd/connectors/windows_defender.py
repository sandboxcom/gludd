"""Windows Defender connector — Get-MpComputerStatus, Get-MpPreference,
Get-MpThreatDetection, Start-MpScan wrappers.

Self-contained source: imports nothing from sibling connectors. All subprocess
calls use a LIST argv (never ``shell=True``) and an injectable runner so unit
tests never need the real binaries.

Security: all caller-supplied values are validated to reject leading-dash
(option-injection guard) and shell metacharacters before they touch argv.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from typing import Any

RunnerResult = tuple[int, str, str] | str
Runner = Callable[[list[str]], RunnerResult]

_SHELL_METACHARS: frozenset[str] = frozenset(";&|`$<>(){}[]!*?#~\n\r\t \"'")

_DEFAULT_TIMEOUT = 30.0

_VALID_TARGETS = frozenset({
    "status", "computer_status",
    "preferences", "mp_preference",
    "threats", "threat_detection",
    "scan", "start_scan",
    "exclusions", "get_exclusions",
})

_VALID_SCAN_TYPES = frozenset({"QuickScan", "FullScan"})


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


def _ps_command(cmdlet: str) -> list[str]:
    """Build a PowerShell argv for a cmdlet piped to ConvertTo-Json."""
    return [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        f"{cmdlet} | ConvertTo-Json -Depth 5",
    ]


def _parse_json_stdout(stdout: str) -> list[dict[str, Any]]:
    """Parse stdout as JSON; wrap a single object in a list."""
    text = (stdout or "").strip()
    if not text:
        return []
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _run(runner: Runner, argv: list[str]) -> tuple[int, str, str]:
    """Normalize injected runners while keeping the production tuple contract.

    E2E callers commonly provide a function returning canned stdout directly;
    production runners return ``(returncode, stdout, stderr)``.  Treat a string
    result as successful stdout and retain the strict list-argv invocation.
    """
    try:
        result = runner(argv)
    except Exception as exc:
        return 127, "", str(exc)
    if isinstance(result, str):
        return 0, result, ""
    if isinstance(result, tuple) and len(result) == 3:
        rc, stdout, stderr = result
        return int(rc), str(stdout or ""), str(stderr or "")
    raise TypeError("runner must return stdout or (returncode, stdout, stderr)")


def _normalize_record(
    raw_item: dict[str, Any],
    ts: float,
    source: str,
    kind: str,
    *,
    level_or_status: str = "info",
    message: str = "",
    command: str = "",
) -> dict[str, Any]:
    return {
        "ts": ts,
        "source": source,
        "kind": kind,
        "level_or_status": level_or_status,
        "message": message or str(raw_item.get("message", json.dumps(raw_item))),
        "value": None,
        "labels": dict(raw_item),
        "raw": {"command": command, "raw": raw_item},
    }


class WindowsDefenderConnector:
    """Query Windows Defender via PowerShell cmdlets.

    Parameters
    ----------
    config:
        Arbitrary mapping; ``name`` key sets the log-record source name.
    runner:
        Optional injected command runner ``(argv) -> (rc, stdout, stderr)``.
        Defaults to a ``subprocess.run`` LIST-argv runner (never ``shell=True``).
    """

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "windows_defender"))
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe Get-MpComputerStatus; return ``{'ok': bool, 'detail': str}``.

        Never raises — all exceptions are caught.
        """
        argv = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-MpComputerStatus | Select-Object AntivirusEnabled,AMServiceEnabled,"
            "AntispywareEnabled,RealTimeProtectionEnabled | ConvertTo-Json",
        ]
        try:
            rc, out, err = _run(self._runner, argv)
            if rc == 0:
                return {"ok": True, "detail": "Get-MpComputerStatus responded"}
            detail = (err or out or "").strip() or f"exit code {rc}"
            return {"ok": False, "detail": detail}
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    # -- query ----------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a Defender probe and return normalized records.

        ``spec['target']`` selects the probe:

        ======================  =============================================
        target                   PowerShell cmdlet
        ======================  =============================================
        ``"status"``             ``Get-MpComputerStatus``
        ``"computer_status"``    ``Get-MpComputerStatus`` (alias)
        ``"preferences"``        ``Get-MpPreference``
        ``"mp_preference"``      ``Get-MpPreference`` (alias)
        ``"threats"``            ``Get-MpThreatDetection``
        ``"threat_detection"``   ``Get-MpThreatDetection`` (alias)
        ``"scan"``               ``Start-MpScan`` (mutating)
        ``"start_scan"``         ``Start-MpScan`` (alias, mutating)
        ``"exclusions"``         ``Get-MpPreference`` (exclusion fields)
        ``"get_exclusions"``     ``Get-MpPreference`` (exclusion fields, alias)
        ======================  =============================================

        Additional ``spec`` keys:
          ``allow_mutate`` — required ``True`` for ``"scan"`` target.
          ``scan_type`` — ``"QuickScan"`` (default) or ``"FullScan"``.

        Returns an empty list on non-zero exit.
        """
        spec = spec or {}
        target_raw = str(spec.get("target", "status")).strip().lower()
        _validate_arg(target_raw, field="target")

        if target_raw not in _VALID_TARGETS:
            raise ValueError(
                f"unknown target {target_raw!r}; valid: {sorted(_VALID_TARGETS)}"
            )

        if target_raw in ("status", "computer_status"):
            return self._run_get_mp_computer_status()
        if target_raw in ("preferences", "mp_preference"):
            return self._run_get_mp_preference()
        if target_raw in ("threats", "threat_detection"):
            return self._run_get_mp_threat_detection()
        if target_raw in ("scan", "start_scan"):
            return self._run_start_mp_scan(spec)
        if target_raw in ("exclusions", "get_exclusions"):
            return self._run_get_exclusions()
        return []

    # -- Get-MpComputerStatus -------------------------------------------------

    def _run_get_mp_computer_status(self) -> list[dict[str, Any]]:
        argv = _ps_command("Get-MpComputerStatus")
        rc, out, _err = _run(self._runner, argv)
        if rc != 0:
            return []
        return self._normalize_computer_status(out, "Get-MpComputerStatus")

    def _normalize_computer_status(
        self, stdout: str, command: str
    ) -> list[dict[str, Any]]:
        ts = time.time()
        items = _parse_json_stdout(stdout)
        return [
            _normalize_record(
                item,
                ts,
                source=self.name,
                kind=self.KIND,
                message=f"Defender status: {item.get('AMServiceEnabled', 'unknown')}",
                command=command,
            )
            for item in items
        ]

    # -- Get-MpPreference -----------------------------------------------------

    def _run_get_mp_preference(self) -> list[dict[str, Any]]:
        argv = _ps_command("Get-MpPreference")
        rc, out, _err = _run(self._runner, argv)
        if rc != 0:
            return []
        return self._normalize_preferences(out, "Get-MpPreference")

    def _normalize_preferences(
        self, stdout: str, command: str
    ) -> list[dict[str, Any]]:
        ts = time.time()
        items = _parse_json_stdout(stdout)
        return [
            _normalize_record(
                item,
                ts,
                source=self.name,
                kind=self.KIND,
                message=(
                    "Defender preferences "
                    f"(DisableRealtimeMonitoring={item.get('DisableRealtimeMonitoring', 'unknown')})"
                ),
                command=command,
            )
            for item in items
        ]

    # -- Get-MpThreatDetection ------------------------------------------------

    def _run_get_mp_threat_detection(self) -> list[dict[str, Any]]:
        argv = _ps_command("Get-MpThreatDetection")
        rc, out, _err = _run(self._runner, argv)
        if rc != 0:
            return []
        return self._normalize_threats(out, "Get-MpThreatDetection")

    def _normalize_threats(
        self, stdout: str, command: str
    ) -> list[dict[str, Any]]:
        ts = time.time()
        items = _parse_json_stdout(stdout)
        results: list[dict[str, Any]] = []
        for item in items:
            threat_name = item.get("ThreatName", "unknown")
            severity = item.get("SeverityName", "unknown")
            status = item.get("StatusName", "unknown")
            results.append(
                _normalize_record(
                    item,
                    ts,
                    source=self.name,
                    kind=self.KIND,
                    level_or_status=severity.lower() if isinstance(severity, str) else "info",
                    message=f"Threat: {threat_name} | Status: {status} | Severity: {severity}",
                    command=command,
                )
            )
        return results

    # -- Start-MpScan (mutating) ----------------------------------------------

    def _run_start_mp_scan(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        allow_mutate = spec.get("allow_mutate")
        if not isinstance(allow_mutate, bool):
            raise ValueError(
                f"allow_mutate must be a bool, got {type(allow_mutate).__name__}"
            )
        if allow_mutate is not True:
            ts = time.time()
            return [{
                "ts": ts,
                "source": self.name,
                "kind": self.KIND,
                "level_or_status": "blocked",
                "message": "Start-MpScan requires spec['allow_mutate'] == True",
                "value": None,
                "labels": {"allow_mutate": allow_mutate},
                "raw": None,
            }]

        scan_type = str(spec.get("scan_type", "QuickScan"))
        _validate_arg(scan_type, field="scan_type")
        if scan_type not in _VALID_SCAN_TYPES:
            raise ValueError(
                f"scan_type must be one of {sorted(_VALID_SCAN_TYPES)}, got {scan_type!r}"
            )

        argv = _ps_command(f"Start-MpScan -ScanType {scan_type}")
        rc, out, _err = _run(self._runner, argv)
        ts = time.time()

        if rc != 0:
            return []

        items = _parse_json_stdout(out)
        return [
            _normalize_record(
                item,
                ts,
                source=self.name,
                kind=self.KIND,
                message=f"Started {scan_type} scan",
                command=f"Start-MpScan -ScanType {scan_type}",
            )
            for item in items
        ]

    # -- exclusions via Get-MpPreference --------------------------------------

    def _run_get_exclusions(self) -> list[dict[str, Any]]:
        argv = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-MpPreference | Select-Object ExclusionPath,ExclusionExtension,"
            "ExclusionProcess | ConvertTo-Json -Depth 5",
        ]
        rc, out, _err = _run(self._runner, argv)
        if rc != 0:
            return []
        return self._normalize_exclusions(out, "Get-MpPreference | Select-Object ExclusionPath,...")

    def _normalize_exclusions(
        self, stdout: str, command: str
    ) -> list[dict[str, Any]]:
        ts = time.time()
        items = _parse_json_stdout(stdout)
        results: list[dict[str, Any]] = []
        for item in items:
            paths = item.get("ExclusionPath", [])
            extensions = item.get("ExclusionExtension", [])
            processes = item.get("ExclusionProcess", [])
            total = (
                len(paths if isinstance(paths, list) else [])
                + len(extensions if isinstance(extensions, list) else [])
                + len(processes if isinstance(processes, list) else [])
            )
            results.append(
                _normalize_record(
                    item,
                    ts,
                    source=self.name,
                    kind=self.KIND,
                    message=f"Defender exclusions: {total} total",
                    command=command,
                )
            )
        return results


WindowsDefenderSource = WindowsDefenderConnector
