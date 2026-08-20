from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


def test_trace_event_writes_jsonl_with_resource_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(log_path))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")

    trace.write_event("START", nodeid="tests/demo.py::test_demo")

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event"] == "START"
    assert payload["nodeid"] == "tests/demo.py::test_demo"
    assert payload["worker"] == "gw7"
    assert payload["pid"] > 0
    assert "timestamp" in payload
    assert "loadavg" in payload
    assert "disk_free_bytes" in payload
    assert "rss_kb" in payload


def test_controller_sessionstart_truncates_trace_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text("stale" + chr(10), encoding="utf-8")
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(log_path))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")

    session = SimpleNamespace(config=SimpleNamespace(workerinput=None))
    trace.pytest_sessionstart(cast(pytest.Session, session))

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["RUN_START"]


def test_summary_identifies_unfinished_nodeids(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import summarize_log

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        chr(10).join(
            [
                json.dumps({"event": "START", "worker": "gw0", "nodeid": "tests/a.py::test_a"}),
                json.dumps({"event": "FINISH", "worker": "gw0", "nodeid": "tests/a.py::test_a"}),
                json.dumps({"event": "START", "worker": "gw1", "nodeid": "tests/b.py::test_b"}),
            ]
        )
        + chr(10),
        encoding="utf-8",
    )

    summary = summarize_log(log_path)

    assert summary["started"] == 2
    assert summary["finished"] == 1
    assert summary["unfinished"] == [{"worker": "gw1", "nodeid": "tests/b.py::test_b"}]


def test_compact_summary_keeps_every_unique_failure_without_tracebacks(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import compact_summary, summarize_log

    log_path = tmp_path / "trace.jsonl"
    events: list[str] = []
    for index in range(60):
        nodeid = f"tests/failures/test_{index}.py::test_failure"
        for worker in ("gw0", "controller"):
            events.append(
                json.dumps(
                    {
                        "event": "REPORT",
                        "worker": worker,
                        "nodeid": nodeid,
                        "outcome": "failed",
                        "when": "call",
                        "longrepr": "x" * 4000,
                    }
                )
            )
    log_path.write_text(chr(10).join(events) + chr(10), encoding="utf-8")

    summary = summarize_log(log_path)
    compact = compact_summary(summary)

    assert summary["failure_report_count"] == 120
    assert summary["failure_nodeid_count"] == 60
    assert compact["failure_nodeids"] == [
        f"tests/failures/test_{index}.py::test_failure" for index in range(60)
    ]
    assert "failures" not in compact
    assert "longrepr" not in json.dumps(compact)


def test_summary_tolerates_missing_blank_malformed_and_non_mapping_events(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import summarize_log

    log_path = tmp_path / "trace.jsonl"
    assert summarize_log(log_path)["events"] == 0

    log_path.write_text(chr(10) + "not-json" + chr(10) + "[]" + chr(10) + "{}" + chr(10), encoding="utf-8")
    summary = summarize_log(log_path)

    assert summary["events"] == 2
    assert summary["failure_nodeids"] == []


def test_summary_cli_defaults_compact_and_retains_verbose_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.summarize_xdist_trace import main

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "event": "REPORT",
                "worker": "gw0",
                "nodeid": "tests/demo.py::test_failure",
                "outcome": "failed",
                "when": "call",
                "longrepr": "secret traceback detail",
            }
        )
        + chr(10),
        encoding="utf-8",
    )

    assert main([str(log_path)]) == 0
    compact = json.loads(capsys.readouterr().out)
    assert compact["failure_nodeids"] == ["tests/demo.py::test_failure"]
    assert "failures" not in compact

    assert main(["--verbose", str(log_path)]) == 0
    verbose = json.loads(capsys.readouterr().out)
    assert verbose["failures"][0]["longrepr"] == "secret traceback detail"


def test_summary_reports_worker_memory_growth_and_largest_jumps(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import summarize_log

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        chr(10).join(
            [
                json.dumps(
                    {
                        "event": "START",
                        "worker": "gw0",
                        "nodeid": "tests/a.py::test_a",
                        "rss_kb": 100,
                    }
                ),
                json.dumps(
                    {
                        "event": "FINISH",
                        "worker": "gw0",
                        "nodeid": "tests/a.py::test_a",
                        "rss_kb": 140,
                    }
                ),
                json.dumps(
                    {
                        "event": "START",
                        "worker": "gw0",
                        "nodeid": "tests/b.py::test_b",
                        "rss_kb": 220,
                    }
                ),
                json.dumps(
                    {
                        "event": "START",
                        "worker": "gw1",
                        "nodeid": "tests/c.py::test_c",
                        "rss_kb": 75,
                    }
                ),
            ]
        )
        + chr(10),
        encoding="utf-8",
    )

    summary = summarize_log(log_path)

    assert summary["memory_by_worker"]["gw0"] == {
        "first_rss_kb": 100,
        "peak_rss_kb": 220,
        "growth_rss_kb": 120,
        "peak_nodeid": "tests/b.py::test_b",
    }
    assert summary["largest_rss_increases"][0] == {
        "worker": "gw0",
        "nodeid": "tests/b.py::test_b",
        "event": "START",
        "rss_kb": 220,
        "increase_rss_kb": 80,
    }


def test_make_targets_run_traced_full_suite() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert chr(10) + "test-xdist-trace:" in makefile
    assert "scripts/run_xdist_trace.py" in makefile
    assert "--max-worker-restart=0" in makefile
    assert "-p scripts.xdist_trace_plugin" in makefile
    assert chr(10) + "test-xdist-trace-summary:" in makefile


def test_runner_exports_repo_root_for_plugin_import(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys

    from scripts import run_xdist_trace

    repo_root = Path.cwd()
    monkeypatch.delenv("PYTHONPATH", raising=False)
    while str(repo_root) in sys.path:
        sys.path.remove(str(repo_root))

    run_xdist_trace.prepare_environment("/tmp/gludd-xdist-test.jsonl")

    assert sys.path[0] == str(repo_root)
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(repo_root)
    assert os.environ["GLUDD_XDIST_TRACE_LOG"] == "/tmp/gludd-xdist-test.jsonl"


def test_trace_path_is_stable_after_session_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    first_log = tmp_path / "first.jsonl"
    second_log = tmp_path / "second.jsonl"
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(first_log))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")
    trace._ACTIVE_TRACE_PATH = None

    try:
        session = SimpleNamespace(config=SimpleNamespace(workerinput=None))
        trace.pytest_sessionstart(cast(pytest.Session, session))
        monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(second_log))
        trace.write_event("START", nodeid="tests/demo.py::test_demo")
    finally:
        trace._ACTIVE_TRACE_PATH = None

    assert "tests/demo.py::test_demo" in first_log.read_text(encoding="utf-8")
    assert not second_log.exists()
