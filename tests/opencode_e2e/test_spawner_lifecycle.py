"""Deterministic lifecycle coverage for the OpenCode E2E spawner."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from ._spawner import OpencodeSpawner, ResponseFrame, SpawnResult, ToolCallSnapshot


def _run_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
    *,
    timeout_sec: float = 2.0,
    progress_interval_sec: float = 30.0,
    env: dict[str, str] | None = None,
) -> SpawnResult:
    """Run a namespaced Python child through the real capture lifecycle."""

    def _command(_spawner: OpencodeSpawner) -> list[str]:
        return [sys.executable, "-u", "-c", script]

    monkeypatch.setattr(OpencodeSpawner, "_build_command", _command)
    return OpencodeSpawner(
        project_dir=str(tmp_path),
        prompt="deterministic lifecycle probe",
        timeout_sec=timeout_sec,
        log_dir=str(tmp_path / "logs"),
        progress_interval_sec=progress_interval_sec,
        env=env,
    ).run()


def test_early_exit_is_reaped_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A naturally exiting child is reaped without being called a timeout."""
    result = _run_script(monkeypatch, tmp_path, "print('early-exit', flush=True)")

    assert result.killed is False
    assert result.verdict == "FAIL"
    assert result.verdict_reason.startswith("No dispatches")
    raw_log = Path(result.log_path.replace(".ndjson", ".raw.log"))
    assert "early-exit" in raw_log.read_text()


def test_timeout_is_bounded_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A silent child cannot block the timeout check in ``readline``."""
    result = _run_script(
        monkeypatch,
        tmp_path,
        "import time; time.sleep(2)",
        timeout_sec=0.1,
    )

    assert result.killed is True
    assert result.elapsed_sec < 1.5
    assert result.verdict == "FAIL"
    assert result.verdict_reason.startswith("Killed by timeout")


def test_timeout_cleans_up_descendant_process_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """TERM-resistant descendants are stopped by the bounded KILL fallback."""
    heartbeat = tmp_path / "child-heartbeat"
    child_pid_path = tmp_path / "child.pid"
    child_script = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"handle=open({str(heartbeat)!r}, 'a', buffering=1); "
        "exec(\"while True:\\n handle.write('x')\\n handle.flush()\\n time.sleep(0.02)\")"
    )
    parent_script = (
        "import pathlib,signal,subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable, '-u', '-c', {child_script!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(2)"
    )

    try:
        result = _run_script(
            monkeypatch,
            tmp_path,
            parent_script,
            timeout_sec=0.1,
            progress_interval_sec=0.05,
        )
        assert result.killed is True
        assert child_pid_path.exists()
        size_after_run = heartbeat.stat().st_size
        time.sleep(0.15)
        assert heartbeat.stat().st_size == size_after_run
    finally:
        if child_pid_path.exists():
            child_pid = int(child_pid_path.read_text())
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_progress_is_immediate_flushed_and_run_namespaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Short runs still publish progress and cannot clobber sibling logs."""
    first = _run_script(
        monkeypatch,
        tmp_path,
        "pass",
        env={"GLUDD_PROJECT_NAMESPACE": "project / unsafe"},
    )
    second = _run_script(monkeypatch, tmp_path, "pass")

    assert first.progress_log != second.progress_log
    assert "project-unsafe" in Path(first.progress_log).name
    for result in (first, second):
        progress = Path(result.progress_log)
        assert progress.exists()
        assert progress.read_text().startswith("PROGRESS ")


def test_real_capture_loop_parses_mixed_ndjson_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The threaded reader preserves every supported OpenCode event shape."""
    events = [
        "",
        '{"type":"step_start"}',
        '{"type":"text","part":{"text":"first"}}',
        '{"type":"tool_use","tool":"task","id":"ok-1","input":{"x":1}}',
        '{"type":"step_start"}',
        '{"type":"content_block_delta"}',
        '{"type":"content_block_start"}',
        "plain nested text",
        '{"type":"tool_use","tool":"agent","id":"ok-2"}',
        '{"type":"step_finish"}',
        '{"type":"step_finish"}',
        '{"type":"message_start","role":"assistant"}',
        '{"type":"text","text":"message text"}',
        '{"type":"message_start","role":"user"}',
        "ignored user line",
        "assistant: begin",
        "plain assistant text",
        "Assistant: next",
        "workflow(do_work)",
        '{"type":"tool_result","tool_use_id":"missing","is_error":false}',
        '{"type":"step_start"}',
        '{"type":"tool_use","tool":"task","id":"bad-1"}',
        '{"type":"tool_result","tool_use_id":"bad-1","is_error":true}',
        '{"type":"message_start","role":"assistant"}',
        "tail without finish",
    ]
    script = (
        "import sys\n"
        f"for line in {events!r}:\n print(line, flush=True)\n"
        "print('captured stderr', file=sys.stderr, flush=True)"
    )

    result = _run_script(monkeypatch, tmp_path, script)

    assert result.verdict == "PASS"
    assert result.total_dispatch_calls == 3
    assert result.total_messages >= 6
    assert result.depth_count == 2
    assert result.per_wave_violations
    assert result.text_only_stops
    raw_log = Path(result.log_path.replace(".ndjson", ".raw.log"))
    assert "captured stderr" in raw_log.read_text()


def test_timeout_with_dispatch_preserves_success_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Timeout remains successful only when observable dispatch work preceded it."""
    events = ['{"type":"step_start"}'] + [
        f'{{"type":"tool_use","tool":"task","id":"task-{index}"}}' for index in range(10)
    ]
    script = f"import time\nfor line in {events!r}:\n print(line, flush=True)\ntime.sleep(2)"

    result = _run_script(monkeypatch, tmp_path, script, timeout_sec=0.3)

    assert result.killed is True
    assert result.total_dispatch_calls == 10
    assert result.verdict == "PASS"
    assert result.verdict_reason.startswith("Still dispatching when killed")


def test_parser_edge_cases_and_dispatch_correction() -> None:
    """Malformed and text-form events remain conservative and deterministic."""
    assert OpencodeSpawner._extract_tool_call("{not-json").name == "unknown"
    assert OpencodeSpawner._extract_tool_call('"name":"task"').name == "task"
    assert OpencodeSpawner._extract_tool_call("ordinary output").name == "unknown"
    assert OpencodeSpawner._extract_text("ordinary output") == ""
    assert OpencodeSpawner._extract_text("{not-json") == ""
    assert OpencodeSpawner._extract_tool_result("ordinary output") == ("", False)
    assert OpencodeSpawner._extract_tool_result("{not-json") == ("", False)
    nested_result = '{"type":"tool_result","part":{"result":{"tool_use_id":"x","is_error":true}}}'
    assert OpencodeSpawner._extract_tool_result(nested_result) == ("x", True)

    frame = ResponseFrame(
        dispatch_count=1,
        tool_calls=[ToolCallSnapshot(name="task", tool_use_id="x")],
    )
    OpencodeSpawner._correct_dispatch_count(frame, {"x"})
    assert frame.dispatch_count == 0
    assert frame.tool_calls[0].is_error is True


def test_analysis_records_violations_and_text_only_stops(tmp_path: Path) -> None:
    """Analysis keeps policy violations visible while preserving verdict rules."""
    spawner = OpencodeSpawner(project_dir=str(tmp_path), prompt="analyze", log_dir=str(tmp_path / "logs"))
    frame = ResponseFrame(
        sequence=2,
        text_content="premature stop",
        dispatch_count=3,
        tool_call_count=3,
        is_text_only=True,
    )

    completed = spawner._analyze([frame], elapsed=0.1, killed=False, depth_count=2)
    timed_out = spawner._analyze([frame], elapsed=0.2, killed=True, depth_count=2)

    assert completed.verdict == "PASS"
    assert completed.per_wave_violations[0]["dispatch_count"] == 3
    assert completed.text_only_stops[0]["text_preview"] == "premature stop"
    assert completed.responses[0]["sequence"] == 2
    assert timed_out.verdict == "PASS"
    assert timed_out.verdict_reason.startswith("Still dispatching when killed")

    empty_result = SpawnResult(verdict="FAIL")
    spawner._write_structured_log(empty_result, [])
    spawner._write_raw_log([], [])
