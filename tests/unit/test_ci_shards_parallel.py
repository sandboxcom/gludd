from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

import run_ci_shards_parallel as runner


class FakeProc:
    def __init__(self, pid: int, returncode: int, lines: list[str]) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = list(lines)
        self.wait_called = False

    def poll(self) -> int:
        return self.returncode

    def wait(self) -> int:
        self.wait_called = True
        return self.returncode


def test_build_shard_command_uses_make_target() -> None:
    assert runner.build_shard_command("unit-2", "-q") == [
        "make",
        "--no-print-directory",
        "test-ci-shard-summary",
        "SHARD=unit-2",
        "PYTEST_ARGS=-q",
    ]


def test_child_env_caps_workers_per_shard(monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_XDIST", "8")
    env = runner.child_env(1)
    assert env["GLUDD_XDIST"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_parallel_runner_launches_all_shards_and_aggregates_failures(tmp_path: Path) -> None:
    started: list[tuple[list[str], dict[str, Any]]] = []
    procs: list[FakeProc] = []

    def fake_popen(cmd, **kwargs):
        started.append((list(cmd), dict(kwargs)))
        shard = next(part.removeprefix("SHARD=") for part in cmd if part.startswith("SHARD="))
        rc = 1 if shard == "unit-2" else 0
        proc = FakeProc(len(started), rc, [f"{shard} output" + chr(10)])
        procs.append(proc)
        return proc

    rc = runner.run_parallel(
        ["unit-2", "unit-3"],
        pytest_args="-q",
        workers_per_shard=1,
        log_dir=tmp_path,
        heartbeat_interval=999,
        popen_factory=fake_popen,
        install_signal_guard=False,
    )

    assert rc == 1
    assert [cmd[3] for cmd, _kwargs in started] == ["SHARD=unit-2", "SHARD=unit-3"]
    assert all(kwargs["env"]["GLUDD_XDIST"] == "1" for _cmd, kwargs in started)
    assert all(kwargs["start_new_session"] is True for _cmd, kwargs in started)
    assert all(proc.wait_called for proc in procs)
    assert (tmp_path / "unit-2.log").read_text() == "unit-2 output" + chr(10)
    assert (tmp_path / "unit-3.log").read_text() == "unit-3 output" + chr(10)


def test_parallel_runner_never_terminates_sibling_processes() -> None:
    source = Path(runner.__file__).read_text()
    assert ".terminate(" not in source
    assert ".kill(" not in source
    assert "signal.signal(signal.SIGTERM" in source


def test_unexpected_sigterm_marks_run_failed_after_shards_finish(tmp_path: Path) -> None:
    procs: list[FakeProc] = []
    sent = False

    class SigtermProc(FakeProc):
        def poll(self) -> int:
            nonlocal sent
            if not sent:
                sent = True
                os.kill(os.getpid(), signal.SIGTERM)
            return self.returncode

    def fake_popen(cmd, **kwargs):
        proc = SigtermProc(len(procs) + 1, 0, ["ok" + chr(10)])
        procs.append(proc)
        return proc

    rc = runner.run_parallel(
        ["unit-2"],
        pytest_args="-q",
        workers_per_shard=1,
        log_dir=tmp_path,
        heartbeat_interval=999,
        popen_factory=fake_popen,
        install_signal_guard=True,
    )

    assert rc == 2
    assert procs[0].wait_called
