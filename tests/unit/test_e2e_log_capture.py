"""Timeout and streaming contracts for the live E2E log runner."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import scripts.e2e_log_capture as log_capture


def test_non_streaming_capture_forwards_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        observed.update(command=command, timeout=kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    monkeypatch.setattr(log_capture.subprocess, "run", fake_run)

    result = log_capture.capture(
        ["fake-command"],
        label="configurable-timeout",
        timeout_seconds=3725,
    )

    assert observed == {"command": ["fake-command"], "timeout": 3725}
    assert result["exit_code"] == 0


def test_streaming_capture_times_out_and_keeps_live_output(tmp_path: Path) -> None:
    log_capture.LOG_DIR = tmp_path
    result = log_capture.capture(
        [
            sys.executable,
            "-c",
            "import time; print('LIVE_EVENT', flush=True); time.sleep(1)",
        ],
        label="stream-timeout",
        tee=True,
        timeout_seconds=0.1,
    )

    assert result["exit_code"] == 124
    assert result["error_summary"] == ["TIMEOUT after 0.1s"]
    log_text = Path(result["log_file"]).read_text(encoding="utf-8")
    assert "LIVE_EVENT" in log_text
    assert "TIMEOUT after 0.1s" in log_text
