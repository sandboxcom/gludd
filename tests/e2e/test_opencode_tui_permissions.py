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


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return " ".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


class DeterministicProvider:
    """Local OpenAI-compatible provider for repeatable TUI tool loops."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self._responses = responses
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

        with self._state_lock:
            step = self._main_calls
            self._main_calls += 1
        responses = self._responses or [
            self.tool_call(
                "read",
                {"filePath": str(ROOT / "pyproject.toml")},
                "call_read_project_name",
            ),
            {"text": "general-ludd-agent"},
            self.tool_call(
                "grep",
                {
                    "pattern": r"authors\s*=",
                    "path": str(ROOT),
                    "include": "pyproject.toml",
                },
                "call_grep_authors",
            ),
            self.tool_call(
                "read",
                {"filePath": str(ROOT / "pyproject.toml")},
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
            return responses[step]
        return {"text": f"Unexpected deterministic provider turn {step}"}

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
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                    ],
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
            cwd=ROOT,
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
                    f"OpenCode TUI exited during prompt; rc={self.proc.poll()}\n"
                    f"--- TUI tail ---\n{rendered[-6000:]}"
                )
        else:
            pytest.fail(
                "OpenCode TUI did not finish the prompt within 120s\n"
                f"--- TUI tail ---\n{rendered[-6000:]}"
            )
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
@pytest.mark.xdist_group("opencode-tui-permissions")
def test_tui_handles_multiple_permissioned_tool_prompts() -> None:
    """A persistent TUI can read, grep, and run an allowed Make target."""
    provider = DeterministicProvider()
    tui = _Tui(provider.config_content)
    try:
        tui.wait_for("Ask anything...", timeout=30)
        read_segment = tui.prompt(
            "Use the read tool to inspect pyproject.toml, then reply with only "
            "the value of project.name.",
        )
        assert "permission=read" in read_segment
        assert "action.action=allow" in read_segment
        assert f"file={ROOT / 'pyproject.toml'}" in read_segment

        grep_segment = tui.prompt(
            "Use the grep tool to locate the exact `authors =` declaration in "
            "pyproject.toml, then use the read tool on that matching line and "
            "reply with only the author name, not its line number.",
        )
        assert "permission=grep" in grep_segment
        assert "action.action=allow" in grep_segment

        bash_segment = tui.prompt(
            "Use the bash tool to run make version, then reply with only the "
            "version value printed by that command.",
        )
        assert "permission=bash" in bash_segment
        assert "action.action=allow" in bash_segment

        denied_segment = tui.prompt(
            "Use the bash tool to run pwd exactly once. If OpenCode denies the "
            "command, explain that briefly.",
        )
        assert "permission=bash" in denied_segment
        assert "action.action=deny" in denied_segment
        assert provider.main_calls == 9
    finally:
        tui.close()
        provider.close()


@pytest.mark.skipif(OPENCODE is None, reason="opencode binary not on PATH")
@pytest.mark.timeout(420)
@pytest.mark.xdist_group("opencode-tui-permissions")
def test_tui_no_wait_plugin_handles_multiple_bash_prompts() -> None:
    """A fresh TUI allows normal Make work and denies blocking Make waits."""
    responses = [
        DeterministicProvider.tool_call(
            "bash",
            {"command": "make version", "description": "Print project version"},
            "call_no_wait_make_version",
        ),
        {"text": "0.1.0-beta.3"},
        DeterministicProvider.tool_call(
            "bash",
            {"command": "make gate-tail", "description": "Follow the gate log"},
            "call_no_wait_gate_tail",
        ),
        {"text": "The no-wait guard denied the blocking gate tail."},
        DeterministicProvider.tool_call(
            "bash",
            {
                "command": "make gate-status-check",
                "description": "Wait for gate status",
            },
            "call_no_wait_gate_status",
        ),
        {"text": "The no-wait guard denied the blocking gate status check."},
    ]
    provider = DeterministicProvider(responses=responses)
    config = json.loads(provider.config_content)
    config["plugin"] = ["./.opencode/plugin/enforce-no-wait.ts"]
    tui = _Tui(
        json.dumps(config),
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
            "GLUDD_SESSION_START_ENFORCE": "0",
            "GLUDD_STOP_ENFORCE": "0",
            "GLUDD_TASK_DEADLINE_ENABLED": "0",
            "GLUDD_TASK_TRACKING_ENFORCE": "0",
            "GLUDD_TDD_ENFORCE": "0",
            "GLUDD_TEST_INTEGRITY_ENFORCE": "0",
            "GLUDD_VERIFIED_CLAIMS_ENFORCE": "0",
            "GLUDD_WORKTREE_ENFORCE": "0",
        },
    )
    try:
        tui.wait_for("Ask anything...", timeout=30)
        allowed_segment = tui.prompt(
            "Use the bash tool to run make version, then reply with only the "
            "version value printed by that command.",
        )
        assert "permission=bash" in allowed_segment
        assert "action.action=allow" in allowed_segment

        gate_tail_segment = tui.prompt(
            "Use the bash tool to run make gate-tail exactly once. If the "
            "no-wait guard denies it, explain that briefly.",
        )
        assert "$ make gate-tail" in gate_tail_segment
        assert "no-wait guard denied the blocking gate tail" in gate_tail_segment

        gate_status_segment = tui.prompt(
            "Use the bash tool to run make gate-status-check exactly once. If "
            "the no-wait guard denies it, explain that briefly.",
        )
        assert "$ make gate-status-check" in gate_status_segment
        assert (
            "no-wait guard denied the blocking gate status check"
            in gate_status_segment
        )
        assert provider.main_calls == 6
    finally:
        tui.close()
        provider.close()
