"""Test that stream dispatch + sandbox exec pass scrubbed env= to child processes.

The child process must NOT inherit secrets like ZAI_API_KEY, AWS_*, DATABASE_URL.
Only allowlisted vars may be passed through.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


class _FakePopen:
    """Minimal fake for subprocess.Popen that captures the env= kwarg."""

    def __init__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        env: dict[str, str] | None = None,
        **__: Any,
    ) -> None:
        self.args = args
        self.cwd = cwd
        self.child_env = env
        self.returncode: int = 0

    def communicate(self, input: Any = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        return (b"", b"")

    def kill(self) -> None:
        pass

    def __enter__(self) -> _FakePopen:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class TestStreamDispatchChildEnvIsScrubbed:
    _SECRET_KEYS: frozenset[str] = frozenset(
        {
            "ZAI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL",
            "GLUDD_AUTH_PSK",
            "OPENAI_API_KEY",
        }
    )

    _ALLOWED_KEYS: frozenset[str] = frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "SHELL",
            "LANG",
        }
    )

    # — _run_subprocess (routers/stream.py) ———————————————————

    def test_run_subprocess_passes_scrubbed_env(self, monkeypatch: Any) -> None:
        """_run_subprocess must pass env= with only allowlisted keys."""
        from general_ludd.routers.stream import _run_subprocess

        capt: _FakePopen | None = None

        def _fake(args: list[str], **kwargs: Any) -> _FakePopen:
            nonlocal capt
            capt = _FakePopen(args, **kwargs)
            return capt

        monkeypatch.setattr(subprocess, "Popen", _fake)

        _run_subprocess(["ansible-playbook", "run-clone.yml"], "/tmp/test", 60.0)

        assert capt is not None
        assert capt.child_env is not None, (
            "_run_subprocess MUST pass explicit env= to subprocess.Popen. "
            "Without it, the child inherits the full daemon environment."
        )
        for secret in self._SECRET_KEYS:
            assert secret not in capt.child_env, f"Secret env var {secret!r} leaked into child process env"

    def test_allowlisted_keys_pass_through(self, monkeypatch: Any) -> None:
        """Allowlisted keys like PATH, HOME must still be passed through."""
        from general_ludd.routers.stream import _run_subprocess

        capt: _FakePopen | None = None

        def _fake(args: list[str], **kwargs: Any) -> _FakePopen:
            nonlocal capt
            capt = _FakePopen(args, **kwargs)
            return capt

        monkeypatch.setattr(subprocess, "Popen", _fake)

        _run_subprocess(["ansible-playbook", "run-clone.yml"], "/tmp/test", 60.0)

        assert capt is not None and capt.child_env is not None
        for key in self._ALLOWED_KEYS:
            if key in os.environ:
                assert key in capt.child_env, f"Allowlisted env var {key!r} must pass through to child"

    def test_environ_secrets_are_stripped(self, monkeypatch: Any) -> None:
        """With mock secrets in os.environ, child env must exclude them."""
        from general_ludd.routers.stream import _run_subprocess

        seeds = {"ZAI_API_KEY": "sk-test-secret", "DATABASE_URL": "pg://bad"}  # pragma: allowlist secret
        monkeypatch.setattr(os, "environ", {**os.environ, **seeds})

        capt: _FakePopen | None = None

        def _fake(args: list[str], **kwargs: Any) -> _FakePopen:
            nonlocal capt
            capt = _FakePopen(args, **kwargs)
            return capt

        monkeypatch.setattr(subprocess, "Popen", _fake)

        _run_subprocess(["ansible-playbook", "run-clone.yml"], "/tmp/test", 60.0)

        assert capt is not None and capt.child_env is not None
        for key in seeds:
            assert key not in capt.child_env, f"Secret env var {key!r} leaked into child process env"

    # — SandboxExecutor.execute ————————————————————————————————

    def test_sandbox_executor_passes_scrubbed_env(self, monkeypatch: Any) -> None:
        """SandboxExecutor.execute must pass env= with only allowlisted keys."""
        from general_ludd.sandbox_exec.executor import SandboxExecutor

        capt_env: dict[str, str] | None = None

        def _capture_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal capt_env
            capt_env = kwargs.get("env")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "general_ludd.sandbox_exec.executor.subprocess.run",
            _capture_run,
        )

        executor = SandboxExecutor()
        executor.execute("echo hello", workdir="/tmp/test")

        assert capt_env is not None, (
            "SandboxExecutor.execute MUST pass explicit env=. "
            "Without it, the child inherits the full daemon environment."
        )
        for secret in self._SECRET_KEYS:
            assert secret not in capt_env, f"Secret env var {secret!r} leaked via SandboxExecutor child env"

    def test_sandbox_executor_strips_injected_secrets(self, monkeypatch: Any) -> None:
        """With mock secrets, SandboxExecutor child env must exclude them."""
        from general_ludd.sandbox_exec.executor import SandboxExecutor

        seeds = {"ZAI_API_KEY": "sk-test-secret", "DATABASE_URL": "pg://bad"}  # pragma: allowlist secret
        monkeypatch.setattr(os, "environ", {**os.environ, **seeds})

        capt_env: dict[str, str] | None = None

        def _capture_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal capt_env
            capt_env = kwargs.get("env")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "general_ludd.sandbox_exec.executor.subprocess.run",
            _capture_run,
        )

        executor = SandboxExecutor()
        executor.execute("echo hello", workdir="/tmp/test")

        assert capt_env is not None
        for key in seeds:
            assert key not in capt_env, f"Secret env var {key!r} leaked via SandboxExecutor child env"
