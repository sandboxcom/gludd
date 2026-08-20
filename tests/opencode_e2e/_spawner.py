"""Spawn opencode as a subprocess, monitor its output, and collect metrics.

Usage::

    from tests.opencode_e2e._spawner import OpencodeSpawner, SpawnResult

    spawner = OpencodeSpawner(
        project_dir="/tmp/opencode-e2e-test/",
        prompt="Read TASKS.md, dispatch 10 subagents to complete tasks.",
        timeout_sec=3600,
        prompt_sequence=["Read TASKS.md and dispatch 10 subagents.",
                         "Keep working. Complete all remaining tasks."],
        prompt_interval_sec=300,
    )
    result = spawner.run()
    print(result.verdict)          # "PASS" or "FAIL"
    print(result.dispatch_waves)   # list[dict]
    print(result.per_wave_violations)  # waves with <10 dispatches
    print(result.depth_count)      # max nesting depth observed
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import IO

OPENCODE_BIN = "opencode"
_CAPTURE_POLL_SEC = 0.05
_TERMINATE_GRACE_SEC = 1.0
_KILL_GRACE_SEC = 1.0

TOOL_CALL_RE = re.compile(r'(?:\btool_use\b|"type"\s*:\s*"tool_use"|"type":"tool_use")')
TOOL_RESULT_RE = re.compile(r'(?:tool_result|"type"\s*:\s*"tool_result")')
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
DISPATCH_TEXT_RE = re.compile(r"(?:\btask\s*\(|\bagent\s*\(|\bworkflow\s*\()")
TEXT_ASSISTANT_RE = re.compile(r"^\s*(?:assistant|Assistant|ASSISTANT)\s*[>:]")
TEXT_EVENT_RE = re.compile(r'(?:"type"\s*:\s*"text"|"type":"text")')
NESTED_DISPATCH_RE = re.compile(
    r'(?:tool_use.*"(?:task|agent|workflow)")'
    r'|(?:"type":"tool_use".*"(?:task|agent|workflow)")'
)


@dataclass
class ToolCallSnapshot:
    name: str
    timestamp: float = 0.0
    args: dict[str, object] = field(default_factory=dict)
    tool_use_id: str = ""
    is_error: bool = False


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
    verdict_reason: str = ""
    dispatch_waves: list[dict[str, object]] = field(default_factory=list)
    per_wave_violations: list[dict[str, object]] = field(default_factory=list)
    text_only_stops: list[dict[str, object]] = field(default_factory=list)
    responses: list[dict[str, object]] = field(default_factory=list)
    total_tool_calls: int = 0
    total_dispatch_calls: int = 0
    total_messages: int = 0
    elapsed_sec: float = 0.0
    killed: bool = False
    log_path: str = ""
    progress_log: str = ""
    depth_count: int = 0
    prompts_sent: int = 0


class OpencodeSpawner:
    """Spawn opencode run with --format json, parse NDJSON output, detect stop bugs."""

    def __init__(
        self,
        project_dir: str,
        prompt: str,
        *,
        timeout_sec: float = 120,
        log_dir: str = "/tmp/gludd-opencode-e2e",
        agent: str = "build",
        model: str | None = None,
        env: dict[str, str] | None = None,
        prompt_sequence: list[str] | None = None,
        prompt_interval_sec: int = 300,
        progress_interval_sec: float = 30,
    ) -> None:
        self._project_dir = os.path.abspath(project_dir)
        self._prompt = prompt
        self._prompt_sequence = prompt_sequence or []
        self._prompt_interval_sec = prompt_interval_sec
        self._progress_interval_sec = progress_interval_sec
        self._timeout_sec = timeout_sec
        self._agent = agent
        self._model = model
        self._env = dict(os.environ)
        if env:
            self._env.update(env)
        self._env.setdefault("OPENCODE_SUBAGENT", "0")
        self._env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
        self._env.setdefault("GLUDD_ENHANCEMENT_RATIO_BLOCK", "0")
        self._env.setdefault("GLUDD_CLEAN_TREE_ENFORCE", "0")
        self._env.setdefault("GLUDD_TDD_ENFORCE", "0")
        self._env.setdefault("GLUDD_TASK_DEADLINE_BLOCK", "0")
        self._env.setdefault("GLUDD_MAKE_ENFORCE", "0")
        self._env.setdefault("GLUDD_VERIFIED_CLAIMS_ENFORCE", "0")
        self._env.setdefault("GLUDD_MODEL_UTIL_ENFORCE", "0")
        self._env.setdefault("OPENCODE_DISABLE_CLAUDE_CODE", "0")
        raw_namespace = self._env.get("GLUDD_PROJECT_NAMESPACE") or os.path.basename(self._project_dir)
        self._namespace = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_namespace).strip("-")[:64] or "gludd"
        os.makedirs(log_dir, exist_ok=True)
        self._log_dir = log_dir
        self._log_path = ""
        self._progress_log_path = ""
        self._prompts_sent = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self) -> SpawnResult:
        run_id = f"{self._namespace}-{os.getpid()}-{time.time_ns()}"
        self._log_path = os.path.join(self._log_dir, f"opencode-e2e-{run_id}.ndjson")
        self._progress_log_path = os.path.join(self._log_dir, f"opencode-e2e-{run_id}.progress.log")
        frames, elapsed, killed, raw_stdout, raw_stderr, depth_count = self._spawn_and_capture()
        result = self._analyze(frames, elapsed, killed, depth_count)
        self._write_structured_log(result, frames)
        self._write_raw_log(raw_stdout, raw_stderr)
        return result

    # ------------------------------------------------------------------
    # subprocess lifecycle
    # ------------------------------------------------------------------

    def _spawn_and_capture(
        self,
    ) -> tuple[list[ResponseFrame], float, bool, list[str], list[str], int]:
        cmd = self._build_command()
        proc = subprocess.Popen(
            cmd,
            cwd=self._project_dir,
            env=self._env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=os.name == "posix",
        )

        frames: list[ResponseFrame] = []
        current = ResponseFrame(sequence=0, timestamp=time.time())
        in_assistant = False
        killed = False
        errored_tool_ids: set[str] = set()
        t0 = time.monotonic()
        stderr_lines: list[str] = []
        raw_stdout_lines: list[str] = []
        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stdout_done = False
        depth_count = 0
        nesting_stack: list[bool] = []
        progress_stop = threading.Event()

        _lock = threading.Lock()

        def _read_stdout() -> None:
            stream: IO[str] | None = proc.stdout
            try:
                if stream is None:
                    return
                for line in stream:
                    raw_stdout_lines.append(line.rstrip("\n"))
                    stdout_queue.put(line)
            finally:
                stdout_queue.put(None)

        def _read_stderr() -> None:
            stream: IO[str] | None = proc.stderr
            if stream is None:
                return
            for line in stream:
                if line:
                    stderr_lines.append(line.rstrip("\n"))

        def _write_progress() -> None:
            with open(self._progress_log_path, "w") as pfh:
                while True:
                    elapsed = time.monotonic() - t0
                    with _lock:
                        wave_count = len(frames)
                        total_disp = sum(f.dispatch_count for f in frames)
                    pfh.write(
                        f"PROGRESS {time.strftime('%H:%M:%S')}  "
                        f"elapsed={elapsed:.0f}s  waves={wave_count}  "
                        f"dispatches={total_disp}  prompts_sent={self._prompts_sent}\n"
                    )
                    pfh.flush()
                    if progress_stop.wait(self._progress_interval_sec):
                        break

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        progress_thread = threading.Thread(target=_write_progress, daemon=True)
        progress_thread.start()

        try:
            while True:
                process_active = proc.poll() is None or self._process_group_running(proc)
                if not killed and process_active and time.monotonic() - t0 >= self._timeout_sec:
                    killed = True
                    self._terminate_process_group(proc)
                try:
                    line = stdout_queue.get(timeout=_CAPTURE_POLL_SEC)
                except queue.Empty:
                    if proc.poll() is not None and stdout_done:
                        break
                    continue
                if line is None:
                    stdout_done = True
                    if proc.poll() is not None and stdout_queue.empty():
                        break
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                current.raw_lines.append(stripped)

                _is_json = stripped.startswith("{") and '"type"' in stripped

                if _is_json and STEP_START_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        with _lock:
                            frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    in_assistant = True
                    current.is_text_only = True
                    nesting_stack.append(False)
                    continue

                if _is_json and MESSAGE_START_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        with _lock:
                            frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    in_assistant = bool(MESSAGE_ROLE_RE.search(stripped))
                    continue

                if TEXT_ASSISTANT_RE.search(stripped):
                    if in_assistant and (current.text_content or current.tool_calls):
                        with _lock:
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
                        if nesting_stack:
                            nesting_stack[-1] = True
                    continue

                if DISPATCH_TEXT_RE.search(stripped):
                    current.dispatch_count += 1
                    current.tool_call_count += 1
                    current.is_text_only = False
                    tc = ToolCallSnapshot(name="dispatch", timestamp=time.time())
                    current.tool_calls.append(tc)
                    if nesting_stack:
                        nesting_stack[-1] = True
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
                        self._correct_dispatch_count(current, errored_tool_ids)
                        with _lock:
                            frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    if nesting_stack and nesting_stack.pop():
                        current_depth = len(nesting_stack) + 1
                        if current_depth > depth_count:
                            depth_count = current_depth
                    continue

                if TOOL_RESULT_RE.search(stripped):
                    tool_id, is_err = self._extract_tool_result(stripped)
                    if is_err and tool_id:
                        errored_tool_ids.add(tool_id)
                    in_assistant = False
                    if current.tool_calls or current.text_content:
                        self._correct_dispatch_count(current, errored_tool_ids)
                        with _lock:
                            frames.append(current)
                        current = ResponseFrame(sequence=len(frames), timestamp=time.time())
                    if nesting_stack:
                        nesting_stack.append(False)
                    continue

                current.text_content += stripped + "\n"

        finally:
            progress_stop.set()
            if proc.poll() is None or self._process_group_running(proc):
                self._terminate_process_group(proc)
            else:
                proc.wait()
            stdout_thread.join(timeout=_KILL_GRACE_SEC)
            stderr_thread.join(timeout=_KILL_GRACE_SEC)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            stdout_thread.join(timeout=_CAPTURE_POLL_SEC)
            stderr_thread.join(timeout=_CAPTURE_POLL_SEC)
            progress_thread.join(timeout=_KILL_GRACE_SEC)
            elapsed = time.monotonic() - t0

        if in_assistant and (current.text_content or current.tool_calls):
            self._correct_dispatch_count(current, errored_tool_ids)
            with _lock:
                frames.append(current)

        return frames, elapsed, killed, raw_stdout_lines, stderr_lines, depth_count

    @staticmethod
    def _process_group_running(proc: subprocess.Popen[str]) -> bool:
        if os.name != "posix":
            return proc.poll() is None
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _signal_process_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            return

    @classmethod
    def _terminate_process_group(cls, proc: subprocess.Popen[str]) -> None:
        """Bound TERM→KILL for the isolated child process group and reap its leader."""
        if proc.poll() is not None and not cls._process_group_running(proc):
            proc.wait()
            return

        cls._signal_process_group(proc, signal.SIGTERM)
        deadline = time.monotonic() + _TERMINATE_GRACE_SEC
        while time.monotonic() < deadline:
            if proc.poll() is not None and not cls._process_group_running(proc):
                break
            time.sleep(_CAPTURE_POLL_SEC)

        if proc.poll() is None or cls._process_group_running(proc):
            cls._signal_process_group(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=_KILL_GRACE_SEC)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"failed to reap timed-out process group {proc.pid}") from exc

    def _build_command(self) -> list[str]:
        cmd = [
            OPENCODE_BIN,
            "run",
            "--format",
            "json",
            "--auto",
            "--dir",
            self._project_dir,
            "--print-logs",
            "--log-level",
            "ERROR",
            "--agent",
            self._agent,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        cmd.append(self._prompt)
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
            tc.name = str(data.get("tool") or part.get("tool") or state.get("tool") or data.get("name") or "unknown")
            tc.tool_use_id = str(
                data.get("id")
                or part.get("id")
                or state.get("id")
                or data.get("tool_use_id")
                or part.get("tool_use_id")
                or state.get("tool_use_id")
                or ""
            )
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

    @staticmethod
    def _extract_tool_result(line: str) -> tuple[str, bool]:
        if not line.startswith("{"):
            return "", False
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return "", False
        part = data.get("part", {}) if isinstance(data.get("part"), dict) else {}
        state = part.get("state", {}) if isinstance(part, dict) else {}
        result = part.get("result") or state.get("result") or data.get("result")
        tool_use_id = str(
            data.get("tool_use_id")
            or part.get("tool_use_id")
            or state.get("tool_use_id")
            or (result.get("tool_use_id") if isinstance(result, dict) else "")
            or ""
        )
        is_error = bool(
            data.get("is_error")
            or part.get("is_error")
            or state.get("is_error")
            or (result.get("is_error") if isinstance(result, dict) else False)
        )
        return tool_use_id, is_error

    @staticmethod
    def _correct_dispatch_count(frame: ResponseFrame, errored_ids: set[str]) -> None:
        for tc in frame.tool_calls:
            if tc.tool_use_id and tc.tool_use_id in errored_ids:
                tc.is_error = True
                if frame.dispatch_count > 0:
                    frame.dispatch_count -= 1

    # ------------------------------------------------------------------
    # analysis
    # ------------------------------------------------------------------

    def _analyze(
        self,
        frames: list[ResponseFrame],
        elapsed: float,
        killed: bool,
        depth_count: int,
    ) -> SpawnResult:
        dispatch_waves: list[dict[str, object]] = []
        per_wave_violations: list[dict[str, object]] = []
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

            if 0 < f.dispatch_count < 10:
                per_wave_violations.append(
                    {
                        "sequence": f.sequence,
                        "timestamp": f.timestamp,
                        "dispatch_count": f.dispatch_count,
                        "reason": f"Wave {f.sequence} had {f.dispatch_count} dispatches (floor=10)",
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

        non_zero_waves = [w for w in dispatch_waves if isinstance(w["dispatch_count"], int) and w["dispatch_count"] > 0]
        num_waves = len(non_zero_waves)

        if total_dispatch == 0 and not killed:
            verdict = "FAIL"
            reason = "No dispatches and not killed by timeout"
        elif total_dispatch == 0 and killed:
            verdict = "FAIL"
            reason = "Killed by timeout with 0 dispatches"
        elif killed and total_dispatch > 0:
            verdict = "PASS"
            reason = f"Still dispatching when killed: {total_dispatch} dispatches across {num_waves} waves"
        elif not killed and num_waves >= 1:
            verdict = "PASS"
            reason = f"Completed naturally with {total_dispatch} dispatches across {num_waves} waves"
        else:
            verdict = "FAIL"
            reason = f"Stopped early: {num_waves} waves, {total_dispatch} dispatches"

        if depth_count < 2:
            reason += f" (depth advisory: max={depth_count}, 3x depth not tested)"

        return SpawnResult(
            verdict=verdict,
            verdict_reason=reason,
            dispatch_waves=dispatch_waves,
            per_wave_violations=per_wave_violations,
            text_only_stops=text_only_stops,
            responses=self._serialize_frames(frames),
            total_tool_calls=total_tool_calls,
            total_dispatch_calls=total_dispatch,
            total_messages=len(frames),
            elapsed_sec=elapsed,
            killed=killed,
            log_path=self._log_path,
            progress_log=self._progress_log_path,
            depth_count=depth_count,
            prompts_sent=self._prompts_sent,
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
                "verdict_reason": result.verdict_reason,
                "project_dir": self._project_dir,
                "prompt": self._prompt[:500],
                "timeout_sec": self._timeout_sec,
                "elapsed_sec": result.elapsed_sec,
                "killed": result.killed,
                "depth_count": result.depth_count,
                "prompts_sent": result.prompts_sent,
                "wave_count": len(result.dispatch_waves),
                "per_wave_violation_count": len(result.per_wave_violations),
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
