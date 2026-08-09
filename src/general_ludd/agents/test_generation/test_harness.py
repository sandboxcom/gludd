"""HarnessRunner — runs generated pytest files in a subprocess.

Captures stdout/stderr/returncode with a configurable timeout.
Provides the execution layer for generated E2E tests.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class TestResult:
    __slots__ = ("returncode", "stderr", "stdout")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class HarnessRunner:
    def __init__(
        self,
        *,
        pytest_args: list[str] | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.pytest_args = pytest_args if pytest_args is not None else ["-v"]
        self.timeout_seconds = timeout_seconds

    def execute(self, *, test_dir: str) -> TestResult:
        test_path = Path(test_dir)
        cmd = [sys.executable, "-m", "pytest", str(test_path), *self.pytest_args]

        logger.info("running pytest: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return TestResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("pytest timed out after %ds", self.timeout_seconds)
            return TestResult(
                returncode=-1,
                stdout=exc.stdout.decode() if exc.stdout else "",
                stderr=exc.stderr.decode() if exc.stderr else f"timeout after {self.timeout_seconds}s",
            )


__all__ = ["HarnessRunner", "TestResult"]
