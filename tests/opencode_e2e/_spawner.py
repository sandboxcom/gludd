"""Spawn opencode as a subprocess, monitor its output, and collect metrics.

Usage::

    from tests.opencode_e2e._spawner import OpencodeSpawner, SpawnResult

    spawner = OpencodeSpawner(
        project_dir="/tmp/opencode-e2e-test/",
        prompt="Write a Python function that returns the sum of two numbers.",
        timeout_sec=60,
    )
    result = spawner.run()
    print(result.verdict)          # "PASS" or "FAIL"
    print(result.dispatch_waves)   # list[dict]
    print(result.text_only_stops)  # list[dict]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

OPENCODE_BIN = "opencode"

TOOL_CALL_RE = re.compile(r'(?:tool_use|"type"\s*:\s*"tool_use"|"type":"tool_use")')
TASK_TOOL_NAME_RE = re.compile(
    r'(?:"name"\s*:\s*"(task|agent|workflow)"'
    r'|"tool"\s*:\s*"(task|agent|workflow)"'
    r"|\(task\b|\(agent\b|\(workflow\b)"
)
ASSISTANT_DELTA_RE = re.compile(r'(?:content_block_delta|"type"\s*:\s*"content_block_delta")')
ASSISTANT_START_RE = re.compile(r'(?:content_block_start|"type"\s*:\s*"content_block_start")')
STEP_START_RE = re.compile(r'(?:step_start|"type"\s*:\s*"step_start"|"type":"step_start")')
STEP_FINISH_RE = re.compile(r'(?:step_finish|"type"\s*:\s*"step_finish"|"type":"step_finish")')
MESSAGE_START_RE = re.compile(r'(?:message_start|"type"\s*:\s*"message_start")')
MESSAGE_ROLE_RE = re.compile(r'(?:role"\s*:\s*"assistant|\bassistant\b)')
TOOL_RESULT_RE = re.compile(r'(?:tool_result|"type"\s*:\s*"tool_result")')
DISPATCH_TEXT_RE = re.compile(r"(?:\btask\s*\(|\bagent\s*\(|\bworkflow\s*\()")
TEXT_ASSISTANT_RE = re.compile(r"^\s*(?:assistant|Assistant|ASSISTANT)\s*[>:]")
TEXT_EVENT_RE = re.compile(r'(?:"type"\s*:\s*"text"|"type":"text")')


@dataclass
class ToolCallSnapshot:
    name: str
    timestamp: float = 0.0
    args: dict = field(default_factory=dict)


@dataclass
class ResponseFrame:
    """One assistant response (text + tool calls)."""

    sequence: int = 0
    timestamp: float = 0.0
    text_content: str = ""
    tool_calls: list[ToolCallSnapshot] = field(default_factory=list)
    dispatch_count: int = 0
    tool_call_count: int = 0
    is_text_only: bool = True
    raw_lines: list[str] = field(default_factory=list)


@dataclass
class SpawnResult:
    verdict: str  # "PASS" | "FAIL" | "ERROR"
    dispatch_waves: list[dict[str, object]] = field(default_factory=list)
    text_only_stops: list[dict[str, object]] = field(default_factory=list)
    responses: list[dict[str, object]] = field(default_factory=list)
    total_tool_calls: int = 0
    total_dispatch_calls: int = 0
    total_messages: int = 0
    elapsed_sec: float = 0.0
    killed: bool = False
    log_path: str = ""


class OpencodeSpawner:
    """Spawn opencode run with --format json, parse NDJSON output, detect stop bugs."""

    def __init__(
        self,
        project_dir: str,
        prompt: str,
        *,
        timeout_sec: int = 120,
        log_dir: str = "/tmp/gludd-opencode-e2e",
        agent: str = "build",
        model: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._project_dir = os.path.abspath(project_dir)
        self._prompt = prompt
        self._timeout_sec = timeout_sec
        self._agent = agent
        self._model = model
        self._env = dict(os.environ)
        if env:
            self._env.update(env)
        self._env.setdefault("OPENCODE_SUBAGENT", "0")
        self._env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
        self._env.setdefault("GLUDD_FLOOR_ENFORCE", "0")
        self._env.setdefault("GLUDD_SESSION_START_ENFORCE", "0")
        self._env.setdefault("GLUDD_MAINTHREAD_STREAK_ENFORCE", "0")
        self._env.setdefault("GLUDD_MULTITASK_FLOOR_ENFORCE", "0")
        self._env.setdefault("GLUDD_ENHANCEMENT_RATIO_BLOCK", "0")
        self._env.setdefault("GLUDD_CLEAN_TREE_ENFORCE", "0")
        self._env.setdefault("GLUDD_TDD_ENFORCE", "0")
        self._env.setdefault("GLUDD_TASK_DEADLINE_BLOCK", "0")
        self._env.setdefault("GLUDD_MAKE_ENFORCE", "0")
        self._env.setdefault("GLUDD_VERIFIED_CLAIMS_ENFORCE", "0")
        self._env.setdefault("OPENCODE_DISABLE_CLAUDE_CODE", "0")
        os.makedirs(log_dir, exist_ok=True)
        self._log_dir = log_dir
        self._log_path = ""

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self) -> SpawnResult:
        log_name = f"opencode-e2e-{int(time.time())}.ndjson"
        self._log_path = os.path.join(self._log_dir, log_name)
        frames, elapsed, killed, raw_stdout, raw_stderr = self._spawn_and_capture()
        result = self._analyze(frames, elapsed, killed)
        self._write_structured_log(result, frames)
        self._write_raw_log(raw_stdout, raw_stderr)
        return result

    # ------------------------------------------------------------------
    # subprocess lifecycle
    # ------------------------------------------------------------------

    def _spawn_and_capture(self) -> tuple[list[ResponseFrame], float, bool, list[str], list[str]]:
        cmd = self._build_command()
        proc = subprocess.Popen(
            cmd,
            cwd=self._project_dir,
            env=self._env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if proc.stdin is not None:
            proc.stdin.write(self._prompt + "\n")
            proc.stdin.flush()
            proc.stdin.close()

        frames: list[ResponseFrame] = []
        current = ResponseFrame(sequence=0, timestamp=time.time())
        in_assistant = False
        killed = False
        t0 = time.time()
        stderr_lines: list[str] = []
        raw_stdout_lines: list[str] = []

        def _read_stderr() -> None:
            try:
                while proc.poll() is None:
                    line = proc.stderr.readline() if proc.stderr else ""
                    if not line:
                        time.sleep(0.05)
                        continue
                    stderr_lines.append(line.rstrip("\n"))
            except Exception:
                pass

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        try:
            while True:
                if time.time() - t0 > self._timeout_sec:
                    killed = True
                    break
                if proc.poll() is not None:
                    break
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    time.sleep(0.05)
                    continue
                raw_stdout_lines.append(line.rstrip("\n"))
                stripped = line.strip()
                if not stripped:
                    continue
                current.raw_lines.append(stripped)

                _is_json = stripped.startswith("{") and '"type"' in stripped

                if _is_json and STEP_START_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    in_assistant = True
                    current.is_text_only = True
                    continue

                if _is_json and MESSAGE_START_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    in_assistant = bool(MESSAGE_ROLE_RE.search(stripped))
                    continue

                if TEXT_ASSISTANT_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    in_assistant = True
                    current.is_text_only = False
                    continue

                if not in_assistant:
                    continue

                if _is_json and TEXT_EVENT_RE.search(stripped):
                    current.is_text_only = False
                    text = self._extract_text(stripped)
                    current.text_content += text + "\n"
                    continue

                if TOOL_CALL_RE.search(stripped):
                    tc = self._extract_tool_call(stripped)
                    current.tool_calls.append(tc)
                    current.tool_call_count = len(current.tool_calls)
                    current.is_text_only = False
                    if TASK_TOOL_NAME_RE.search(stripped):
                        current.dispatch_count += 1
                    continue

                if DISPATCH_TEXT_RE.search(stripped):
                    current.dispatch_count += 1
                    current.tool_call_count += 1
                    current.is_text_only = False
                    tc = ToolCallSnapshot(name="dispatch", timestamp=time.time())
                    current.tool_calls.append(tc)
                    continue

                if ASSISTANT_DELTA_RE.search(stripped):
                    current.is_text_only = False
                    continue
                if ASSISTANT_START_RE.search(stripped):
                    current.is_text_only = False
                    continue

                if _is_json and STEP_FINISH_RE.search(stripped):
                    in_assistant = False
                    if current.tool_calls or current.text_content:
                        frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    continue

                if TOOL_RESULT_RE.search(stripped):
                    in_assistant = False
                    if current.tool_calls or current.text_content:
                        frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    continue

                current.text_content += stripped + "\n"

        finally:
            elapsed = time.time() - t0
            if killed:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            else:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            stderr_thread.join(timeout=2)

        if in_assistant and (current.text_content or current.tool_calls):
            frames.append(current)

        return frames, elapsed, killed, raw_stdout_lines, stderr_lines

    def _build_command(self) -> list[str]:
        cmd = [
            OPENCODE_BIN,
            "run",
            "--format",
            "json",
            "--auto",
            "--print-logs",
            "--log-level",
            "ERROR",
            "--agent",
            self._agent,
            "--dir",
            self._project_dir,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        return cmd

    # ------------------------------------------------------------------
    # parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tool_call(line: str) -> ToolCallSnapshot:
        tc = ToolCallSnapshot(name="unknown", timestamp=time.time())
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                return tc
            part = data.get("part", {}) if isinstance(data.get("part"), dict) else {}
            state = part.get("state", {}) if isinstance(part, dict) else {}
            tc.name = str(data.get("tool") or part.get("tool") or data.get("name") or "unknown")
            raw_input = data.get("input") or state.get("input") or part.get("input")
            tc.args = raw_input if isinstance(raw_input, dict) else {}
        else:
            m = TASK_TOOL_NAME_RE.search(line)
            if m:
                tc.name = m.group(1) or "dispatch"
            else:
                tc.name = "unknown"
        return tc

    @staticmethod
    def _extract_text(line: str) -> str:
        if not line.startswith("{"):
            return ""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return ""
        part = data.get("part", {}) if isinstance(data.get("part"), dict) else {}
        return str(part.get("text", data.get("text", "")))

    # ------------------------------------------------------------------
    # analysis
    # ------------------------------------------------------------------

    def _analyze(self, frames: list[ResponseFrame], elapsed: float, killed: bool) -> SpawnResult:
        dispatch_waves: list[dict[str, object]] = []
        text_only_stops: list[dict[str, object]] = []
        total_tool_calls = 0
        total_dispatch = 0

        for f in frames:
            total_tool_calls += f.tool_call_count
            total_dispatch += f.dispatch_count

            dispatch_waves.append(
                {
                    "sequence": f.sequence,
                    "timestamp": f.timestamp,
                    "dispatch_count": f.dispatch_count,
                }
            )

            if f.is_text_only:
                text_only_stops.append(
                    {
                        "sequence": f.sequence,
                        "timestamp": f.timestamp,
                        "text_preview": f.text_content[:200],
                    }
                )

        if total_dispatch >= 2 and killed:
            verdict = "PASS"
        elif total_dispatch == 0:
            verdict = "ERROR"
        else:
            verdict = "FAIL"

        return SpawnResult(
            verdict=verdict,
            dispatch_waves=dispatch_waves,
            text_only_stops=text_only_stops,
            responses=self._serialize_frames(frames),
            total_tool_calls=total_tool_calls,
            total_dispatch_calls=total_dispatch,
            total_messages=len(frames),
            elapsed_sec=elapsed,
            killed=killed,
            log_path=self._log_path,
        )

    @staticmethod
    def _serialize_frames(frames: list[ResponseFrame]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for f in frames:
            out.append(
                {
                    "sequence": f.sequence,
                    "tool_call_count": f.tool_call_count,
                    "dispatch_count": f.dispatch_count,
                    "is_text_only": f.is_text_only,
                    "text_truncated": f.text_content[:200],
                }
            )
        return out

    # ------------------------------------------------------------------
    # structured log
    # ------------------------------------------------------------------

    def _write_structured_log(self, result: SpawnResult, frames: list[ResponseFrame]) -> None:
        if not self._log_path:
            return
        with open(self._log_path, "w") as fh:
            header = {
                "type": "spawn_meta",
                "verdict": result.verdict,
                "project_dir": self._project_dir,
                "prompt": self._prompt[:500],
                "timeout_sec": self._timeout_sec,
                "elapsed_sec": result.elapsed_sec,
                "killed": result.killed,
                "total_messages": result.total_messages,
                "total_tool_calls": result.total_tool_calls,
                "total_dispatch_calls": result.total_dispatch_calls,
                "text_only_stop_count": len(result.text_only_stops),
            }
            fh.write(json.dumps(header) + "\n")
            for frame in frames:
                record = {
                    "type": "response_frame",
                    "sequence": frame.sequence,
                    "dispatch_count": frame.dispatch_count,
                    "tool_call_count": frame.tool_call_count,
                    "is_text_only": frame.is_text_only,
                }
                fh.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------
    # raw log (write all captured data for debugging)
    # ------------------------------------------------------------------

    def _write_raw_log(self, raw_stdout_lines: list[str], stderr_lines: list[str]) -> None:
        raw_path = self._log_path.replace(".ndjson", ".raw.log") if self._log_path else ""
        if not raw_path:
            return
        with open(raw_path, "w") as fh:
            fh.write("=== RAW STDOUT ===\n")
            for line in raw_stdout_lines:
                fh.write(line + "\n")
            fh.write("\n=== RAW STDERR ===\n")
            for line in stderr_lines:
                fh.write(line + "\n")
