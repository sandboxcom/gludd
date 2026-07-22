from __future__ import annotations

import shlex
import subprocess


class SandboxExecutor:
    def __init__(self, timeout: int = 30, max_output_bytes: int = 1_000_000) -> None:
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.max_command_chars = 1_000_000

    def execute(self, command: str, workdir: str | None = None) -> subprocess.CompletedProcess[str]:
        if len(command) > self.max_command_chars:
            raise OSError(
                f"command length {len(command)} exceeds sandbox limit "
                f"{self.max_command_chars}"
            )
        return subprocess.run(
            shlex.split(command),
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
