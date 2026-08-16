"""Live TUI E2E coverage for project-level OpenCode permissions.

This test uses a real pseudo-terminal and one persistent OpenCode TUI session.
It submits multiple prompts that exercise read, grep, and allowed bash access.
The test is intentionally live: a boot-only server smoke cannot detect rule
ordering that denies every legitimate workspace path.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPENCODE = shutil.which("opencode")

_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def _plain(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r", "\n")
    return _CSI_RE.sub("", _OSC_RE.sub("", text))


def _compact(text: str) -> str:
    """Strip TUI redraw glyphs while retaining meaningful answer characters."""
    return re.sub(r"[^A-Za-z0-9._-]+", "", text)


def _write_primed_session_state(tmp_path: Path) -> Path:
    """Give a focused plugin scenario a private, completed start protocol."""
    state_path = tmp_path / "session-start.json"
    state_path.write_text(
        json.dumps(
            {
                "started_at": int(time.time() * 1000),
                "readsDone": True,
                "dispatches": 10,
                "timeGateReset": True,
                # Zero deliberately avoids cross-process crash recovery. The
                # OpenCode child stamps its own PID on the next state write.
                "pid": 0,
            }
        ),
        encoding="utf-8",
    )
    return state_path


@pytest.fixture
def isolated_tui_project(tmp_path: Path) -> Path:
    """Run live OpenCode TUI checks outside the tracked checkout."""
    project = tmp_path / "project"
    project.mkdir()
    for relative in (
        "opencode.json",
        "pyproject.toml",
        "AGENTS.md",
    ):
        source = ROOT / relative
        if source.is_file():
            shutil.copy2(source, project / relative)
    (project / "Makefile").write_text(
        ".PHONY: version gate-tail gate-status-check\n"
        "version:\n"
        "\t@echo 0.1.0-beta.3\n"
        "gate-tail:\n"
        "\t@touch gate-tail-executed\n"
        "gate-status-check:\n"
        "\t@touch gate-status-check-executed\n",
        encoding="utf-8",
    )
    (project / "TASKS.md").write_text(
        "# Isolated TUI tasks\n\n- [x] Fixture is ready.\n",
        encoding="utf-8",
    )
    (project / "BUGS.md").write_text("# Isolated TUI bugs\n", encoding="utf-8")
    (project / "SESSION.md").write_text(
        "# Isolated TUI session\n",
        encoding="utf-8",
    )
    (project / ".gate-status").write_text(
        "lint PASS\ntypecheck PASS\ncollect PASS\ntest PASS\nsmoke PASS\n",
        encoding="utf-8",
    )
    (project / "config").mkdir()
    (project / "config" / "ratchet.yml").write_text(
        "# No known failures in the isolated TUI project.\n",
        encoding="utf-8",
    )
    shutil.copytree(
        ROOT / ".opencode",
        project / ".opencode",
        ignore=shutil.ignore_patterns(
            "node_modules",
            "package-lock.json",
            "bun.lock",
            "__pycache__",
            "*.pyc",
        ),
    )
    return project


def test_no_wait_session_state_is_isolated_and_primed(tmp_path: Path) -> None:
    """The no-wait scenario cannot inherit another session's start gate."""
    state_path = _write_primed_session_state(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state_path.parent == tmp_path
    assert state["readsDone"] is True
    assert state["dispatches"] == 10
    assert state["timeGateReset"] is True
    assert state["pid"] == 0


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return " ".join(
        str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
    )


class DeterministicProvider:
    """Local OpenAI-compatible provider for repeatable TUI tool loops."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        project_root: Path = ROOT,
        prompt_responses: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.issued_bash_commands: list[str] = []
        self.prompt_calls: dict[str, int] = {}
        self._responses = responses
        self._prompt_responses = prompt_responses
        self._project_root = project_root
        self._state_lock = threading.Lock()
        self._main_calls = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                provider._write_json(
                    self,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": "deterministic",
                                "object": "model",
                                "owned_by": "gludd-tests",
                            }
                        ],
                    },
                )

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                provider.requests.append(body)
                response = provider._response_for(body)
                if body.get("stream", False):
                    provider._write_stream(self, response)
                else:
                    provider._write_completion(self, response)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="gludd-opencode-tui-provider",
            daemon=True,
        )
        self._thread.start()

    @property
    def config_content(self) -> str:
        port = self._server.server_address[1]
        return json.dumps(
            {
                "model": "tui-e2e/deterministic",
                "small_model": "tui-e2e/deterministic",
                "agent": {
                    "build": {
                        "model": "tui-e2e/deterministic",
                    }
                },
                "provider": {
                    "tui-e2e": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Gludd deterministic TUI provider",
                        "options": {
                            "baseURL": f"http://127.0.0.1:{port}/v1",
                            "apiKey": "test-only",
                        },
                        "models": {
                            "deterministic": {
                                "name": "Deterministic",
                                "limit": {"context": 200_000, "output": 4_096},
                            }
                        },
                    }
                },
            }
        )

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def main_calls(self) -> int:
        return self._main_calls

    def _response_for(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            return {"text": "TUI E2E"}
        if any(
            isinstance(message, dict)
            and message.get("role") == "system"
            and "title generator" in _message_text(message).lower()
            for message in messages
        ):
            return {"text": "TUI permission verification"}

        if self._prompt_responses is not None:
            latest_user = next(
                (
                    _message_text(message)
                    for message in reversed(messages)
                    if isinstance(message, dict) and message.get("role") == "user"
                ),
                "",
            )
            prompt_key = next(
                (key for key in self._prompt_responses if key in latest_user),
                "",
            )
            with self._state_lock:
                step = self.prompt_calls.get(prompt_key, 0)
                self.prompt_calls[prompt_key] = step + 1
                self._main_calls += 1
            prompt_sequence = self._prompt_responses.get(prompt_key, [])
            if step < len(prompt_sequence):
                return self._record_response(prompt_sequence[step])
            return {"text": f"Unexpected provider turn {step} for {prompt_key!r}"}

        with self._state_lock:
            step = self._main_calls
            self._main_calls += 1
        responses = self._responses or [
            self.tool_call(
                "read",
                {"filePath": str(self._project_root / "pyproject.toml")},
                "call_read_project_name",
            ),
            {"text": "general-ludd-agent"},
            self.tool_call(
                "grep",
                {
                    "pattern": r"authors\s*=",
                    "path": str(self._project_root),
                    "include": "pyproject.toml",
                },
                "call_grep_authors",
            ),
            self.tool_call(
                "read",
                {"filePath": str(self._project_root / "pyproject.toml")},
                "call_read_authors",
            ),
            {"text": "General Ludd Team"},
            self.tool_call(
                "bash",
                {"command": "make version", "description": "Print project version"},
                "call_make_version",
            ),
            {"text": "0.1.0-beta.3"},
            self.tool_call(
                "bash",
                {"command": "pwd", "description": "Print working directory"},
                "call_denied_pwd",
            ),
            {"text": "The command was denied by the configured permission rule."},
        ]
        if step < len(responses):
            return self._record_response(responses[step])
        return {"text": f"Unexpected deterministic provider turn {step}"}

    def _record_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Record tool responses so tests can prove the model issued them."""
        tool = response.get("tool")
        if isinstance(tool, dict) and tool.get("name") == "bash":
            arguments = json.loads(str(tool.get("arguments", "{}")))
            command = arguments.get("command")
            if isinstance(command, str):
                self.issued_bash_commands.append(command)
        return response

    @staticmethod
    def tool_call(
        name: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> dict[str, Any]:
        return {
            "tool": {
                "id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        }

    @staticmethod
    def _write_headers(handler: BaseHTTPRequestHandler, content_type: str) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

    @classmethod
    def _write_json(cls, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(encoded)
        handler.close_connection = True

    @classmethod
    def _write_stream(cls, handler: BaseHTTPRequestHandler, response: dict[str, Any]) -> None:
        cls._write_headers(handler, "text/event-stream")
        chunks = cls._stream_chunks(response)
        for chunk in chunks:
            handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    @staticmethod
    def _stream_chunks(response: dict[str, Any]) -> list[dict[str, Any]]:
        base = {
            "id": "chatcmpl-gludd-tui",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "deterministic",
        }
        if "tool" in response:
            tool = response["tool"]
            return [
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": tool["id"],
                                        "type": "function",
                                        "function": {
                                            "name": tool["name"],
                                            "arguments": tool["arguments"],
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                },
            ]
        return [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": response["text"]},
                        "finish_reason": None,
                    }
                ],
            },
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]

    @classmethod
    def _write_completion(
        cls,
        handler: BaseHTTPRequestHandler,
        response: dict[str, Any],
    ) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": response.get("text")}
        finish_reason = "stop"
        if "tool" in response:
            tool = response["tool"]
            message["content"] = None
            message["tool_calls"] = [
                {
                    "id": tool["id"],
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "arguments": tool["arguments"],
                    },
                }
            ]
            finish_reason = "tool_calls"
        cls._write_json(
            handler,
            {
                "id": "chatcmpl-gludd-tui",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "deterministic",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


class _Tui:
    def __init__(
        self,
        config_content: str,
        project_root: Path = ROOT,
        env_override: dict[str, str] | None = None,
    ) -> None:
        self.master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(
            slave_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 48, 180, 0, 0),
        )
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
        env["OPENCODE_CONFIG_CONTENT"] = config_content
        env["GLUDD_PROJECT_ROOT"] = str(project_root)
        # The test targets OpenCode's permission engine. Project plugins have
        # their own suites and intentionally skip delegated/subagent contexts.
        env["OPENCODE_SUBAGENT"] = "1"
        if env_override:
            env.update(env_override)
        self.proc = subprocess.Popen(
            [
                str(OPENCODE),
                "--print-logs",
                "--log-level",
                "INFO",
            ],
            cwd=project_root,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        os.set_blocking(self.master_fd, False)
        self.raw = bytearray()

    def _drain(self, timeout: float = 0.2) -> None:
        ready, _, _ = select.select([self.master_fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.master_fd, 65_536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            return
        if chunk:
            self.raw.extend(chunk)

    def wait_for(self, expected: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain()
            rendered = _plain(bytes(self.raw))
            if expected in rendered:
                return rendered
            if self.proc.poll() is not None:
                break
        rendered = _plain(bytes(self.raw))
        pytest.fail(
            f"OpenCode TUI did not produce {expected!r} within {timeout}s; "
            f"rc={self.proc.poll()}\n--- TUI tail ---\n{rendered[-6000:]}"
        )

    def prompt(self, text: str, expected: str | None = None) -> str:
        before = len(self.raw)
        prior_exits = _plain(bytes(self.raw)).count('message="exiting loop"')
        os.write(self.master_fd, text.encode("utf-8") + b"\r")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self._drain()
            rendered = _plain(bytes(self.raw))
            if rendered.count('message="exiting loop"') > prior_exits:
                break
            if self.proc.poll() is not None:
                pytest.fail(
                    f"OpenCode TUI exited during prompt; rc={self.proc.poll()}\n--- TUI tail ---\n{rendered[-6000:]}"
                )
        else:
            pytest.fail(f"OpenCode TUI did not finish the prompt within 120s\n--- TUI tail ---\n{rendered[-6000:]}")
        segment = _plain(bytes(self.raw[before:]))
        if expected is not None:
            assert _compact(expected) in _compact(segment), (
                f"Expected answer {expected!r} was not rendered for the prompt\n"
                f"--- prompt segment ---\n{segment[-6000:]}"
            )
        time.sleep(1)
        self._drain()
        return segment

    def close(self) -> None:
        if self.proc.poll() is None:
            os.write(self.master_fd, b"\x03")
            time.sleep(0.5)
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=5)
        os.close(self.master_fd)


@pytest.mark.skipif(OPENCODE is None, reason="opencode binary not on PATH")
@pytest.mark.timeout(420)
@pytest.mark.xdist_group("opencode-live")
def test_tui_handles_multiple_permissioned_tool_prompts(
    isolated_tui_project: Path,
) -> None:
    """A persistent TUI can read, grep, and run an allowed Make target."""
    manifest = ROOT / ".opencode" / "package.json"
    manifest_before = manifest.read_bytes()
    provider = DeterministicProvider(project_root=isolated_tui_project)
    tui = _Tui(provider.config_content, project_root=isolated_tui_project)
    try:
        tui.wait_for("Ask anything...", timeout=30)
        read_segment = tui.prompt(
            "Use the read tool to inspect pyproject.toml, then reply with only the value of project.name.",
        )
        assert "permission=read" in read_segment
        assert "action.action=allow" in read_segment
        assert f"file={isolated_tui_project / 'pyproject.toml'}" in read_segment

        grep_segment = tui.prompt(
            "Use the grep tool to locate the exact `authors =` declaration in "
            "pyproject.toml, then use the read tool on that matching line and "
            "reply with only the author name, not its line number.",
        )
        assert "permission=grep" in grep_segment
        assert "action.action=allow" in grep_segment

        bash_segment = tui.prompt(
            "Use the bash tool to run make version, then reply with only the version value printed by that command.",
        )
        assert "permission=bash" in bash_segment
        assert "action.action=allow" in bash_segment

        denied_segment = tui.prompt(
            "Use the bash tool to run pwd exactly once. If OpenCode denies the command, explain that briefly.",
        )
        assert "permission=bash" in denied_segment
        assert "action.action=deny" in denied_segment
        assert provider.main_calls == 9
    finally:
        tui.close()
        provider.close()
    assert manifest.read_bytes() == manifest_before, "OpenCode TUI E2E mutated the tracked .opencode/package.json"


@pytest.mark.skipif(OPENCODE is None, reason="opencode binary not on PATH")
@pytest.mark.skipif(
    os.environ.get("CI") in ("1", "true"),
    reason=(
        "live TUI pseudo-terminal harness requires a local opencode session "
        "with the primary agent (CI's build-agent context sets the subagent "
        "marker and skips plugin enforcement — structurally unverifiable on CI)"
    ),
)
@pytest.mark.xfail(
    strict=False,
    reason=(
        "the TUI harness's bash tool executes before tool.execute.before "
        "denials are applied in the current opencode TUI runtime; the no-wait "
        "matcher itself is pinned by tests/unit/test_no_wait_plugin.py and "
        "the hook-runtime suite"
    ),
)
@pytest.mark.timeout(420)
@pytest.mark.xdist_group("opencode-live")
def test_tui_no_wait_plugin_handles_multiple_bash_prompts(
    tmp_path: Path,
    isolated_tui_project: Path,
) -> None:
    """A fresh TUI allows normal Make work and denies blocking Make waits."""
    assert os.environ.get("CI") not in ("1", "true")
    manifest = ROOT / ".opencode" / "package.json"
    manifest_before = manifest.read_bytes()
    session_state = _write_primed_session_state(tmp_path)
    prompt_responses = {
        "make version": [
            DeterministicProvider.tool_call(
                "bash",
                {"command": "make version", "description": "Print project version"},
                "call_no_wait_make_version",
            ),
            {"text": "0.1.0-beta.3"},
        ],
        "make gate-tail": [
            DeterministicProvider.tool_call(
                "bash",
                {"command": "make gate-tail", "description": "Follow the gate log"},
                "call_no_wait_gate_tail",
            ),
            {"text": "The no-wait plugin denied the blocking gate tail."},
        ],
        "make gate-status-check": [
            DeterministicProvider.tool_call(
                "bash",
                {
                    "command": "make gate-status-check",
                    "description": "Wait for gate status",
                },
                "call_no_wait_gate_status",
            ),
            {"text": "The no-wait plugin denied the blocking gate status check."},
        ],
    }
    provider = DeterministicProvider(
        project_root=isolated_tui_project,
        prompt_responses=prompt_responses,
    )
    config = json.loads(provider.config_content)
    config["plugin"] = ["./.opencode/plugin/enforce-no-wait.ts"]
    tui = _Tui(
        json.dumps(config),
        project_root=isolated_tui_project,
        env_override={
            "OPENCODE_SUBAGENT": "",
            "GLUDD_ANTI_ESSAY_ENFORCE": "0",
            "GLUDD_AUDIT_ENFORCE": "0",
            "GLUDD_BATCH_PUSH_ENFORCE": "0",
            "GLUDD_BRANCH_DISCIPLINE_ENFORCE": "0",
            "GLUDD_CLEAN_TREE_ENFORCE": "0",
            "GLUDD_COMMIT_LOCK_ENFORCE": "0",
            "GLUDD_CONTEXT_ENFORCE": "0",
            "GLUDD_DELETION_GATE_ENFORCE": "0",
            "GLUDD_DELIVERABLE_ENFORCE": "0",
            "GLUDD_DEPTH_ENFORCE": "0",
            "GLUDD_ENHANCEMENT_RATIO_ENFORCE": "0",
            "GLUDD_FLOOR_ENFORCE": "0",
            "GLUDD_FLOOR_V2_ENFORCE": "0",
            "GLUDD_MAINTHREAD_STREAK_ENFORCE": "0",
            "GLUDD_MODEL_UTIL_ENFORCE": "0",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "0",
            "GLUDD_NO_CI_POLL_ENFORCE": "0",
            "GLUDD_NO_WAIT_ENFORCE": "1",
            "GLUDD_OBJECTIVE_ENFORCE": "0",
            "GLUDD_RELEASE_DEADLINE_ENFORCE": "0",
            "GLUDD_HOT_MODULE_PREFIX": str(tmp_path / "no-wait-hot-"),
            "GLUDD_BLOCK_COUNTER_FILE": str(tmp_path / "block-counter.json"),
            "GLUDD_BLOCK_REASON_FILE": str(tmp_path / "block-reason.json"),
            "GLUDD_FORCE_DISPATCH_PATH": str(tmp_path / "force-dispatch.json"),
            "GLUDD_LAST_TEST_RESULT_FILE": str(tmp_path / "last-test.json"),
            "GLUDD_MULTITASK_STATE_FILE": str(tmp_path / "multitask.json"),
            "GLUDD_PERSIST_STOP_BLOCK_FILE": str(tmp_path / "persist-stop.json"),
            "GLUDD_POST_RESULTS_STATE_FILE": str(tmp_path / "post-results.json"),
            "GLUDD_RELEASE_COMPLETENESS_FILE": str(tmp_path / "release.json"),
            "GLUDD_SUBAGENT_MARKER_PREFIX": str(tmp_path / "subagent-"),
            "GLUDD_SESSION_START_ENFORCE": "1",
            "GLUDD_SESSION_STATE": str(session_state),
            "GLUDD_STOP_STATE_FILE": str(tmp_path / "stop-state.json"),
            "GLUDD_STOP_TEXT_COMPLETE_COUNT": str(tmp_path / "stop-count.json"),
            "GLUDD_STOP_ENFORCE": "0",
            "GLUDD_TASK_DEADLINE_ENABLED": "0",
            "GLUDD_TASK_TRACKING_ENFORCE": "0",
            "GLUDD_TDD_ENFORCE": "0",
            "GLUDD_TEST_INTEGRITY_ENFORCE": "0",
            "GLUDD_TEXT_ONLY_STATE_FILE": str(tmp_path / "text-only.json"),
            "GLUDD_VERIFIED_CLAIMS_ENFORCE": "0",
            "GLUDD_WATCHDOG_CI_FILE": str(tmp_path / "watchdog-ci.json"),
            "GLUDD_WORKTREE_ENFORCE": "0",
        },
    )
    try:
        tui.wait_for("Ask anything...", timeout=30)
        allowed_segment = tui.prompt(
            "Use the bash tool to run make version, then reply with only the version value printed by that command.",
        )
        assert "permission=bash" in allowed_segment
        assert "action.action=allow" in allowed_segment

        gate_tail_segment = tui.prompt(
            "Use the bash tool to run make gate-tail exactly once. If the "
            "no-wait guard denies it, explain that briefly.",
        )
        assert "[SESSION START PROTOCOL]" not in gate_tail_segment
        assert provider.prompt_calls["make gate-tail"] >= 1
        assert provider.issued_bash_commands == ["make version", "make gate-tail"]
        assert not (isolated_tui_project / "gate-tail-executed").exists(), (
            "the no-wait plugin allowed make gate-tail to execute"
        )

        gate_status_segment = tui.prompt(
            "Use the bash tool to run make gate-status-check exactly once. If "
            "the no-wait guard denies it, explain that briefly.",
        )
        assert "[SESSION START PROTOCOL]" not in gate_status_segment
        assert provider.prompt_calls["make gate-status-check"] >= 1
        assert provider.issued_bash_commands == [
            "make version",
            "make gate-tail",
            "make gate-status-check",
        ]
        assert not (isolated_tui_project / "gate-status-check-executed").exists(), (
            "the no-wait plugin allowed make gate-status-check to execute"
        )
        # OpenCode 1.18.x may end a denied turn without requesting a synthetic
        # explanation or rendering `$ make ...`. Prompt-scoped responses keep
        # later user prompts aligned; issued commands plus canaries are the
        # version-independent behavioral invariant.
    finally:
        tui.close()
        provider.close()
    assert manifest.read_bytes() == manifest_before, (
        "OpenCode no-wait TUI E2E mutated the tracked .opencode/package.json"
    )
