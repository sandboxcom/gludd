from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Any

import run_ci_shard_summary as runner


class FakeProc:
    def __init__(self, returncode: int, lines: list[str] | None = None) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdout = lines or ["ok" + chr(10)]
        self.wait_called = False

    def poll(self) -> int:
        return self.returncode

    def wait(self) -> int:
        self.wait_called = True
        return self.returncode


def test_runner_launches_pytest_in_new_session(monkeypatch, tmp_path: Path) -> None:
    started: list[tuple[list[str], dict[str, Any]]] = []
    proc = FakeProc(0)

    monkeypatch.setattr(runner, "expand_shard", lambda shard: ["tests/unit/test_nag_free_output.py"])
    monkeypatch.setenv("GLUDD_XDIST", "1")

    def fake_popen(cmd, **kwargs):
        started.append((list(cmd), dict(kwargs)))
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    rc = runner.run_shard(
        "unit-3",
        pytest_args="--maxfail=1 --tb=short",
        heartbeat_interval=999,
        log_dir=tmp_path,
    )

    assert rc == 0
    assert proc.wait_called
    cmd, kwargs = started[0]
    assert cmd[:3] == [runner.sys.executable, "-m", "pytest"]
    assert "tests/unit/test_nag_free_output.py" in cmd
    assert "-n" in cmd
    assert "1" in cmd
    assert "--dist" in cmd
    assert "loadgroup" in cmd
    assert "--maxfail=1" in cmd
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert (tmp_path / "unit-3.log").read_text() == "ok" + chr(10)


def test_runner_can_disable_xdist(monkeypatch, tmp_path: Path) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(runner, "expand_shard", lambda shard: ["tests/unit/test_nag_free_output.py"])
    monkeypatch.setenv("GLUDD_XDIST", "0")

    def fake_popen(cmd, **kwargs):
        started.append(list(cmd))
        return FakeProc(0)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    rc = runner.run_shard(
        "unit-3",
        pytest_args="",
        heartbeat_interval=999,
        log_dir=tmp_path,
    )

    assert rc == 0
    assert "-n" not in started[0]
    assert "--dist" not in started[0]


def test_refresh_stop_state_updates_existing_json(tmp_path: Path) -> None:
    state_path = tmp_path / "stop-state.json"
    state_path.write_text(json.dumps({"ts": 1, "hasPendingWork": True}))

    changed = runner._refresh_stop_state_if_present(state_path, now_ms=123456)

    assert changed is True
    data = json.loads(state_path.read_text())
    assert data["ts"] == 123456
    assert data["hasPendingWork"] is True


def test_refresh_stop_state_ignores_missing_file(tmp_path: Path) -> None:
    state_path = tmp_path / "missing-stop-state.json"

    changed = runner._refresh_stop_state_if_present(state_path, now_ms=123456)

    assert changed is False
    assert not state_path.exists()


def test_unexpected_sigterm_marks_run_failed_after_pytest_finishes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "expand_shard", lambda shard: ["tests/unit/test_nag_free_output.py"])
    sent = False
    proc = FakeProc(0)

    def poll_with_sigterm() -> int:
        nonlocal sent
        if not sent:
            sent = True
            os.kill(os.getpid(), signal.SIGTERM)
        return proc.returncode

    proc.poll = poll_with_sigterm  # type: ignore[method-assign]
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: proc)

    rc = runner.run_shard(
        "unit-3",
        pytest_args="",
        heartbeat_interval=999,
        log_dir=tmp_path,
    )

    assert rc == 2
    assert proc.wait_called


def test_pytest_sigterm_returncode_marks_run_failed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "expand_shard", lambda shard: ["tests/unit/test_nag_free_output.py"])
    proc = FakeProc(-signal.SIGTERM)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: proc)

    rc = runner.run_shard(
        "unit-3",
        pytest_args="",
        heartbeat_interval=999,
        log_dir=tmp_path,
    )

    assert rc == 2
    assert proc.wait_called


def test_runner_does_not_terminate_or_kill_children() -> None:
    source = Path(runner.__file__).read_text()
    assert ".terminate(" not in source
    assert ".kill(" not in source
    assert "start_new_session=True" in source
    assert "signal.signal(signal.SIGTERM" in source
