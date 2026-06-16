"""Unit tests for the Windows Event Log connector (mocked runner).

Covers: canned wevtutil-json AND PowerShell ConvertTo-Json normalization (both
shapes), channel-name injection rejection (runner never called), and
``health()`` ok/not-ok/never-raises.
"""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.windows_event import KIND, WindowsEventSource


class RecordingRunner:
    """A fake runner returning canned text; records the argv it got."""

    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return self.output


# wevtutil /f:json shape (flat keys, ISO timestamp, numeric Level).
WEVTUTIL_JSON = json.dumps(
    [
        {
            "Channel": "System",
            "ProviderName": "Service Control Manager",
            "EventID": 7036,
            "Level": 2,
            "Computer": "WIN-HOST",
            "TimeCreated": "2023-11-14T22:13:20+00:00",
            "Message": "The Print Spooler service entered the running state.",
        }
    ]
)

# Get-WinEvent | ConvertTo-Json shape (LevelDisplayName, LogName, MachineName,
# /Date(...)/ timestamp). ConvertTo-Json emits a single object for one event.
POWERSHELL_JSON = json.dumps(
    {
        "LogName": "Application",
        "ProviderName": "MSSQLSERVER",
        "Id": 17890,
        "LevelDisplayName": "Error",
        "MachineName": "WIN-HOST",
        "TimeCreated": "/Date(1700000000000)/",
        "Message": "A significant part of sql server process memory has been paged out.",
    }
)


class TestWindowsKind:
    def test_kind_is_logs(self) -> None:
        assert KIND == "logs"
        assert WindowsEventSource().KIND == "logs"


class TestWevtutilNormalization:
    def test_wevtutil_json_normalized(self) -> None:
        runner = RecordingRunner(WEVTUTIL_JSON)
        src = WindowsEventSource({"name": "win", "backend": "wevtutil"}, runner=runner)
        recs = src.query({"channel": "System"})
        assert len(recs) == 1
        r = recs[0]
        assert set(r) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert r["source"] == "win"
        assert r["kind"] == "logs"
        assert r["level_or_status"] == "error"  # Level 2
        assert r["message"].startswith("The Print Spooler")
        assert r["ts"] == pytest.approx(1700000000.0)
        assert r["labels"] == {
            "channel": "System",
            "provider": "Service Control Manager",
            "event_id": 7036,
            "computer": "WIN-HOST",
        }

    def test_wevtutil_argv_shape(self) -> None:
        runner = RecordingRunner(WEVTUTIL_JSON)
        src = WindowsEventSource({"backend": "wevtutil"}, runner=runner)
        src.query({"channel": "System", "count": 5})
        argv = runner.calls[0]
        assert argv[0] == "wevtutil"
        assert argv[1] == "qe"
        assert "System" in argv
        assert "/f:json" in argv
        assert "/c:5" in argv
        assert all(isinstance(tok, str) for tok in argv)


class TestPowerShellNormalization:
    def test_powershell_json_normalized(self) -> None:
        runner = RecordingRunner(POWERSHELL_JSON)
        src = WindowsEventSource({"name": "win", "backend": "powershell"}, runner=runner)
        recs = src.query({"channel": "Application"})
        assert len(recs) == 1
        r = recs[0]
        assert r["level_or_status"] == "error"  # LevelDisplayName "Error"
        assert r["ts"] == pytest.approx(1700000000.0)  # /Date(ms)/
        assert r["labels"] == {
            "channel": "Application",
            "provider": "MSSQLSERVER",
            "event_id": 17890,
            "computer": "WIN-HOST",
        }

    def test_powershell_argv_shape(self) -> None:
        runner = RecordingRunner(POWERSHELL_JSON)
        src = WindowsEventSource({"backend": "powershell"}, runner=runner)
        src.query({"channel": "Application", "count": 3})
        argv = runner.calls[0]
        assert argv[0] == "powershell"
        assert "-Command" in argv
        assert any("Get-WinEvent" in tok and "Application" in tok for tok in argv)


class TestWindowsInjectionRejected:
    @pytest.mark.parametrize(
        "bad_channel",
        ["System; shutdown /r", "$(reboot)", "-LogName", "App`whoami`", "Sys|tem", "a&b"],
    )
    def test_injection_rejected_runner_not_called(self, bad_channel: str) -> None:
        runner = RecordingRunner(WEVTUTIL_JSON)
        src = WindowsEventSource(runner=runner)
        with pytest.raises(ValueError):
            src.query({"channel": bad_channel})
        assert runner.calls == []

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError):
            WindowsEventSource({"backend": "bash"}, runner=RecordingRunner())


class TestWindowsHealth:
    def test_health_ok(self) -> None:
        h = WindowsEventSource(runner=RecordingRunner(WEVTUTIL_JSON)).health()
        assert h["ok"] is True
        assert "reachable" in h["detail"]

    def test_health_not_ok(self) -> None:
        def boom(argv: list[str]) -> str:
            raise FileNotFoundError("wevtutil not found")

        h = WindowsEventSource(runner=boom).health()
        assert h["ok"] is False
        assert "unavailable" in h["detail"]

    def test_health_never_raises(self) -> None:
        def boom(argv: list[str]) -> str:
            raise RuntimeError("kaboom")

        h = WindowsEventSource(runner=boom).health()
        assert isinstance(h, dict) and h["ok"] is False
