"""Regression coverage for MakeRunner subprocess stream ownership."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from general_ludd.commands.make import MakeRunner


def test_run_closes_captured_subprocess_streams() -> None:
    """Close both owned pipes after their bounded output has been copied."""
    stdout = io.StringIO("ready\n")
    stderr = io.StringIO("diagnostic\n")
    process = MagicMock(stdout=stdout, stderr=stderr, returncode=0)

    with patch(
        "general_ludd.commands.make.subprocess.Popen",
        return_value=process,
    ):
        result = MakeRunner().run("test")

    assert result.stdout_tail == "ready\n"
    assert result.stderr_tail == "diagnostic\n"
    assert stdout.closed is True
    assert stderr.closed is True
