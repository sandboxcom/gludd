from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def test_trace_event_writes_jsonl_with_resource_fields(tmp_path, monkeypatch) -> None:
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


def test_controller_sessionstart_truncates_trace_once(tmp_path, monkeypatch) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text("stale" + chr(10), encoding="utf-8")
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(log_path))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")

    session = SimpleNamespace(config=SimpleNamespace(workerinput=None))
    trace.pytest_sessionstart(session)

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["RUN_START"]


def test_summary_identifies_unfinished_nodeids(tmp_path) -> None:
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


def test_summary_reports_worker_memory_growth_and_largest_jumps(tmp_path) -> None:
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


def test_runner_exports_repo_root_for_plugin_import(monkeypatch) -> None:
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


def test_trace_path_is_stable_after_session_start(tmp_path, monkeypatch) -> None:
    from scripts import xdist_trace_plugin as trace

    first_log = tmp_path / "first.jsonl"
    second_log = tmp_path / "second.jsonl"
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(first_log))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")
    trace._ACTIVE_TRACE_PATH = None

    try:
        session = SimpleNamespace(config=SimpleNamespace(workerinput=None))
        trace.pytest_sessionstart(session)
        monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(second_log))
        trace.write_event("START", nodeid="tests/demo.py::test_demo")
    finally:
        trace._ACTIVE_TRACE_PATH = None

    assert "tests/demo.py::test_demo" in first_log.read_text(encoding="utf-8")
    assert not second_log.exists()
