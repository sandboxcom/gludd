"""Unit tests for the self-contained Windows Event Log connector.

Everything runs with an *injected* command runner returning canned
``Get-WinEvent`` / ``wevtutil`` JSON — no real Windows, no real subprocess, no
shell. These tests must import and pass on any platform.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from general_ludd.connectors.windows_event_log import (
    WindowsEventLogSource,
    _default_runner,
)

# --------------------------------------------------------------------------- #
# Canned collector outputs
# --------------------------------------------------------------------------- #

# Get-WinEvent | ConvertTo-Json with TWO events -> a JSON list.
GET_WINEVENT_JSON_LIST = json.dumps(
    [
        {
            "TimeCreated": "/Date(1718000000000)/",
            "LevelDisplayName": "Error",
            "Level": 2,
            "Message": "The service terminated unexpectedly.",
            "Id": 7034,
            "ProviderName": "Service Control Manager",
            "LogName": "System",
            "MachineName": "WIN-HOST-01",
        },
        {
            "TimeCreated": "/Date(1718000600000)/",
            "LevelDisplayName": "Warning",
            "Level": 3,
            "Message": "A timeout was reached.",
            "Id": 7011,
            "ProviderName": "Service Control Manager",
            "LogName": "System",
            "MachineName": "WIN-HOST-01",
        },
    ]
)

# Get-WinEvent | ConvertTo-Json with ONE event -> a single JSON object.
GET_WINEVENT_JSON_SINGLE = json.dumps(
    {
        "TimeCreated": "2026-06-12T10:00:00",
        "LevelDisplayName": "Information",
        "Level": 4,
        "Message": "Started.",
        "Id": 1,
        "ProviderName": "EventLog",
        "Channel": "Application",
        "MachineName": "WIN-HOST-02",
    }
)

# wevtutil qe /f:json -> {"Events": [...]}
WEVTUTIL_JSON = json.dumps(
    {
        "Events": [
            {
                "TimeCreated": "2026-06-12T09:30:00+00:00",
                "Level": 2,
                "Message": "Disk error.",
                "Id": 51,
                "ProviderName": "disk",
                "Channel": "System",
                "MachineName": "WIN-HOST-03",
            }
        ]
    }
)


class RecordingRunner:
    """An injectable runner that records argv and replays a canned result."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return self.rc, self.stdout, self.stderr


# --------------------------------------------------------------------------- #
# Import / construction
# --------------------------------------------------------------------------- #


def test_module_imports_and_class_kind_logs() -> None:
    assert WindowsEventLogSource.KIND == "logs"


def test_name_default_and_override() -> None:
    runner = RecordingRunner()
    src = WindowsEventLogSource({"channel": "System"}, runner=runner)
    assert src.name == "wineventlog:System"
    assert src.KIND == "logs"

    named = WindowsEventLogSource({"name": "my-logs"}, runner=runner)
    assert named.name == "my-logs"


def test_unknown_backend_rejected() -> None:
    with pytest.raises(ValueError):
        WindowsEventLogSource({"backend": "bash"}, runner=RecordingRunner())


# --------------------------------------------------------------------------- #
# Normalization — Get-WinEvent JSON
# --------------------------------------------------------------------------- #


def test_query_powershell_list_normalizes_two_events() -> None:
    runner = RecordingRunner(stdout=GET_WINEVENT_JSON_LIST)
    src = WindowsEventLogSource({"backend": "powershell", "channel": "System"}, runner=runner)

    records = src.query({"channel": "System", "level": "Error", "count": 50})

    assert len(records) == 2
    first = records[0]
    # TimeCreated /Date(ms)/ -> unix seconds.
    assert first["ts"] == pytest.approx(1718000000.0)
    assert first["source"] == "wineventlog:System"
    assert first["kind"] == "logs"
    assert first["level_or_status"] == "Error"
    assert first["message"] == "7034: The service terminated unexpectedly."
    assert first["value"] == 7034.0
    assert first["labels"]["provider"] == "Service Control Manager"
    assert first["labels"]["id"] == "7034"
    assert first["labels"]["channel"] == "System"
    assert first["labels"]["machine"] == "WIN-HOST-01"
    assert first["raw"]["Id"] == 7034

    assert records[1]["level_or_status"] == "Warning"


def test_query_powershell_single_object_normalizes() -> None:
    runner = RecordingRunner(stdout=GET_WINEVENT_JSON_SINGLE)
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "Application"})

    assert len(records) == 1
    rec = records[0]
    # ISO-8601 TimeCreated parsed.
    assert rec["ts"] is not None
    assert rec["level_or_status"] == "Information"
    assert rec["message"] == "1: Started."
    assert rec["labels"]["channel"] == "Application"


def test_level_mapping_from_numeric_when_display_missing() -> None:
    payload = json.dumps({"TimeCreated": "2026-06-12T10:00:00", "Level": 3, "Message": "x", "Id": 9})
    runner = RecordingRunner(stdout=payload)
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({})
    assert records[0]["level_or_status"] == "Warning"


# --------------------------------------------------------------------------- #
# Normalization — wevtutil JSON
# --------------------------------------------------------------------------- #


def test_query_wevtutil_normalizes() -> None:
    runner = RecordingRunner(stdout=WEVTUTIL_JSON)
    src = WindowsEventLogSource({"backend": "wevtutil", "channel": "System"}, runner=runner)

    records = src.query({"channel": "System", "count": 10})

    assert len(records) == 1
    rec = records[0]
    assert rec["ts"] is not None
    assert rec["level_or_status"] == "Error"  # mapped from Level=2
    assert rec["message"] == "Disk error."
    assert rec["value"] == 51.0
    assert rec["labels"]["provider"] == "disk"
    assert rec["labels"]["channel"] == "System"


# --------------------------------------------------------------------------- #
# argv shape — list form, shell never True
# --------------------------------------------------------------------------- #


def test_powershell_query_argv_is_list_form_and_safe() -> None:
    runner = RecordingRunner(stdout="[]")
    src = WindowsEventLogSource({"backend": "powershell", "channel": "System"}, runner=runner)
    src.query({"channel": "System", "level": "Error", "count": 25, "provider": "EventLog"})

    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert argv[0] == "powershell"
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert "-Command" in argv
    command = argv[argv.index("-Command") + 1]
    assert "Get-WinEvent" in command
    assert "-FilterHashtable" in command
    assert "LogName='System'" in command
    assert "Level=2" in command  # Error -> 2
    assert "-MaxEvents 25" in command
    assert "ProviderName='EventLog'" in command
    assert "ConvertTo-Json" in command


def test_wevtutil_query_argv_is_list_form() -> None:
    runner = RecordingRunner(stdout=WEVTUTIL_JSON)
    src = WindowsEventLogSource({"backend": "wevtutil", "channel": "System"}, runner=runner)
    src.query({"channel": "System", "count": 7})

    argv = runner.calls[0]
    assert argv == ["wevtutil", "qe", "System", "/f:json", "/c:7", "/rd:true"]


def test_default_runner_uses_list_argv_never_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Proc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    import general_ludd.connectors.windows_event_log as mod

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc, _out, _err = _default_runner(["wevtutil", "gli", "System"])

    assert rc == 0
    assert isinstance(captured["args"], list)
    assert captured["kwargs"].get("shell") is False
    assert captured["kwargs"].get("timeout") is not None


# --------------------------------------------------------------------------- #
# Injection-y inputs rejected — runner is NEVER invoked
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "channel",
    [
        "System; rm -rf /",
        "-MaliciousOption",
        "System$(whoami)",
        "System`whoami`",
        "System | Out-File evil",
        "System' ; Stop-Computer #",
        "../etc/passwd\nSystem",
        "Sys&tem",
    ],
)
def test_malicious_channel_rejected_runner_not_called(channel: str) -> None:
    runner = RecordingRunner(stdout=GET_WINEVENT_JSON_LIST)
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": channel})

    assert runner.calls == []  # NEVER executed
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "invalid query spec" in records[0]["message"]


@pytest.mark.parametrize(
    "provider",
    ["-Evil", "Foo; bar", "Foo$(x)", "Foo|bar", "Foo'quote"],
)
def test_malicious_provider_rejected_runner_not_called(provider: str) -> None:
    runner = RecordingRunner(stdout=GET_WINEVENT_JSON_LIST)
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System", "provider": provider})

    assert runner.calls == []
    assert records[0]["level_or_status"] == "error"


def test_malicious_channel_in_config_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        WindowsEventLogSource({"channel": "System; rm -rf /"}, runner=RecordingRunner())


@pytest.mark.parametrize("count", [0, -5, 99999, "abc", True])
def test_bad_count_rejected_runner_not_called(count: Any) -> None:
    runner = RecordingRunner(stdout="[]")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System", "count": count})

    assert runner.calls == []
    assert records[0]["level_or_status"] == "error"


@pytest.mark.parametrize("level", ["nope", 9, "-2", True, 0])
def test_bad_level_rejected_runner_not_called(level: Any) -> None:
    runner = RecordingRunner(stdout="[]")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System", "level": level})

    assert runner.calls == []
    assert records[0]["level_or_status"] == "error"


def test_bad_since_rejected_runner_not_called() -> None:
    runner = RecordingRunner(stdout="[]")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System", "since": "not-a-date; evil"})

    assert runner.calls == []
    assert records[0]["level_or_status"] == "error"


def test_valid_since_passes_into_filter() -> None:
    runner = RecordingRunner(stdout="[]")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)
    src.query({"channel": "System", "since": "2026-06-12T00:00:00"})

    command = runner.calls[0][runner.calls[0].index("-Command") + 1]
    assert "StartTime='2026-06-12T00:00:00'" in command


# --------------------------------------------------------------------------- #
# Error paths in query()
# --------------------------------------------------------------------------- #


def test_query_nonzero_rc_yields_error_record() -> None:
    runner = RecordingRunner(rc=1, stdout="", stderr="No events found")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "No events found" in records[0]["message"]


def test_query_bad_json_yields_error_record() -> None:
    runner = RecordingRunner(stdout="<<not json>>")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)

    records = src.query({"channel": "System"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "parse" in records[0]["message"]


def test_query_runner_exception_becomes_error_record() -> None:
    def boom(argv: Sequence[str]) -> tuple[int, str, str]:
        raise RuntimeError("subprocess blew up")

    src = WindowsEventLogSource({"backend": "powershell"}, runner=boom)
    records = src.query({"channel": "System"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "runner error" in records[0]["message"]


def test_query_empty_output_yields_no_records() -> None:
    runner = RecordingRunner(stdout="")
    src = WindowsEventLogSource({"backend": "powershell"}, runner=runner)
    assert src.query({"channel": "System"}) == []


# --------------------------------------------------------------------------- #
# health()
# --------------------------------------------------------------------------- #


def test_health_ok_powershell() -> None:
    runner = RecordingRunner(rc=0, stdout="LogName : System")
    src = WindowsEventLogSource({"backend": "powershell", "channel": "System"}, runner=runner)

    h = src.health()
    assert h["ok"] is True
    assert h["channel"] == "System"
    argv = runner.calls[0]
    assert argv[0] == "powershell"
    command = argv[argv.index("-Command") + 1]
    assert "Get-WinEvent -ListLog 'System'" in command


def test_health_ok_wevtutil() -> None:
    runner = RecordingRunner(rc=0, stdout="name: System")
    src = WindowsEventLogSource({"backend": "wevtutil", "channel": "System"}, runner=runner)

    h = src.health()
    assert h["ok"] is True
    assert runner.calls[0] == ["wevtutil", "gli", "System"]


def test_health_not_ok_on_nonzero_rc() -> None:
    runner = RecordingRunner(rc=5, stderr="channel not found")
    src = WindowsEventLogSource({"backend": "wevtutil", "channel": "Application"}, runner=runner)

    h = src.health()
    assert h["ok"] is False
    assert "channel not found" in h["detail"]


def test_health_never_raises_on_runner_exception() -> None:
    def boom(argv: Sequence[str]) -> tuple[int, str, str]:
        raise OSError("exec failed")

    src = WindowsEventLogSource({"backend": "powershell"}, runner=boom)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"
