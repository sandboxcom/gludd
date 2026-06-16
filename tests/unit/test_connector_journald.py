"""Unit tests for the journald log connector (mocked runner).

Covers: canned ``journalctl -o json`` normalization, injection-y filter
rejection (runner never called), and ``health()`` ok/not-ok/never-raises.
"""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.journald import KIND, JournaldSource


class RecordingRunner:
    """A fake runner that returns canned text and records the argv it got."""

    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return self.output


def _journal_lines(*entries: dict) -> str:
    return "\n".join(json.dumps(e) for e in entries)


CANNED = _journal_lines(
    {
        "__REALTIME_TIMESTAMP": "1700000000000000",
        "PRIORITY": "3",
        "MESSAGE": "disk failure on /dev/sda",
        "_SYSTEMD_UNIT": "smartd.service",
        "SYSLOG_IDENTIFIER": "smartd",
        "_HOSTNAME": "host01",
        "_PID": "421",
    },
    {
        "__REALTIME_TIMESTAMP": "1700000001000000",
        "PRIORITY": "6",
        "MESSAGE": "started nginx",
        "_SYSTEMD_UNIT": "nginx.service",
        "SYSLOG_IDENTIFIER": "systemd",
        "_HOSTNAME": "host01",
        "_PID": "1",
    },
)


class TestJournaldNormalization:
    def test_kind_is_logs(self) -> None:
        assert KIND == "logs"
        assert JournaldSource().KIND == "logs"

    def test_canned_json_normalized(self) -> None:
        src = JournaldSource({"name": "j1"}, runner=RecordingRunner(CANNED))
        recs = src.query({})
        assert len(recs) == 2

        first = recs[0]
        # All 8 normalized keys present.
        assert set(first) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }
        assert first["source"] == "j1"
        assert first["kind"] == "logs"
        assert first["ts"] == pytest.approx(1700000000.0)
        assert first["level_or_status"] == "error"  # PRIORITY 3
        assert first["message"] == "disk failure on /dev/sda"
        assert first["labels"] == {
            "unit": "smartd.service",
            "syslog_identifier": "smartd",
            "hostname": "host01",
            "pid": "421",
        }
        assert recs[1]["level_or_status"] == "info"  # PRIORITY 6

    def test_blank_and_malformed_lines_skipped(self) -> None:
        out = CANNED + "\n\n{ not json }\n"
        src = JournaldSource(runner=RecordingRunner(out))
        recs = src.query({})
        assert len(recs) == 2  # the two good lines only

    def test_binary_message_decoded(self) -> None:
        out = _journal_lines({"MESSAGE": list(b"hi"), "PRIORITY": "6"})
        recs = JournaldSource(runner=RecordingRunner(out)).query({})
        assert recs[0]["message"] == "hi"

    def test_missing_timestamp_is_none(self) -> None:
        out = _journal_lines({"MESSAGE": "no ts", "PRIORITY": "6"})
        recs = JournaldSource(runner=RecordingRunner(out)).query({})
        assert recs[0]["ts"] is None


class TestJournaldArgv:
    def test_valid_filters_become_argv(self) -> None:
        runner = RecordingRunner(CANNED)
        src = JournaldSource(runner=runner)
        src.query({"unit": "nginx.service", "priority": "3", "since": "2024-01-01 10:00:00", "lines": 50})
        argv = runner.calls[0]
        assert argv[0] == "journalctl"
        assert "-o" in argv and "json" in argv and "--no-pager" in argv
        assert "--unit=nginx.service" in argv
        assert "--priority=3" in argv
        assert "--since=2024-01-01 10:00:00" in argv
        assert argv[-2:] == ["-n", "50"]
        # argv is a list of plain tokens (no shell string interpolation).
        assert all(isinstance(tok, str) for tok in argv)


class TestJournaldInjectionRejected:
    @pytest.mark.parametrize(
        "bad_filter",
        [
            {"unit": "nginx; rm -rf /"},
            {"unit": "$(reboot)"},
            {"unit": "--output=cat"},  # leading-dash flag injection
            {"priority": "3 || curl evil"},
            {"priority": "99"},  # out of 0..7 range
            {"since": "`whoami`"},
            {"since": "-1h; echo pwned"},
        ],
    )
    def test_injection_rejected_runner_not_called(self, bad_filter: dict) -> None:
        runner = RecordingRunner(CANNED)
        src = JournaldSource(runner=runner)
        with pytest.raises(ValueError):
            src.query(bad_filter)
        # The runner must NOT have been invoked for a rejected filter.
        assert runner.calls == []


class TestJournaldHealth:
    def test_health_ok(self) -> None:
        h = JournaldSource(runner=RecordingRunner(CANNED)).health()
        assert h["ok"] is True
        assert "reachable" in h["detail"]

    def test_health_not_ok_on_runner_error(self) -> None:
        def boom(argv: list[str]) -> str:
            raise OSError("journalctl: command not found")

        h = JournaldSource(runner=boom).health()
        assert h["ok"] is False
        assert "unavailable" in h["detail"]

    def test_health_never_raises(self) -> None:
        def boom(argv: list[str]) -> str:
            raise RuntimeError("kaboom")

        # Must return a dict, not propagate.
        h = JournaldSource(runner=boom).health()
        assert isinstance(h, dict)
        assert h["ok"] is False
