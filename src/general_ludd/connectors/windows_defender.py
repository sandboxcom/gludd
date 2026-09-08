"""Collect Windows Defender state through safe PowerShell argv calls.

Supports Get-MpComputerStatus, Get-MpPreference, Get-MpThreatDetection, and
Start-MpScan through an injectable, non-shell runner.

Security: all caller-supplied values are validated to reject leading-dash
(option-injection guard) and shell metacharacters before they touch argv.
"""

from __future__ import annotations

import time
from typing import Any

from general_ludd.connectors.windows_defender_support import (
    VALID_SCAN_TYPES as _VALID_SCAN_TYPES,
)
from general_ludd.connectors.windows_defender_support import (
    VALID_TARGETS as _VALID_TARGETS,
)
from general_ludd.connectors.windows_defender_support import (
    Runner,
)
from general_ludd.connectors.windows_defender_support import (
    default_runner as _default_runner,
)
from general_ludd.connectors.windows_defender_support import (
    normalize_record as _normalize_record,
)
from general_ludd.connectors.windows_defender_support import (
    parse_json_stdout as _parse_json_stdout,
)
from general_ludd.connectors.windows_defender_support import (
    ps_command as _ps_command,
)
from general_ludd.connectors.windows_defender_support import (
    run as _run,
)
from general_ludd.connectors.windows_defender_support import (
    validate_arg as _validate_arg,
)


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
        """Initialize connector configuration and its injectable runner."""
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "windows_defender"))
        self._runner: Runner = runner if runner is not None else _default_runner

    # -- health ---------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe Get-MpComputerStatus; return ``{'ok': bool, 'detail': str}``.

        Never raises — all exceptions are caught.
        """
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
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
            raise ValueError(f"unknown target {target_raw!r}; valid: {sorted(_VALID_TARGETS)}")

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

    def _normalize_computer_status(self, stdout: str, command: str) -> list[dict[str, Any]]:
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

    def _normalize_preferences(self, stdout: str, command: str) -> list[dict[str, Any]]:
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

    def _normalize_threats(self, stdout: str, command: str) -> list[dict[str, Any]]:
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
            raise ValueError(f"allow_mutate must be a bool, got {type(allow_mutate).__name__}")
        if allow_mutate is not True:
            ts = time.time()
            return [
                {
                    "ts": ts,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "blocked",
                    "message": "Start-MpScan requires spec['allow_mutate'] == True",
                    "value": None,
                    "labels": {"allow_mutate": allow_mutate},
                    "raw": None,
                }
            ]

        scan_type = str(spec.get("scan_type", "QuickScan"))
        _validate_arg(scan_type, field="scan_type")
        if scan_type not in _VALID_SCAN_TYPES:
            raise ValueError(f"scan_type must be one of {sorted(_VALID_SCAN_TYPES)}, got {scan_type!r}")

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
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-MpPreference | Select-Object ExclusionPath,ExclusionExtension,"
            "ExclusionProcess | ConvertTo-Json -Depth 5",
        ]
        rc, out, _err = _run(self._runner, argv)
        if rc != 0:
            return []
        return self._normalize_exclusions(out, "Get-MpPreference | Select-Object ExclusionPath,...")

    def _normalize_exclusions(self, stdout: str, command: str) -> list[dict[str, Any]]:
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
