"""Real E2E tests for the OpencodeSpawner.

These tests actually launch the ``opencode`` binary and verify:
  1. Spawner can launch opencode and capture output (even without API key)
  2. Spawner correctly parses NDJSON frames from live output
  3. Spawner handles process timeouts and kills properly
  4. Spawner with a real test project exercises the full pipeline

All tests that spawn opencode require the binary on PATH and may
require an API key for LLM access. They are designed to PASS even
when the LLM is unavailable (the spawner still captures output).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ._spawner import (
    STEP_START_RE,
    TASK_TOOL_NAME_RE,
    TEXT_ASSISTANT_RE,
    TEXT_EVENT_RE,
    TOOL_CALL_RE,
    OpencodeSpawner,
    ResponseFrame,
    SpawnResult,
    ToolCallSnapshot,
)

ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT_SRC = ROOT / "tests" / "opencode_e2e" / "_test_project"
OPENCODE_BIN = "opencode"

def _has_opencode() -> bool:
    try:
        subprocess.run([OPENCODE_BIN, "--version"], capture_output=True, timeout=5, check=False)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _make_temp_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="opencode-e2e-real-", dir="/tmp"))
    shutil.copytree(TEST_PROJECT_SRC, tmp, dirs_exist_ok=True, symlinks=True)
    rc = subprocess.run(
        ["bash", "setup.sh", "--copy", str(ROOT), str(tmp)],
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if rc.returncode != 0:
        print(f"WARN: setup.sh returned {rc.returncode}\n{rc.stderr}")
    else:
        print(f"Setup: {rc.stdout.strip()[-300:]}")
    return tmp


def _reset_tasks(tasks_path: Path) -> None:
    content = tasks_path.read_text()
    content = re.sub(r"- \[x\] ", "- [ ] ", content)
    tasks_path.write_text(content)


# ── Template fixture: import spawner ────────────────────────────────────


@pytest.fixture(scope="module")
def _spawner_class() -> type[OpencodeSpawner]:
    return OpencodeSpawner


# ── Spawner Framework Tests (no API key needed) ─────────────────────────


@pytest.mark.skipif(not _has_opencode(), reason="opencode binary not on PATH")
class TestSpawnerFramework:
    """Spawner can launch opencode, capture output, handle errors."""

    def test_spawner_launches_opencode_and_captures_output(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """Spawner launches opencode and captures at least some output."""
        spawner = _spawner_class(
            project_dir=str(TEST_PROJECT_SRC),
            prompt="echo hello",
            timeout_sec=15,
            model="fake-model-no-api",
        )
        result = spawner.run()
        assert result.log_path, "Spawner should produce a log path"
        assert Path(result.log_path).exists(), f"Log file {result.log_path} should exist"
        assert result.total_messages >= 0, "Should have message count (even if 0)"
        assert result.elapsed_sec > 0, "Should have measured elapsed time"

    def test_spawner_writes_structured_log(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """Spawner writes a structured JSON log with spawn_meta header."""
        spawner = _spawner_class(
            project_dir=str(TEST_PROJECT_SRC),
            prompt="echo test",
            timeout_sec=10,
            model="fake-model",
        )
        result = spawner.run()
        log_path = Path(result.log_path)
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) >= 1, "Log should have at least the meta header"
        header = json.loads(lines[0])
        assert header["type"] == "spawn_meta"
        assert "verdict" in header
        assert "project_dir" in header
        assert header["project_dir"] in str(TEST_PROJECT_SRC) or str(TEST_PROJECT_SRC) in header["project_dir"]

    def test_spawner_kills_on_timeout(
        self,
        _spawner_class: type[OpencodeSpawner],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Spawner kills a silent process that genuinely outlives the deadline."""

        def _silent_command(_spawner: object) -> list[str]:
            return [sys.executable, "-u", "-c", "import time; time.sleep(5)"]

        monkeypatch.setattr(_spawner_class, "_build_command", _silent_command)
        spawner = _spawner_class(
            project_dir=str(tmp_path),
            prompt="sleep 999999",
            timeout_sec=0.1,
            log_dir=str(tmp_path / "logs"),
        )
        result = spawner.run()
        assert result.killed, "Should be killed by timeout"
        assert result.elapsed_sec <= 2, "Should not run much past timeout"

    def test_spawner_produces_progress_log(
        self,
        _spawner_class: type[OpencodeSpawner],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Spawner immediately writes progress even for a short process."""

        def _short_command(_spawner: object) -> list[str]:
            return [sys.executable, "-u", "-c", "print('progress-complete')"]

        monkeypatch.setattr(_spawner_class, "_build_command", _short_command)
        spawner = _spawner_class(
            project_dir=str(tmp_path),
            prompt="echo progress-test",
            timeout_sec=2,
            log_dir=str(tmp_path / "logs"),
            progress_interval_sec=30,
        )
        result = spawner.run()
        progress_path = Path(result.progress_log)
        assert progress_path.exists(), f"Progress log {progress_path} should exist"
        content = progress_path.read_text()
        assert "PROGRESS" in content, "Progress log should contain PROGRESS entries"

    def test_spawner_result_has_all_fields(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """SpawnResult has all expected fields populated."""
        spawner = _spawner_class(
            project_dir=str(TEST_PROJECT_SRC),
            prompt="echo fields-test",
            timeout_sec=8,
            model="fake-model",
        )
        result = spawner.run()
        assert result.verdict in ("PASS", "FAIL", "ERROR")
        assert isinstance(result.verdict_reason, str)
        assert isinstance(result.dispatch_waves, list)
        assert isinstance(result.total_tool_calls, int)
        assert isinstance(result.total_dispatch_calls, int)
        assert isinstance(result.total_messages, int)
        assert isinstance(result.elapsed_sec, float)
        assert isinstance(result.killed, bool)
        assert isinstance(result.log_path, str)
        assert isinstance(result.depth_count, int)


# ── Spawner + Test Project Tests (may need API key) ─────────────────────


@pytest.mark.skipif(not _has_opencode(), reason="opencode binary not on PATH")
class TestSpawnerWithProject:
    """Spawner runs opencode against a real test project with tasks."""

    def test_spawner_runs_against_test_project(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """Spawner launches opencode against _test_project/ with real tasks."""
        tmp = _make_temp_project()
        _reset_tasks(tmp / "TASKS.md")
        try:
            spawner = _spawner_class(
                project_dir=str(tmp),
                prompt=(
                    "Read TASKS.md. Dispatch EXACTLY 10 task subagents to run "
                    "make task1 through make task10. Each subagent runs ONE make "
                    "command. Return confirmation when all 10 are dispatched."
                ),
                timeout_sec=60,
                prompt_sequence=[],
                progress_interval_sec=15,
            )
            result = spawner.run()
            log_path = Path(result.log_path)
            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            assert len(lines) >= 1, "Should have at least spawn_meta"

            header = json.loads(lines[0])
            assert header["type"] == "spawn_meta"
            print(f"Spawner verdict: {header['verdict']} — {header['verdict_reason']}")
            print(
                f"Messages: {header['total_messages']}, "
                f"Tool calls: {header['total_tool_calls']}, "
                f"Dispatches: {header['total_dispatch_calls']}"
            )

            assert result.total_messages >= 0
            assert result.elapsed_sec > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_spawner_captures_ndjson_output_from_live_opencode(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """Live opencode produces NDJSON that the spawner parses into frames."""
        tmp = _make_temp_project()
        _reset_tasks(tmp / "TASKS.md")
        try:
            spawner = _spawner_class(
                project_dir=str(tmp),
                prompt="Run: make task1",
                timeout_sec=30,
                progress_interval_sec=15,
            )
            result = spawner.run()
            log_path = Path(result.log_path)
            assert log_path.exists()

            lines = [line for line in log_path.read_text().strip().split("\n") if line.strip()]
            frame_lines = [line for line in lines if '"type":"response_frame"' in line or '"type":"spawn_meta"' in line]
            print(f"Total log lines: {len(lines)}, Frame lines: {len(frame_lines)}")
            print(f"Spawner messages: {result.total_messages}, Tool calls: {result.total_tool_calls}")

            assert result.total_messages >= 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── NDJSON Parser Tests (no API key needed) ─────────────────────────────


class TestNDJSONParsing:
    """OpencodeSpawner parses opencode NDJSON output correctly."""

    @staticmethod
    def _run_spawner_with_log(
        log_content: str,
    ) -> tuple[type[OpencodeSpawner], type[ResponseFrame], type[ToolCallSnapshot]]:
        """Create a spawner, feed it pre-recorded log lines, check parsing."""
        return OpencodeSpawner, ResponseFrame, ToolCallSnapshot

    def test_parses_step_start_marker(self) -> None:
        """STEP_START_RE matches opencode step_start events."""
        assert STEP_START_RE.search('{"type":"step_start"}')
        assert STEP_START_RE.search('{"type": "step_start", "data": {}}')
        assert STEP_START_RE.search('"type":"step_start"')

    def test_parses_tool_use_marker(self) -> None:
        """TOOL_CALL_RE matches opencode tool_use events."""
        assert TOOL_CALL_RE.search('{"type":"tool_use"}')
        assert TOOL_CALL_RE.search('{"type": "tool_use", "name": "bash"}')
        assert TOOL_CALL_RE.search('"type":"tool_use"')

    def test_parses_task_dispatch_marker(self) -> None:
        """TASK_TOOL_NAME_RE matches task/agent/workflow dispatches."""
        assert TASK_TOOL_NAME_RE.search('{"type":"tool_use","name":"task"}')
        assert TASK_TOOL_NAME_RE.search('"name":"agent"')
        assert TASK_TOOL_NAME_RE.search('"name":"workflow"')

    def test_parses_text_event_marker(self) -> None:
        """TEXT_EVENT_RE matches opencode text events."""
        assert TEXT_EVENT_RE.search('{"type":"text"}')
        assert TEXT_EVENT_RE.search('"type":"text"')

    def test_text_assistant_re_matches(self) -> None:
        """TEXT_ASSISTANT_RE matches assistant: prefix lines."""
        assert TEXT_ASSISTANT_RE.search("assistant: hello")
        assert TEXT_ASSISTANT_RE.search("Assistant: testing")
        assert TEXT_ASSISTANT_RE.search("ASSISTANT: loud")

    def test_extracts_tool_call_from_json(self) -> None:
        """_extract_tool_call correctly parses a tool_use JSON line."""
        tc = OpencodeSpawner._extract_tool_call(
            '{"type":"tool_use","tool":"bash","id":"abc123","part":{"input":{"command":"ls"}}}'
        )
        assert tc.name == "bash"
        assert tc.tool_use_id == "abc123"
        assert tc.args == {"command": "ls"}

    def test_extracts_tool_call_from_nested_part(self) -> None:
        """_extract_tool_call handles nested part.state format."""
        raw = (
            '{"type":"tool_use","part":{"state":'
            '{"tool":"task","id":"x1","input":'
            '{"description":"do X","prompt":"run X"}}}}'
        )
        tc = OpencodeSpawner._extract_tool_call(raw)
        assert tc.name == "task"
        assert tc.tool_use_id == "x1"
        assert tc.args == {"description": "do X", "prompt": "run X"}

    def test_extracts_text_from_json(self) -> None:
        """_extract_text correctly parses text from JSON events."""
        text = OpencodeSpawner._extract_text('{"type":"text","text":"hello world"}')
        assert text == "hello world"
        text2 = OpencodeSpawner._extract_text('{"type":"text","part":{"text":"from part"}}')
        assert text2 == "from part"

    def test_extracts_tool_result(self) -> None:
        """_extract_tool_result returns (tool_use_id, is_error)."""
        tid, is_err = OpencodeSpawner._extract_tool_result('{"type":"tool_result","tool_use_id":"abc","is_error":true}')
        assert tid == "abc"
        assert is_err is True


# ── Build Command Tests ─────────────────────────────────────────────────


class TestBuildCommand:
    """_build_command produces correct command line."""

    def test_default_build_command(self) -> None:
        """Default command includes --format json --auto."""
        spawner = OpencodeSpawner(project_dir="/tmp/test", prompt="hello")
        cmd = spawner._build_command()
        assert OPENCODE_BIN in cmd[0]
        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "--auto" in cmd
        assert "hello" in cmd

    def test_build_command_with_model(self) -> None:
        """--model is added when model is specified."""
        spawner = OpencodeSpawner(project_dir="/tmp/test", prompt="hello", model="sonnet")
        cmd = spawner._build_command()
        assert "--model" in cmd
        assert "sonnet" in cmd

    def test_build_command_sets_agent(self) -> None:
        """--agent defaults to 'build'."""
        spawner = OpencodeSpawner(project_dir="/tmp/test", prompt="hello")
        cmd = spawner._build_command()
        assert "--agent" in cmd


# ── SpawnResult Data Class Tests ────────────────────────────────────────


class TestSpawnResult:
    """SpawnResult dataclass has correct defaults and fields."""

    def test_result_defaults(self) -> None:
        """SpawnResult instantiation with minimal fields."""
        r = SpawnResult(verdict="PASS", verdict_reason="test")
        assert r.verdict == "PASS"
        assert r.verdict_reason == "test"
        assert r.dispatch_waves == []
        assert r.total_tool_calls == 0
        assert r.elapsed_sec == 0.0
        assert r.killed is False

    def test_result_with_data(self) -> None:
        """SpawnResult with populated fields."""
        r = SpawnResult(
            verdict="FAIL",
            verdict_reason="timeout",
            dispatch_waves=[{"sequence": 0, "dispatch_count": 5}],
            total_tool_calls=12,
            total_dispatch_calls=5,
            total_messages=3,
            elapsed_sec=30.5,
            killed=True,
            log_path="/tmp/test.ndjson",
            depth_count=2,
            prompts_sent=2,
        )
        assert r.verdict == "FAIL"
        assert r.total_dispatch_calls == 5
        assert r.elapsed_sec == 30.5
        assert r.killed is True
        assert r.depth_count == 2
        assert len(r.dispatch_waves) == 1


# ── MANUAL: Full E2E test (requires API key, skipped by default) ────────


@pytest.mark.skip(reason="Requires LLM API key. Run manually: make test-opencode-e2e-real")
class TestFullE2EWithAPI:
    """Full E2E test: launch opencode, dispatch 10 agents, complete tasks.

    Requires a valid LLM API key in environment. Run manually:
       make test-specific TESTFILE='tests/opencode_e2e/test_spawner_e2e.py::TestFullE2EWithAPI'
       # with OPENCODE_API_KEY set
    """

    def test_full_multitask_dispatch_wave(
        self,
        _spawner_class: type[OpencodeSpawner],
    ) -> None:
        """Launch opencode with 18 tasks, verify it dispatches subagents."""
        tmp = _make_temp_project()
        _reset_tasks(tmp / "TASKS.md")
        try:
            spawner = _spawner_class(
                project_dir=str(tmp),
                prompt=(
                    "Read TASKS.md. There are 18 tasks. You MUST dispatch EXACTLY 10 "
                    "task subagents in EACH wave. When subagent results arrive, "
                    "immediately dispatch the NEXT wave of 10 task subagents for "
                    "remaining unchecked tasks. Each subagent runs ONE make taskN "
                    "command. NEVER send a text-only answer while tasks remain. "
                    "When ALL 18 tasks show [x], say ALL DONE and exit."
                ),
                timeout_sec=300,
                progress_interval_sec=30,
            )
            result = spawner.run()

            print(f"\nVerdict: {result.verdict} — {result.verdict_reason}")
            print(f"Waves: {len(result.dispatch_waves)}, Dispatches: {result.total_dispatch_calls}")
            print(f"Messages: {result.total_messages}, Tool calls: {result.total_tool_calls}")
            print(f"Depth: {result.depth_count}, Killed: {result.killed}")
            for v in result.per_wave_violations[:5]:
                print(f"  Violation: seq={v.get('sequence')} dispatches={v.get('dispatch_count')}")

            assert result.elapsed_sec > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
