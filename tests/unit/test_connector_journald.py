"""Unit tests for the JournaldSource connector (injected runner, no real journalctl)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.journald import JournaldSource


class RecordingRunner:
    """An injectable runner that records every argv and returns canned output."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        # Capture a copy so later mutation can't corrupt the assertion.
        self.calls.append(list(argv))
        return self.rc, self.stdout, self.stderr


def _journal_line(**overrides: object) -> str:
    entry = {
        "__REALTIME_TIMESTAMP": "1700000000000000",  # 1.7e15 us -> 1.7e9 s
        "PRIORITY": "3",
        "MESSAGE": "disk failure imminent",
        "_SYSTEMD_UNIT": "nginx.service",
        "_HOSTNAME": "web-01",
        "SYSLOG_IDENTIFIER": "nginx",
        "_PID": "4242",
    }
    entry.update(overrides)
    return json.dumps(entry)


def test_kind_and_name() -> None:
    src = JournaldSource({"name": "journal-prod"})
    assert JournaldSource.KIND == "logs"
    assert src.KIND == "logs"
    assert src.name == "journal-prod"


def test_name_defaults() -> None:
    assert JournaldSource().name == "journald"


def test_query_normalizes_record() -> None:
    runner = RecordingRunner(stdout=_journal_line() + "\n")
    src = JournaldSource({"name": "j1"}, runner=runner)

    records = src.query({"lines": 5})

    assert len(records) == 1
    rec = records[0]
    # ts: microseconds -> seconds
    assert rec["ts"] == pytest.approx(1_700_000_000.0)
    assert rec["source"] == "j1"
    assert rec["kind"] == "logs"
    # PRIORITY 3 -> "err"
    assert rec["level_or_status"] == "err"
    assert rec["message"] == "disk failure imminent"
    assert rec["value"] is None
    assert rec["labels"] == {
        "_SYSTEMD_UNIT": "nginx.service",
        "_HOSTNAME": "web-01",
        "SYSLOG_IDENTIFIER": "nginx",
        "_PID": "4242",
    }
    assert rec["raw"]["MESSAGE"] == "disk failure imminent"


def test_priority_mapping_all_levels() -> None:
    expected = {
        0: "emerg",
        1: "alert",
        2: "crit",
        3: "err",
        4: "warning",
        5: "notice",
        6: "info",
        7: "debug",
    }
    for prio, name in expected.items():
        runner = RecordingRunner(stdout=_journal_line(PRIORITY=str(prio)) + "\n")
        src = JournaldSource(runner=runner)
        rec = src.query({})[0]
        assert rec["level_or_status"] == name


def test_query_skips_blank_and_malformed_lines() -> None:
    out = "\n".join(["", _journal_line(), "not-json", "  ", _journal_line()])
    runner = RecordingRunner(stdout=out)
    src = JournaldSource(runner=runner)
    assert len(src.query({})) == 2


def test_query_nonzero_rc_returns_empty() -> None:
    runner = RecordingRunner(rc=1, stderr="boom")
    src = JournaldSource(runner=runner)
    assert src.query({}) == []


def test_argv_is_list_and_well_formed() -> None:
    runner = RecordingRunner(stdout="")
    src = JournaldSource(runner=runner)
    src.query({"lines": 20, "unit": "nginx.service", "since": "yesterday", "priority": 4})

    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == "journalctl"
    assert "-o" in argv and "json" in argv
    assert "--no-pager" in argv
    assert argv[argv.index("-n") + 1] == "20"
    assert argv[argv.index("-u") + 1] == "nginx.service"
    assert argv[argv.index("--since") + 1] == "yesterday"
    assert argv[argv.index("-p") + 1] == "4"


def test_default_lines_when_unset_or_invalid() -> None:
    runner = RecordingRunner(stdout="")
    src = JournaldSource(runner=runner)
    src.query({"lines": "not-a-number"})
    argv = runner.calls[0]
    assert argv[argv.index("-n") + 1] == "100"


@pytest.mark.parametrize(
    "bad_unit",
    ["-u evil", "--rotate", "nginx;rm -rf /", "a$(whoami)", "foo|bar", "a b", "x&y"],
)
def test_unsafe_unit_rejected_runner_not_called(bad_unit: str) -> None:
    runner = RecordingRunner(stdout="")
    src = JournaldSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"unit": bad_unit})
    assert runner.calls == []  # runner must never be invoked on rejection


@pytest.mark.parametrize("bad_since", ["-1h", "--vacuum", "a;b", "x`y`"])
def test_unsafe_since_rejected(bad_since: str) -> None:
    runner = RecordingRunner(stdout="")
    src = JournaldSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"since": bad_since})
    assert runner.calls == []


def test_priority_out_of_range_rejected() -> None:
    runner = RecordingRunner(stdout="")
    src = JournaldSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"priority": 9})
    assert runner.calls == []


def test_health_ok() -> None:
    runner = RecordingRunner(rc=0, stdout="systemd 255 (255.4)\n+PAM +AUDIT")
    src = JournaldSource(runner=runner)
    h = src.health()
    assert h["ok"] is True
    assert "systemd 255" in h["detail"]
    assert runner.calls[0] == ["journalctl", "--version"]


def test_health_not_ok_on_nonzero_rc() -> None:
    runner = RecordingRunner(rc=127, stderr="journalctl: command not found")
    src = JournaldSource(runner=runner)
    h = src.health()
    assert h["ok"] is False
    assert "not found" in h["detail"]


def test_health_never_raises_on_runner_exception() -> None:
    def boom(argv: list[str]) -> tuple[int, str, str]:
        raise OSError("no such binary")

    src = JournaldSource(runner=boom)
    h = src.health()
    assert h["ok"] is False
    assert "unavailable" in h["detail"]


def test_default_runner_uses_list_argv_never_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default subprocess runner must pass a list argv and shell=False."""
    import general_ludd.connectors.journald as mod

    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv: Any, **kwargs: Any) -> Any:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    src = JournaldSource()  # no runner -> default
    src.query({"lines": 3})

    assert isinstance(captured["argv"], list)
    kwargs = captured["kwargs"]
    assert kwargs.get("shell", False) is False  # shell never True
    assert kwargs.get("timeout")  # time-bound


def test_reader_adapter_returns_normalized_records() -> None:
    def reader(**_: object) -> list[dict[str, object]]:
        return [{"MESSAGE": "reader event", "__REALTIME_TIMESTAMP": "1700000000000000"}]

    src = JournaldSource(reader=reader)
    assert src.health()["ok"] is True
    assert src.query({})[0]["message"] == "reader event"
