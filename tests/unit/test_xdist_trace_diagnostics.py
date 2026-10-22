from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile


@pytest.mark.parametrize(
    ("trace_role_context", "trace_role_expected"),
    [(None, "controller"), ({}, "controller"), ({"workerid": "gw2"}, "gw2")],
    ids=("unbound", "controller", "worker"),
)
def test_trace_worker_identity_uses_native_context_and_preserves_carriers(
    monkeypatch: pytest.MonkeyPatch,
    trace_role_context: dict[str, str] | None,
    trace_role_expected: str,
) -> None:
    import os

    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-inherited")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "73")
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "inherited-run")
    config = (
        None if trace_role_context is None
        else SimpleNamespace(workerinput=trace_role_context or None)
    )
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", config)
    assert trace._worker_id() == trace_role_expected
    assert os.environ["PYTEST_XDIST_WORKER"] == "gw-inherited"
    assert os.environ["PYTEST_XDIST_WORKER_COUNT"] == "73"
    assert os.environ["PYTEST_XDIST_TESTRUNUID"] == "inherited-run"


@pytest.mark.parametrize("trace_invalid_worker", ["", None, 7, True])
def test_trace_native_worker_refuses_invalid_identity_without_environment_fallback(
    monkeypatch: pytest.MonkeyPatch, trace_invalid_worker: object,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-inherited")
    config = SimpleNamespace(workerinput={"workerid": trace_invalid_worker})
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", config)
    with pytest.raises(ValueError, match="native xdist worker identity"):
        trace._worker_id()


def test_ordinary_trace_config_unconfigure_releases_only_matching_owner(
    native_profile_config: pytest.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", None)
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", tmp_path / "ordinary.jsonl")
    other = pytest.Config.fromdictargs(
        {"basetemp": str(tmp_path / "other-base")}, [str(tmp_path / "checkout")],
    )
    try:
        trace._pin_profile_trace(native_profile_config)
        assert trace._ACTIVE_TRACE_CONFIG is native_profile_config
        trace.pytest_unconfigure(other)
        assert trace._ACTIVE_TRACE_CONFIG is native_profile_config
        trace.pytest_unconfigure(native_profile_config)
        assert trace._ACTIVE_TRACE_CONFIG is None
        assert trace._worker_id() == "controller"
        trace.pytest_unconfigure(native_profile_config)
        assert trace._ACTIVE_TRACE_CONFIG is None
    finally:
        other._ensure_unconfigure()


def test_profile_cleanup_restores_previous_ordinary_config_until_its_own_teardown(
    native_profile_config: pytest.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", None)
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", tmp_path / "previous.jsonl")
    previous = pytest.Config.fromdictargs(
        {"basetemp": str(tmp_path / "previous-base")}, [str(tmp_path / "checkout")],
    )
    try:
        trace._pin_profile_trace(previous)
        monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
        trace._consume_profile_request(native_profile_config)
        state = trace._profile_state(native_profile_config)
        assert state is not None
        assert state.previous_config is previous
        trace._pin_profile_trace(native_profile_config)
        assert trace._ACTIVE_TRACE_CONFIG is native_profile_config
        assert trace._finish_profile(native_profile_config, 1)
        assert trace._ACTIVE_TRACE_CONFIG is previous
        trace.pytest_unconfigure(native_profile_config)
        assert trace._ACTIVE_TRACE_CONFIG is previous
        trace.pytest_unconfigure(previous)
        assert trace._ACTIVE_TRACE_CONFIG is None
    finally:
        previous._ensure_unconfigure()


def test_trace_event_writes_jsonl_with_resource_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", None)
    monkeypatch.setattr(trace, "_ACTIVE_RUN_ID", None)
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(log_path))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_RUN_ID", "observed-run-7")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    monkeypatch.setattr(
        trace, "_ACTIVE_TRACE_CONFIG",
        cast(pytest.Config, SimpleNamespace(workerinput={"workerid": "gw7"})),
    )

    trace.write_event("START", nodeid="tests/demo.py::test_demo")

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event"] == "START"
    assert payload["nodeid"] == "tests/demo.py::test_demo"
    assert payload["worker"] == "gw7"
    assert payload["run_id"] == "observed-run-7"
    assert payload["pid"] > 0
    assert "timestamp" in payload
    assert "loadavg" in payload
    assert "disk_free_bytes" in payload
    assert "rss_kb" in payload


def test_collection_finish_records_only_the_bounded_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", log_path)
    monkeypatch.setattr(trace, "_ACTIVE_RUN_ID", "collect-run")
    session = SimpleNamespace(items=[object(), object(), object()])

    trace.pytest_collection_finish(cast(pytest.Session, session))

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["event"] == "COLLECTION_FINISH"
    assert payload["run_id"] == "collect-run"
    assert payload["collected"] == 3
    assert "nodeids" not in payload


def test_trace_resource_probes_degrade_to_bounded_empty_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import resource
    import shutil

    from scripts import xdist_trace_plugin as trace

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("resource probe unavailable")

    monkeypatch.setattr(os, "getloadavg", unavailable)
    monkeypatch.setattr(resource, "getrusage", unavailable)
    monkeypatch.setattr(shutil, "disk_usage", unavailable)

    assert trace._loadavg() == []
    assert trace._rss_kb() == 0
    assert trace._disk_free_bytes() == 0


def test_trace_protocol_records_exception_and_ignores_passing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", log_path)
    monkeypatch.setattr(trace, "_ACTIVE_RUN_ID", "exception-run")
    item = SimpleNamespace(nodeid="tests/demo.py::test_error")
    protocol = trace.pytest_runtest_protocol(cast(pytest.Item, item), None)

    next(protocol)
    with pytest.raises(StopIteration):
        protocol.send(
            SimpleNamespace(excinfo=(ValueError, ValueError("broken protocol"), None))
        )
    trace.pytest_runtest_logreport(
        cast(pytest.TestReport, SimpleNamespace(failed=False))
    )

    events = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["START", "FINISH"]
    assert events[1]["outcome"] == "exception"
    assert events[1]["exc_type"] == "ValueError"


def test_controller_sessionstart_truncates_trace_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import xdist_trace_plugin as trace

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text("stale" + chr(10), encoding="utf-8")
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(log_path))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")

    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", None)
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


def test_summary_filters_an_append_only_trace_by_run_id(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import summarize_log

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        chr(10).join(
            [
                "not json from an unrelated historical run",
                json.dumps(
                    {
                        "event": "START",
                        "run_id": "old-run",
                        "worker": "gw0",
                        "nodeid": "tests/old.py::test_old",
                    }
                ),
                json.dumps(
                    {
                        "event": "START",
                        "run_id": "current-run",
                        "worker": "gw1",
                        "nodeid": "tests/current.py::test_current",
                    }
                ),
            ]
        )
        + chr(10),
        encoding="utf-8",
    )

    summary = summarize_log(log_path, run_id="current-run")

    assert summary["run_id"] == "current-run"
    assert summary["events"] == 1
    assert summary["started"] == 1
    assert summary["last_by_worker"] == {
        "gw1": "tests/current.py::test_current"
    }


def test_summary_cli_accepts_run_id_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.summarize_xdist_trace import main

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "event": "RUN_FINISH",
                "run_id": "selected-run",
                "worker": "controller",
            }
        )
        + chr(10),
        encoding="utf-8",
    )

    assert main(["--run-id", "selected-run", str(log_path)]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "selected-run"

    with pytest.raises(SystemExit):
        main(["--run-id", "../other-run", str(log_path)])


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


def test_compact_summary_omits_memory_arrays_unless_requested(tmp_path: Path) -> None:
    from scripts.summarize_xdist_trace import compact_summary, summarize_log

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "event": "START",
                "worker": "gw0",
                "nodeid": "tests/demo.py::test_demo",
                "rss_kb": 100,
            }
        )
        + chr(10),
        encoding="utf-8",
    )

    summary = summarize_log(log_path)

    compact = compact_summary(summary)
    assert "memory_by_worker" not in compact
    assert "largest_rss_increases" not in compact

    compact_with_memory = compact_summary(summary, include_memory=True)
    assert compact_with_memory["memory_by_worker"] == summary["memory_by_worker"]
    assert compact_with_memory["largest_rss_increases"] == summary["largest_rss_increases"]


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


def test_summary_cli_has_concise_failures_only_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.summarize_xdist_trace import main

    log_path = tmp_path / "trace.jsonl"
    log_path.write_text(
        chr(10).join(
            [
                json.dumps(
                    {
                        "event": "START",
                        "worker": "gw1",
                        "nodeid": "tests/demo.py::test_worker_exit",
                        "rss_kb": 100,
                    }
                ),
                json.dumps(
                    {
                        "event": "REPORT",
                        "worker": "controller",
                        "nodeid": "tests/demo.py::test_worker_exit",
                        "outcome": "failed",
                        "when": "call",
                        "longrepr": "worker gw1 crashed with a very long traceback",
                    }
                ),
            ]
        )
        + chr(10),
        encoding="utf-8",
    )

    assert main(["--failures-only", str(log_path)]) == 0
    failures_only = json.loads(capsys.readouterr().out)

    assert failures_only == {
        "failure_nodeid_count": 1,
        "failure_nodeids": ["tests/demo.py::test_worker_exit"],
        "failure_report_count": 1,
        "path": str(log_path),
        "unfinished": [
            {"nodeid": "tests/demo.py::test_worker_exit", "worker": "gw1"}
        ],
    }


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
        "first_rss_bytes": 100 * 1024,
        "first_rss_kib": 100,
        "peak_rss_bytes": 220 * 1024,
        "peak_rss_kib": 220,
        "growth_rss_bytes": 120 * 1024,
        "growth_rss_kib": 120,
        "peak_nodeid": "tests/b.py::test_b",
    }
    assert summary["largest_rss_increases"][0] == {
        "worker": "gw0",
        "nodeid": "tests/b.py::test_b",
        "event": "START",
        "rss_bytes": 220 * 1024,
        "rss_kib": 220,
        "increase_rss_bytes": 80 * 1024,
        "increase_rss_kib": 80,
    }
    assert summary["legacy_rss_input_unit"] == "kib"


def test_summary_normalizes_legacy_macos_rss_bytes_without_inflation(
    tmp_path: Path,
) -> None:
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
                        "rss_kb": 92_684_288,
                    }
                ),
                json.dumps(
                    {
                        "event": "FINISH",
                        "worker": "gw0",
                        "nodeid": "tests/a.py::test_a",
                        "rss_kb": 93_011_968,
                    }
                ),
            ]
        )
        + chr(10),
        encoding="utf-8",
    )

    summary = summarize_log(log_path)

    assert summary["legacy_rss_input_unit"] == "bytes"
    assert summary["memory_by_worker"]["gw0"] == {
        "first_rss_bytes": 92_684_288,
        "first_rss_kib": 90_512,
        "peak_rss_bytes": 93_011_968,
        "peak_rss_kib": 90_832,
        "growth_rss_bytes": 327_680,
        "growth_rss_kib": 320,
        "peak_nodeid": "tests/a.py::test_a",
    }


def test_make_targets_run_traced_full_suite() -> None:
    makefile = compose_makefile(Path("Makefile"))

    assert chr(10) + "test-xdist-trace:" in makefile
    assert "scripts/run_xdist_trace.py" in makefile
    assert "--max-worker-restart=0" in makefile
    assert "-p scripts.xdist_trace_plugin" in makefile
    assert "GLUDD_XDIST_TRACE_RUN_ID" in makefile
    assert chr(10) + "test-xdist-trace-summary:" in makefile
    summary_recipe = makefile.split(chr(10) + "test-xdist-trace-summary:" + chr(10), 1)[1].split(
        chr(10) + chr(10), 1
    )[0]
    assert '--run-id "$$GLUDD_TRACE_SUMMARY_RUN_ID"' in summary_recipe
    assert 'scripts/summarize_xdist_trace.py "$$@" -- ' in summary_recipe
    assert '"$${GLUDD_TRACE_SUMMARY_LOG:-/tmp/gludd-xdist-progress.log}"' in summary_recipe
    assert (
        "test-xdist-trace-summary: override GLUDD_TRACE_SUMMARY_RUN_ID = $(value RUN_ID)"
        in makefile
    )


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
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", None)
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


@pytest.fixture
def native_profile_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[pytest.Config]:
    """Use a real Config with an isolated checkout and native descriptor state."""
    from scripts import xdist_trace_plugin as trace

    root = tmp_path / "checkout"
    root.mkdir()
    base = root / "basetemp"
    base.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(trace, "_profile_root", lambda: root)
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_PATH", None)
    monkeypatch.setattr(trace, "_ACTIVE_RUN_ID", None)
    monkeypatch.setattr(trace, "_ACTIVE_TRACE_CONFIG", None)
    config = pytest.Config.fromdictargs({"basetemp": str(base)}, [str(root)])
    try:
        yield config
    finally:
        try:
            trace.pytest_unconfigure(config)
        finally:
            config._ensure_unconfigure()


def _begin_native_profile(config: pytest.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(config)
    assert "_TEST_FILES_CPROFILE" not in __import__("os").environ
    trace._start_profile(config)


def test_native_cprofile_off_does_not_import_or_create_profile(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    from scripts import xdist_trace_plugin as trace

    original = builtins.__import__

    def guarded_import(
        name: str, globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None, fromlist: Sequence[str] = (), level: int = 0,
    ) -> ModuleType:
        assert name not in {"cProfile", "pstats", "tempfile", "secrets"}
        return original(name, globals, locals, fromlist, level)

    monkeypatch.delenv("_TEST_FILES_CPROFILE", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    trace._consume_profile_request(native_profile_config)
    trace._start_profile(native_profile_config)
    assert trace._finish_profile(native_profile_config, 0)
    assert not (trace._profile_root() / ".gate-logs").exists()


@pytest.mark.parametrize("kind", ["directory-link", "dangling-link", "file", "world-writable"])
def test_native_cprofile_refuses_unsafe_artifact_route_without_writing_outside(
    native_profile_config: pytest.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    logs = trace._profile_root() / ".gate-logs"
    if kind == "directory-link":
        logs.symlink_to(outside, target_is_directory=True)
    elif kind == "dangling-link":
        logs.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif kind == "file":
        logs.write_text("unchanged", encoding="utf-8")
    else:
        logs.mkdir()
        logs.chmod(0o777)
    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    with pytest.raises(pytest.UsageError, match="unsafe artifact directory"):
        trace._consume_profile_request(native_profile_config)
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]
    assert trace._profile_state(native_profile_config) is None


@pytest.mark.parametrize("artifact", ["pytest.pstats", "report.txt"])
def test_native_cprofile_exclusive_artifacts_never_overwrite(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    _begin_native_profile(native_profile_config, monkeypatch)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    target = state.directory / artifact
    target.write_bytes(b"original immutable artifact")
    assert not trace._finish_profile(native_profile_config, 0)
    assert target.read_bytes() == b"original immutable artifact"
    assert state.root_fd == state.directory_fd == -1
    assert trace._finish_profile(native_profile_config, 0)


@pytest.mark.parametrize("limit", ["_PROFILE_RAW_LIMIT", "_PROFILE_REPORT_LIMIT"])
def test_native_cprofile_rejects_oversize_without_publishing(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch, limit: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    _begin_native_profile(native_profile_config, monkeypatch)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    monkeypatch.setattr(trace, limit, 1)
    assert not trace._finish_profile(native_profile_config, 0)
    assert not (state.directory / "pytest.pstats").exists()
    assert not (state.directory / "report.txt").exists()
    assert state.root_fd == state.directory_fd == -1


def test_native_cprofile_refuses_changed_leaf_and_keeps_outside_data(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    _begin_native_profile(native_profile_config, monkeypatch)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    moved = state.directory.with_name(state.directory.name + "-moved")
    state.directory.rename(moved)
    state.directory.mkdir()
    sentinel = state.directory / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert not trace._finish_profile(native_profile_config, 0)
    assert sorted(path.name for path in state.directory.iterdir()) == ["sentinel"]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (moved / "pytest.pstats").exists()


def test_native_cprofile_preserves_active_profiler_and_refuses_foreign_pid(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    state = trace._profile_state(native_profile_config)
    assert state is not None

    def already_active(_frame: object, _event: str, _arg: object) -> None:
        return None

    old = sys.getprofile()
    sys.setprofile(already_active)
    try:
        with pytest.raises(pytest.UsageError, match="active or foreign profiler"):
            trace._start_profile(native_profile_config)
        assert sys.getprofile() is already_active
    finally:
        sys.setprofile(old)
    state.pid += 1
    with pytest.raises(pytest.UsageError, match="active or foreign profiler"):
        trace._start_profile(native_profile_config)
    assert state.profiler is None
    assert not (state.directory / "pytest.pstats").exists()


def test_native_cprofile_unconfigure_finalizes_once_and_closes_descriptors(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", "previous-trace")
    _begin_native_profile(native_profile_config, monkeypatch)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    descriptors = (state.root_fd, state.directory_fd)
    trace.pytest_unconfigure(native_profile_config)
    original = (state.directory / "pytest.pstats").read_bytes()
    trace.pytest_unconfigure(native_profile_config)
    assert (state.directory / "pytest.pstats").read_bytes() == original
    assert "pytest_exitstatus=unknown" in (state.directory / "report.txt").read_text(encoding="utf-8")
    assert os.environ["GLUDD_XDIST_TRACE_LOG"] == "previous-trace"
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("exitstatus", [0, 1])
def test_native_cprofile_publication_error_never_turns_failure_green(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch, exitstatus: int,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    session = pytest.Session.from_config(native_profile_config)
    trace.pytest_sessionstart(session)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("native profile publication unavailable")

    monkeypatch.setattr(trace, "_publish_profile", unavailable)
    session.exitstatus = exitstatus
    trace.pytest_sessionfinish(session, exitstatus)
    assert session.exitstatus == (pytest.ExitCode.INTERNAL_ERROR if exitstatus == 0 else exitstatus)
    state = trace._profile_state(native_profile_config)
    assert state is not None and state.finished and state.profiler is None
    assert state.root_fd == state.directory_fd == -1


@pytest.mark.parametrize("raw_request", ["0", "unexpected"])
def test_native_cprofile_invalid_request_is_consumed_without_artifacts(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch, raw_request: str,
) -> None:
    import os

    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", raw_request)
    with pytest.raises(pytest.UsageError, match="invalid one-shot"):
        trace._consume_profile_request(native_profile_config)
    assert "_TEST_FILES_CPROFILE" not in os.environ
    assert not (trace._profile_root() / ".gate-logs").exists()


def test_native_cprofile_refuses_non_owning_checkout_and_closes_opened_descriptors(
    native_profile_config: pytest.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from scripts import xdist_trace_plugin as trace

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    original = os.open
    descriptors: list[int] = []

    def opened(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
               *, dir_fd: int | None = None) -> int:
        descriptor = original(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    with pytest.raises(pytest.UsageError, match="owning checkout"):
        trace._consume_profile_request(native_profile_config)
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_native_cprofile_root_swap_refuses_publication(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    _begin_native_profile(native_profile_config, monkeypatch)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    root = trace._profile_root()
    moved = root.with_name("moved-checkout")
    root.rename(moved)
    root.mkdir()
    with pytest.raises(ValueError, match="owning checkout route changed"):
        trace._profile_route(state)
    sentinel = root / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert not trace._finish_profile(native_profile_config, 0)
    assert sorted(path.name for path in root.iterdir()) == ["sentinel"]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (moved / ".gate-logs" / "test-files-cprofile" / state.directory.name / "pytest.pstats").exists()


def test_native_cprofile_allocation_failure_closes_every_descriptor(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from scripts import xdist_trace_plugin as trace

    original = os.open
    descriptors: list[int] = []

    def opened(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
               *, dir_fd: int | None = None) -> int:
        descriptor = original(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(descriptor)
        return descriptor

    class UnavailableState(trace._ProfileState):
        def __init__(self, directory: Path, root_fd: int, directory_fd: int) -> None:
            raise OSError("native state initialization unavailable")

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(trace, "_ProfileState", UnavailableState)
    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    with pytest.raises(pytest.UsageError, match="state initialization unavailable"):
        trace._consume_profile_request(native_profile_config)
    assert len(descriptors) == 4
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("replacement", ["leaf-link", "parent-link", "trace-link", "trace-hardlink", "trace-directory"])
def test_profile_trace_replacement_never_changes_outside_bytes(
    native_profile_config: pytest.Config, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    session = pytest.Session.from_config(native_profile_config)
    trace.pytest_sessionstart(session)
    outside = tmp_path / "outside-trace"
    outside.mkdir()
    sentinel = outside / "trace.jsonl"
    sentinel.write_text("outside stays unchanged", encoding="utf-8")
    path = state.directory / "trace.jsonl"
    if replacement == "leaf-link":
        state.directory.rename(state.directory.with_name(state.directory.name + "-held"))
        state.directory.symlink_to(outside, target_is_directory=True)
    elif replacement == "parent-link":
        parent = state.directory.parent
        parent.rename(parent.with_name("held-profile-parent"))
        (outside / state.directory.name).mkdir()
        (outside / state.directory.name / "trace.jsonl").write_text("nested stays unchanged", encoding="utf-8")
        parent.symlink_to(outside, target_is_directory=True)
    else:
        path.unlink()
        if replacement == "trace-link":
            path.symlink_to(sentinel)
        elif replacement == "trace-hardlink":
            path.hardlink_to(sentinel)
        else:
            path.mkdir()
    refused = False
    try:
        try:
            trace.write_event("START", nodeid="owned::must_refuse")
        except (OSError, ValueError, pytest.UsageError):
            refused = True
    finally:
        trace._finish_profile(native_profile_config, 1)
    assert sentinel.read_text(encoding="utf-8") == "outside stays unchanged"
    if replacement == "parent-link":
        assert (outside / state.directory.name / "trace.jsonl").read_text(encoding="utf-8") == "nested stays unchanged"
    assert refused, "changed profile trace route was accepted"
    assert state.root_fd == state.directory_fd == -1


def test_profile_trace_initial_run_start_refuses_replaced_leaf(
    native_profile_config: pytest.Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    outside = tmp_path / "initial-outside"
    outside.mkdir()
    sentinel = outside / "trace.jsonl"
    sentinel.write_text("initial unchanged", encoding="utf-8")
    state.directory.rename(state.directory.with_name(state.directory.name + "-held"))
    state.directory.symlink_to(outside, target_is_directory=True)
    session = pytest.Session.from_config(native_profile_config)
    refused = False
    try:
        try:
            trace.pytest_sessionstart(session)
        except (OSError, ValueError, pytest.UsageError):
            refused = True
    finally:
        trace._finish_profile(native_profile_config, 1)
    assert sentinel.read_text(encoding="utf-8") == "initial unchanged"
    assert refused


def test_profile_trace_keeps_real_controller_and_worker_events(tmp_path: Path) -> None:
    from tests.unit.test_scoped_admission_targets import _native_cprofile_probe

    result = _native_cprofile_probe(
        tmp_path,
        "import os, sys\n"
        "def test_preserved_worker_trace():\n"
        "    assert os.environ.get('PYTEST_XDIST_WORKER', '').startswith('gw')\n"
        "    assert sys.getprofile() is None\n",
        arguments="-q -W error -n 2",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    prefix = "pytest-cprofile: directory="
    lines = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1
    directory = Path(lines[0].removeprefix(prefix))
    events = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "RUN_START" and event["role"] == "controller" for event in events)
    assert any(event["event"] == "RUN_START" and event["role"] == "worker" for event in events)
    assert any(event["event"] == "START" and event["worker"].startswith("gw") for event in events)
    assert any(event["event"] == "FINISH" and event["worker"].startswith("gw") for event in events)
    assert any(event["event"] == "RUN_FINISH" and event["role"] == "worker" for event in events)


_PROFILE_TRACE_FIFO_FIXTURE = r"""
import os, sys
from pathlib import Path
import pytest
from scripts import xdist_trace_plugin as trace
root = Path(sys.argv[1])
root.mkdir()
os.chdir(root)
trace._profile_root = lambda: root
base = root / "basetemp"
base.mkdir()
config = pytest.Config.fromdictargs({"basetemp": str(base)}, [str(root)])
monkeypatch = pytest.MonkeyPatch()
monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
trace._consume_profile_request(config)
state = trace._profile_state(config)
assert state is not None
os.mkfifo(state.directory / "trace.jsonl")
refused = False
try:
    try:
        trace.pytest_sessionstart(pytest.Session.from_config(config))
    except (OSError, ValueError, pytest.UsageError):
        refused = True
finally:
    trace._finish_profile(config, 1)
    config._ensure_unconfigure()
    monkeypatch.undo()
assert refused, "profile FIFO accepted"
print("native profile FIFO refused", flush=True)
"""


def test_profile_trace_no_writer_fifo_refuses_with_bounded_native_drain(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-B", "-W", "error", "-c", _PROFILE_TRACE_FIFO_FIXTURE, str(tmp_path / "checkout")],
        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
    assert process.poll() is not None
    assert not timed_out, "profile trace blocked on a native no-writer FIFO"
    assert process.returncode == 0, stdout + stderr
    assert stdout.strip() == "native profile FIFO refused", stderr


@pytest.mark.parametrize("replacement", ["trace-link", "trace-hardlink"])
def test_profile_trace_inherited_worker_refuses_outside_file(
    native_profile_config: pytest.Config, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    directory = state.directory
    trace._finish_profile(native_profile_config, 1)
    outside = tmp_path / "worker-outside"
    outside.write_text("worker outside unchanged", encoding="utf-8")
    target = directory / "trace.jsonl"
    if replacement == "trace-link":
        target.symlink_to(outside)
    else:
        target.hardlink_to(outside)
    monkeypatch.setenv("GLUDD_XDIST_TRACE_LOG", str(target))
    monkeypatch.setenv("GLUDD_XDIST_TRACE_RUN_ID", directory.name)
    worker = pytest.Config.fromdictargs({"basetemp": str(tmp_path / "worker-base")}, [str(trace._profile_root())])
    monkeypatch.setattr(worker, "workerinput", {"workerid": "gw1"}, raising=False)
    session = pytest.Session.from_config(worker)
    refused = False
    try:
        try:
            trace.pytest_sessionstart(session)
        except (OSError, ValueError, pytest.UsageError):
            refused = True
    finally:
        trace._finish_profile(worker, 1)
        worker._ensure_unconfigure()
    assert outside.read_text(encoding="utf-8") == "worker outside unchanged"
    assert refused


@pytest.mark.parametrize("member", [
    "other.jsonl", "nested/trace.jsonl", "../trace.jsonl",
])
def test_profile_trace_reserved_namespace_refuses_noncanonical_members(
    native_profile_config: pytest.Config, member: str,
) -> None:
    from scripts import xdist_trace_plugin as trace

    path = trace._profile_root() / ".gate-logs" / "test-files-cprofile" / member
    with pytest.raises(ValueError, match="profile trace"):
        trace._profile_trace_directory(path)
    assert not path.exists()


def test_profile_trace_pins_regular_file_identity_and_refuses_truncation(
    native_profile_config: pytest.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import xdist_trace_plugin as trace

    monkeypatch.setenv("_TEST_FILES_CPROFILE", "1")
    trace._consume_profile_request(native_profile_config)
    session = pytest.Session.from_config(native_profile_config)
    trace.pytest_sessionstart(session)
    state = trace._profile_state(native_profile_config)
    assert state is not None
    path = state.directory / "trace.jsonl"
    original = path.read_bytes()
    path.rename(path.with_name("held-trace.jsonl"))
    path.write_text("replacement unchanged", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="identity changed"):
            trace.write_event("START", nodeid="must::not_append")
        assert path.read_text(encoding="utf-8") == "replacement unchanged"
        assert path.with_name("held-trace.jsonl").read_bytes() == original
        monkeypatch.setenv("GLUDD_XDIST_TRACE_TRUNCATE", "1")
        with pytest.raises(ValueError, match="cannot be truncated"):
            trace.pytest_sessionstart(session)
        assert path.read_text(encoding="utf-8") == "replacement unchanged"
    finally:
        trace._finish_profile(native_profile_config, 1)


def _guidance_event(kind: str, timestamp: float, **values: object) -> str:
    return json.dumps({
        "event": kind, "run_id": "selected-run", "worker": "gw0", "pid": 101,
        "nodeid": "tests/example.py::test_example", "timestamp": timestamp, **values,
    })


_GUIDANCE_PAIRED = (
    _guidance_event("START", 10),
    _guidance_event("FINISH", 12),
)
_GUIDANCE_CASES = [
    pytest.param(None, True, "empty", 0, 0, 0, 0, 0, id="missing"),
    pytest.param((), True, "empty", 0, 0, 0, 0, 0, id="empty"),
    pytest.param(
        (_guidance_event("START", 10, run_id="other-run"),), True,
        "empty", 0, 0, 0, 0, 0, id="filtered-empty",
    ),
    pytest.param(
        (_guidance_event("RUN_START", 10),), True,
        "no-paired-timings", 1, 0, 0, 0, 0, id="events-without-protocol",
    ),
    pytest.param(
        (_guidance_event("START", 10),), True,
        "partial", 1, 0, 1, 1, 0, id="unfinished",
    ),
    pytest.param(("not-json",), False, "partial", 1, 0, 1, 0, 0, id="parse-error"),
    pytest.param(
        (*_GUIDANCE_PAIRED, _guidance_event("FINISH", 13)), True,
        "partial", 3, 1, 1, 0, 0, id="paired-plus-unmatched",
    ),
    pytest.param(
        (_guidance_event("START", 10),
         _guidance_event("REPORT", 11, outcome="failed", when="call"),
         _guidance_event("FINISH", 12)),
        True, "populated-with-failures", 3, 1, 0, 0, 1, id="failed-paired",
    ),
    pytest.param(_GUIDANCE_PAIRED, True, "populated", 2, 1, 0, 0, 0, id="paired"),
]


@pytest.mark.parametrize("trace_guidance_mode", [[], ["--failures-only"], ["--verbose"]])
@pytest.mark.parametrize(
    ("trace_guidance_rows", "trace_guidance_filter", "trace_guidance_outcome",
     "trace_guidance_events", "trace_guidance_pairs", "trace_guidance_issues",
     "trace_guidance_unfinished", "trace_guidance_failures"),
    _GUIDANCE_CASES,
)
def test_summary_cli_guidance_uses_actual_selected_evidence_without_json_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    trace_guidance_mode: list[str], trace_guidance_rows: tuple[str, ...] | None,
    trace_guidance_filter: bool, trace_guidance_outcome: str,
    trace_guidance_events: int, trace_guidance_pairs: int, trace_guidance_issues: int,
    trace_guidance_unfinished: int, trace_guidance_failures: int,
) -> None:
    from scripts import summarize_xdist_trace as summary_module

    path = tmp_path / "trace.jsonl"
    if trace_guidance_rows is not None:
        path.write_text("".join(row + chr(10) for row in trace_guidance_rows), encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    selected_run = "selected-run" if trace_guidance_filter else None
    summary = summary_module.summarize_log(path, run_id=selected_run)
    if "--verbose" in trace_guidance_mode:
        expected = summary
    elif "--failures-only" in trace_guidance_mode:
        expected = summary_module.failures_only_summary(summary)
    else:
        expected = summary_module.compact_summary(summary)
    arguments = [*trace_guidance_mode]
    if selected_run is not None:
        arguments.extend(["--run-id", selected_run])

    assert summary_module.main([*arguments, str(path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == json.dumps(expected, indent=2, sort_keys=True) + chr(10)
    assert summary["events"] == trace_guidance_events
    assert summary["timings"]["paired_count"] == trace_guidance_pairs
    assert summary["timings"]["issue_count"] == trace_guidance_issues
    assert len(summary["unfinished"]) == trace_guidance_unfinished
    assert summary["failure_report_count"] == trace_guidance_failures
    assert captured.err.startswith(
        "AGENT_NEXT target=test-xdist-trace-summary outcome=" + trace_guidance_outcome + " "
    )
    for expected_field in (
        f"events={trace_guidance_events}", f"paired_count={trace_guidance_pairs}",
        f"issue_count={trace_guidance_issues}", f"unfinished={trace_guidance_unfinished}",
        f"failure_reports={trace_guidance_failures}", "gate_evidence=false",
        "release_ready=false", "Exit 0 means summary generation",
    ):
        assert expected_field in captured.err
    if trace_guidance_outcome == "empty":
        assert "timing, cause and performance remain unknown" in captured.err
        assert "exact LOG/RUN_ID" in captured.err
    elif trace_guidance_outcome == "partial":
        assert "missing intervals remain unknown" in captured.err
    elif trace_guidance_outcome == "no-paired-timings":
        assert "no accepted paired timing spans" in captured.err
    else:
        assert "wall spans include fixtures and child waits" in captured.err
        assert "not CPU or suite elapsed time" in captured.err
    assert captured.err.count(chr(10)) == 1
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("trace_guidance_library_rows", [(), _GUIDANCE_PAIRED])
def test_summary_library_projection_remains_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
    trace_guidance_library_rows: tuple[str, ...],
) -> None:
    from scripts import summarize_xdist_trace as summary_module

    path = tmp_path / "trace.jsonl"
    path.write_text("".join(row + chr(10) for row in trace_guidance_library_rows), encoding="utf-8")
    summary = summary_module.summarize_log(path)
    assert summary_module.compact_summary(summary)["events"] == len(trace_guidance_library_rows)
    assert summary_module.failures_only_summary(summary)["failure_report_count"] == 0
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("trace_guidance_native_rows", "trace_guidance_native_outcome"),
    [
        ((), "empty"),
        ((_guidance_event("START", 10),), "partial"),
        (_GUIDANCE_PAIRED, "populated"),
    ],
)
def test_native_summary_cli_keeps_json_status_and_emits_guidance(
    tmp_path: Path, trace_guidance_native_rows: tuple[str, ...],
    trace_guidance_native_outcome: str,
) -> None:
    from scripts import summarize_xdist_trace as summary_module

    path = tmp_path / "trace with spaces.jsonl"
    path.write_text("".join(row + chr(10) for row in trace_guidance_native_rows), encoding="utf-8")
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(Path(summary_module.__file__).resolve()), "--include-timings",
         "--run-id", "selected-run", "--", str(path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == summary_module.compact_summary(
        summary_module.summarize_log(path, run_id="selected-run"), include_timings=True,
    )
    assert "outcome=" + trace_guidance_native_outcome + " " in result.stderr
    assert "gate_evidence=false release_ready=false" in result.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("trace_guidance_failure", "trace_guidance_status", "trace_guidance_error"),
    [("invalid-run-id", 2, "run ID must be a safe path component"),
     ("unreadable-input", 1, "IsADirectoryError")],
)
def test_native_summary_cli_preserves_parse_and_io_failure_status(
    tmp_path: Path, trace_guidance_failure: str,
    trace_guidance_status: int, trace_guidance_error: str,
) -> None:
    from scripts import summarize_xdist_trace as summary_module

    arguments = ["--run-id", "../unsafe"] if trace_guidance_failure == "invalid-run-id" else []
    result = subprocess.run(
        [sys.executable, str(Path(summary_module.__file__).resolve()), *arguments, str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == trace_guidance_status
    assert result.stdout == ""
    assert trace_guidance_error in result.stderr
    assert "AGENT_NEXT" not in result.stderr


@pytest.mark.parametrize("trace_guidance_make_e", [False, True])
def test_summary_recipe_retains_native_quoted_run_and_log_arguments(
    tmp_path: Path, trace_guidance_make_e: bool,
) -> None:
    from tests.unit.test_trace_summary_make_inputs import _assert_call, _run, _values

    values = {**_values(tmp_path), "TRACE_SUMMARY_TIMINGS": "1", "TRACE_SUMMARY_MEMORY": "1"}
    result = _run(tmp_path, values, trace_environment_overrides=trace_guidance_make_e)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_call(tmp_path, values)
