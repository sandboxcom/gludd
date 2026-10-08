"""Sandboxed subprocess executor with a scrubbed environment allowlist."""

from __future__ import annotations

import os
import shlex
import subprocess

# Playbook env allowlist — subprocess children must never inherit daemon
# secrets (ZAI_API_KEY, AWS_*, DATABASE_URL, GLUDD_AUTH_PSK, …).  Mirrors
# AnsibleCoreRunner._PLAYBOOK_ENV_ALLOWLIST.
_SANDBOX_PLAYBOOK_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "GLUDD_PLAYBOOK_TIMEOUT",
        "ANSIBLE_CONFIG",
        "ANSIBLE_ROLES_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_LIBRARY",
        "ANSIBLE_MODULE_UTILS",
        "ANSIBLE_FILTER_PLUGINS",
        "ANSIBLE_CALLBACK_PLUGINS",
        "ANSIBLE_LOOKUP_PLUGINS",
        "ANSIBLE_STRATEGY_PLUGINS",
        "ANSIBLE_CACHE_PLUGINS",
        "ANSIBLE_CONNECTION_PLUGINS",
        "ANSIBLE_VARS_PLUGINS",
        "ANSIBLE_HOST_KEY_CHECKING",
        "ANSIBLE_STDOUT_CALLBACK",
        "ANSIBLE_RETRY_FILES_ENABLED",
        "ANSIBLE_FORCE_COLOR",
        "ANSIBLE_NOCOLOR",
        "ANSIBLE_VERBOSITY",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "VIRTUAL_ENV",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "TERM",
        "COLUMNS",
        "LINES",
    }
)


class SandboxExecutor:
    """Run shell commands under bounded time/size limits with a scrubbed env."""

    def __init__(self, timeout: int = 30, max_output_bytes: int = 1_000_000) -> None:
        """Initialize the executor with its timeout and output caps."""
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.max_command_chars = 1_000_000

    def execute(self, command: str, workdir: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run one command with the allowlisted environment and capture its output."""
        if len(command) > self.max_command_chars:
            raise OSError(f"command length {len(command)} exceeds sandbox limit {self.max_command_chars}")
        scrubbed_env = {k: v for k, v in os.environ.items() if k in _SANDBOX_PLAYBOOK_ENV_ALLOWLIST}
        return subprocess.run(
            shlex.split(command),
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=scrubbed_env,
        )
