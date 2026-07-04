from __future__ import annotations

import subprocess


class SandboxExecutor:
    def __init__(self, timeout: int = 30, max_output_bytes: int = 1_000_000) -> None:
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def execute(self, command: str, workdir: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
