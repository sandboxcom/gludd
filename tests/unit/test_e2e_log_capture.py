"""Timeout and streaming contracts for the live E2E log runner."""

from __future__ import annotations

import json
import subprocess
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
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = log_capture.capture(
        ["fake-command"],
        label="configurable-timeout",
        timeout_seconds=3725,
    )

    assert observed == {"command": ["fake-command"], "timeout": 3725}
    assert result["exit_code"] == 0


def test_streaming_capture_times_out_and_keeps_live_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
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


def test_streaming_capture_closes_owned_child_pipe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    opened: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen

    def tracking_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        opened.append(process)
        return process

    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "Popen", tracking_popen)

    result = log_capture.capture(
        [sys.executable, "-c", "print('owned-pipe', flush=True)"],
        label="stream-owned-pipe",
        tee=True,
        timeout_seconds=5,
    )

    assert result["exit_code"] == 0
    assert len(opened) == 1
    assert opened[0].stdout is not None
    assert opened[0].stdout.closed


def test_capture_rejects_non_positive_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)

    with pytest.raises(ValueError, match="must be positive"):
        log_capture.capture(["unused"], label="bad-timeout", timeout_seconds=0)


def test_non_streaming_timeout_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout_run(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", timeout_run)

    result = log_capture.capture(["slow"], label="nonstream-timeout", timeout_seconds=3)

    assert result["exit_code"] == 124
    assert result["error_summary"] == ["TIMEOUT after 3s"]
    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert stored["exit_code"] == 124


def test_error_extraction_and_run_listing_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    (tmp_path / "good-20260824T000000Z.json").write_text(
        json.dumps(
            {
                "label": "good",
                "timestamp": "20260824T000000Z",
                "command": "true",
                "exit_code": 0,
                "log_file": "good.log",
                "error_summary": None,
                "timeout_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "broken-20260824T000001Z.json").write_text("not-json", encoding="utf-8")

    errors = log_capture._extract_errors(
        "noise\nError: broken\nFAILED test_node\nRuntimeError: boom\n"
    )
    runs = log_capture.list_runs()

    assert errors == ["Error: broken", "FAILED test_node", "RuntimeError: boom"]
    assert runs == [
        {"label": "broken-20260824T000001Z", "timestamp": "", "exit_code": -1},
        {"label": "good", "timestamp": "20260824T000000Z", "exit_code": 0},
    ]


def test_latest_helpers_and_cli_report_owned_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    payload = {
        "label": "latest",
        "timestamp": "20260824T000000Z",
        "command": "false",
        "exit_code": 7,
        "log_file": "latest.log",
        "error_summary": ["FAILED owned"],
        "timeout_seconds": 1,
    }
    (tmp_path / "latest-20260824T000000Z.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    latest_log = tmp_path / "latest-20260824T000000Z.log"
    latest_log.write_text("owned output\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["e2e-log-capture", "--latest", "latest"])

    with pytest.raises(SystemExit) as exc_info:
        log_capture.main()

    assert exc_info.value.code == 7
    assert log_capture.latest_result("latest") == payload
    assert log_capture.latest_log("latest") == latest_log
    output = capsys.readouterr().out
    assert "FAILED owned" in output
    assert "owned output" in output


def test_cli_audit_and_capture_forwarding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(log_capture, "LOG_DIR", tmp_path)
    (tmp_path / "ok-20260824T000000Z.json").write_text(
        json.dumps(
            {
                "label": "ok",
                "timestamp": "20260824T000000Z",
                "command": "true",
                "exit_code": 0,
                "log_file": "ok.log",
                "error_summary": None,
                "timeout_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["e2e-log-capture", "--audit"])
    with pytest.raises(SystemExit) as audit_exit:
        log_capture.main()
    assert audit_exit.value.code == 0
    assert "PASS" in capsys.readouterr().out

    observed: dict[str, object] = {}

    def fake_capture(
        command: list[str],
        *,
        label: str,
        tee: bool,
        timeout_seconds: float,
        env: dict[str, str] | None = None,
    ) -> log_capture.CaptureResult:
        observed.update(
            command=command,
            label=label,
            tee=tee,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        return {
            "label": label,
            "timestamp": "20260824T000000Z",
            "command": " ".join(command),
            "exit_code": 2,
            "log_file": "capture.log",
            "error_summary": ["FAILED capture"],
            "timeout_seconds": timeout_seconds,
        }

    monkeypatch.setattr(log_capture, "capture", fake_capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e-log-capture",
            "--cmd",
            "tool --flag",
            "--label",
            "capture",
            "--tee",
            "--timeout",
            "9",
        ],
    )
    with pytest.raises(SystemExit) as capture_exit:
        log_capture.main()

    assert capture_exit.value.code == 2
    assert observed == {
        "command": ["tool", "--flag"],
        "label": "capture",
        "tee": True,
        "timeout_seconds": 9.0,
        "env": None,
    }
    assert "ERROR SUMMARY" in capsys.readouterr().out
