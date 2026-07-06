"""Unit tests for the AuditdSource connector (injected runner, no real ausearch)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.auditd import AuditdSource


class RecordingRunner:
    """An injectable runner that records every argv and returns canned output.

    `responses` maps the first argv token (e.g. 'auditctl') to (rc, out, err);
    a default response covers anything else.
    """

    def __init__(
        self,
        rc: int = 0,
        stdout: str = "",
        stderr: str = "",
        responses: dict[str, tuple[int, str, str]] | None = None,
    ) -> None:
        self.default = (rc, stdout, stderr)
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return self.responses.get(argv[0], self.default)


# A canned ausearch --format csv body: a header row + two event rows.
_CSV = (
    "node,event_kind,event_type,event_time,serial,uid,exe,success,summary\n"
    "web-01,audit,SYSCALL,1700000000,4711,0,/usr/bin/sudo,yes,root ran sudo\n"
    "web-01,audit,USER_LOGIN,1700000123,4712,1000,/usr/sbin/sshd,no,failed login for bob\n"
)


def test_kind_and_name() -> None:
    src = AuditdSource({"name": "audit-prod"})
    assert AuditdSource.KIND == "logs"
    assert src.KIND == "logs"
    assert src.name == "audit-prod"


def test_name_defaults() -> None:
    assert AuditdSource().name == "auditd"


def test_query_normalizes_records() -> None:
    runner = RecordingRunner(stdout=_CSV)
    src = AuditdSource({"name": "a1"}, runner=runner)

    records = src.query({})

    assert len(records) == 2
    first = records[0]
    assert first["ts"] == pytest.approx(1_700_000_000.0)
    assert first["source"] == "a1"
    assert first["kind"] == "logs"
    assert first["level_or_status"] == "SYSCALL"
    assert first["message"] == "root ran sudo"
    assert first["value"] is None
    assert first["labels"] == {
        "type": "SYSCALL",
        "uid": "0",
        "exe": "/usr/bin/sudo",
        "success": "yes",
    }
    assert first["raw"]["serial"] == "4711"

    second = records[1]
    assert second["level_or_status"] == "USER_LOGIN"
    assert second["labels"]["success"] == "no"
    assert second["message"] == "failed login for bob"


def test_header_and_blank_rows_skipped() -> None:
    runner = RecordingRunner(stdout=_CSV + "\n\n")
    src = AuditdSource(runner=runner)
    assert len(src.query({})) == 2  # header + trailing blanks excluded


def test_query_nonzero_rc_returns_empty() -> None:
    runner = RecordingRunner(rc=2, stderr="ausearch: error")
    src = AuditdSource(runner=runner)
    assert src.query({}) == []


def test_argv_is_list_and_well_formed() -> None:
    runner = RecordingRunner(stdout="")
    src = AuditdSource(runner=runner)
    src.query({"start": "today", "type": "USER_LOGIN"})

    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == "ausearch"
    assert "--format" in argv and "csv" in argv
    assert argv[argv.index("--start") + 1] == "today"
    assert argv[argv.index("-m") + 1] == "USER_LOGIN"


@pytest.mark.parametrize(
    "bad_start",
    ["-1h", "--checkpoint", "a;b", "x$(id)", "a|b", "a b", "x&y", "`cmd`"],
)
def test_unsafe_start_rejected_runner_not_called(bad_start: str) -> None:
    runner = RecordingRunner(stdout="")
    src = AuditdSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"start": bad_start})
    assert runner.calls == []


@pytest.mark.parametrize("bad_type", ["-m", "--input", "A;B", "x*y"])
def test_unsafe_type_rejected(bad_type: str) -> None:
    runner = RecordingRunner(stdout="")
    src = AuditdSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"type": bad_type})
    assert runner.calls == []


def test_health_ok_via_auditctl() -> None:
    runner = RecordingRunner(
        responses={"auditctl": (0, "enabled 1\npid 1234\n", "")},
    )
    src = AuditdSource(runner=runner)
    h = src.health()
    assert h["ok"] is True
    assert "enabled 1" in h["detail"]
    assert runner.calls[0] == ["auditctl", "-s"]


def test_health_falls_back_to_ausearch_version() -> None:
    runner = RecordingRunner(
        responses={
            "auditctl": (1, "", "permission denied"),
            "ausearch": (0, "ausearch version 3.0\n", ""),
        },
    )
    src = AuditdSource(runner=runner)
    h = src.health()
    assert h["ok"] is True
    assert "ausearch version 3.0" in h["detail"]
    # both probes attempted, in order
    assert runner.calls[0] == ["auditctl", "-s"]
    assert runner.calls[1] == ["ausearch", "--version"]


def test_health_not_ok_when_both_fail() -> None:
    runner = RecordingRunner(
        responses={
            "auditctl": (1, "", "no perms"),
            "ausearch": (127, "", "not found"),
        },
    )
    src = AuditdSource(runner=runner)
    h = src.health()
    assert h["ok"] is False
    assert "unavailable" in h["detail"]


def test_health_never_raises_on_runner_exception() -> None:
    def boom(argv: list[str]) -> tuple[int, str, str]:
        raise OSError("no such binary")

    src = AuditdSource(runner=boom)
    h = src.health()
    assert h["ok"] is False


def test_default_runner_uses_list_argv_never_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default subprocess runner must pass a list argv and shell=False."""
    import general_ludd.connectors.auditd as mod

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
    src = AuditdSource()  # no runner -> default
    src.query({})

    assert isinstance(captured["argv"], list)
    kwargs = captured["kwargs"]
    assert kwargs.get("shell", False) is False
    assert kwargs.get("timeout")
