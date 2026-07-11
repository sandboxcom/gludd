"""Unit tests for the dmesg host log connector (injected runner, no real binary)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from general_ludd.connectors.dmesg import DmesgSource


@dataclass
class FakeResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeRunner:
    result: FakeResult = field(default_factory=FakeResult)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Any) -> FakeResult:
        self.calls.append(list(argv))
        return self.result


DMESG_JSON = json.dumps(
    {
        "dmesg": [
            {
                "priority": 6,
                "timestamp": {"usec": 123456},
                "facility": "kern",
                "subsystem": "usb",
                "msg": "USB disconnect, device number 5",
            },
            {
                "level": 3,
                "timestamp": {"usec": 789012},
                "facility": "kern",
                "caller": "ata1",
                "msg": "ata1: SError: { RecovComm }",
            },
        ]
    }
)


def test_kind_and_name() -> None:
    src = DmesgSource(config={"name": "host-dmesg"}, runner=FakeRunner())
    assert src.KIND == "logs"
    assert src.name == "host-dmesg"


def test_query_normalizes_entries() -> None:
    runner = FakeRunner(FakeResult(stdout=DMESG_JSON))
    src = DmesgSource(runner=runner)
    records = src.query({})

    assert runner.calls == [["dmesg", "--json"]]
    assert len(records) == 2

    first = records[0]
    assert first["kind"] == "logs"
    assert first["source"] == "dmesg"
    assert first["level_or_status"] == "info"  # priority 6 -> info
    assert first["ts"] == 123456
    assert first["message"] == "USB disconnect, device number 5"
    assert first["labels"]["facility"] == "kern"
    assert first["labels"]["subsystem"] == "usb"
    assert first["value"] is None

    second = records[1]
    assert second["level_or_status"] == "err"  # level 3 -> err
    assert second["labels"]["subsystem"] == "ata1"


def test_query_builds_validated_filter_argv() -> None:
    runner = FakeRunner(FakeResult(stdout='{"dmesg": []}'))
    src = DmesgSource(runner=runner)
    src.query({"facility": "kern", "level": "err"})
    assert runner.calls == [["dmesg", "--json", "--facility=kern", "--level=err"]]


@pytest.mark.parametrize(
    "bad",
    [
        "-f",
        "--force",
        "kern; rm -rf /",
        "kern|nc",
        "kern && id",
        "$(whoami)",
        "`id`",
        "kern subsystem",  # whitespace
        "",
    ],
)
def test_injection_like_filter_rejected(bad: str) -> None:
    runner = FakeRunner(FakeResult(stdout='{"dmesg": []}'))
    src = DmesgSource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"facility": bad})
    assert runner.calls == []


def test_no_shell_single_argv_elements() -> None:
    runner = FakeRunner(FakeResult(stdout='{"dmesg": []}'))
    src = DmesgSource(runner=runner)
    src.query({})
    # argv is a list; binary and flags are discrete elements (no shell string)
    assert runner.calls[0] == ["dmesg", "--json"]


def test_query_accepts_top_level_list() -> None:
    payload = json.dumps([{"priority": 4, "msg": "low memory", "facility": "kern"}])
    runner = FakeRunner(FakeResult(stdout=payload))
    src = DmesgSource(runner=runner)
    records = src.query({})
    assert records[0]["level_or_status"] == "warning"
    assert records[0]["message"] == "low memory"


def test_health_ok() -> None:
    runner = FakeRunner(FakeResult(returncode=0, stdout='{"dmesg": []}'))
    src = DmesgSource(runner=runner)
    health = src.health()
    assert health["ok"] is True
    assert runner.calls[0] == ["dmesg", "--json"]


def test_health_not_ok_on_nonzero_exit() -> None:
    runner = FakeRunner(FakeResult(returncode=1, stderr="Operation not permitted"))
    src = DmesgSource(runner=runner)
    health = src.health()
    assert health["ok"] is False
    assert health["detail"] == "Operation not permitted"


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, argv: Any) -> FakeResult:
            raise OSError("no kmsg")

    src = DmesgSource(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "no kmsg" in health["detail"]


def test_query_raises_on_nonzero_exit() -> None:
    runner = FakeRunner(FakeResult(returncode=1, stderr="permission denied"))
    src = DmesgSource(runner=runner)
    with pytest.raises(RuntimeError, match="permission denied"):
        src.query({})


def test_query_raises_on_non_json() -> None:
    runner = FakeRunner(FakeResult(stdout="[ 0.000000] booting"))
    src = DmesgSource(runner=runner)
    with pytest.raises(RuntimeError, match="non-JSON"):
        src.query({})
