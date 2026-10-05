"""Behavioral pins for ``gludd daemon --pid-file`` lifecycle.

The molecule ``daemon_lifecycle`` scenario starts the daemon with a pidfile and
asserts the file is unlinked after SIGTERM (verify.yml "PID file (if any) is
cleaned up after shutdown"). These tests pin the CLI-side behavior that makes
that assertion pass:

- ``--pid-file`` is accepted by the ``daemon`` subcommand parser
- ``_cmd_daemon`` writes the spawned child (gunicorn) PID to the file
- ``_cmd_daemon`` unlinks the file on every exit path (normal child exit and
  SIGINT/KeyboardInterrupt shutdown)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from queue import SimpleQueue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _daemon_args(**overrides) -> SimpleNamespace:
    base = dict(
        host="127.0.0.1",
        port=8000,
        log_level="info",
        tick_interval=1.0,
        workers=1,
        project=None,
        config_dir=None,
        templates_dir=None,
        playbooks_dir=None,
        pid_file=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDaemonParserPidFile:
    def test_daemon_parser_accepts_pid_file(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["daemon", "--pid-file", "/tmp/d.pid"])
        assert args.pid_file == "/tmp/d.pid"

    def test_daemon_parser_defaults_pid_file_to_none(self):
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["daemon"])
        assert args.pid_file is None


class TestCmdDaemonPidFileLifecycle:
    def test_cmd_daemon_writes_child_pid_to_pidfile(self, tmp_path):
        from general_ludd.cli import _cmd_daemon

        pid_file = tmp_path / "daemon.pid"
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = 0
        started = threading.Event()
        release = threading.Event()
        outcome: SimpleQueue[BaseException | None] = SimpleQueue()

        def fake_wait():
            started.set()
            release.wait(timeout=10)
            return 0

        fake_proc.wait.side_effect = fake_wait

        def run() -> None:
            try:
                with patch("subprocess.Popen", return_value=fake_proc), patch("signal.signal"):
                    _cmd_daemon(_daemon_args(pid_file=str(pid_file)))
            except BaseException as exc:
                outcome.put(exc)
            else:
                outcome.put(None)

        thread = threading.Thread(
            target=run,
            name="gludd-test-daemon-pidfile-lifecycle",
        )
        thread.start()
        try:
            assert started.wait(timeout=10), "daemon command never reached wait()"
            assert pid_file.exists(), "pidfile must exist while the daemon is running"
            payload = json.loads(pid_file.read_text())
            assert payload["pid"] == 4242, "pidfile must carry the spawned child PID"
        finally:
            release.set()
            thread.join(timeout=10)
        assert not thread.is_alive(), "test-owned daemon thread must terminate"
        thread_outcome = outcome.get(timeout=1)
        assert isinstance(thread_outcome, SystemExit)
        assert thread_outcome.code == 0

    def test_cmd_daemon_unlinks_pidfile_on_normal_exit(self, tmp_path):
        from general_ludd.cli import _cmd_daemon

        pid_file = tmp_path / "daemon.pid"
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = 0
        fake_proc.wait.return_value = 0
        with patch("subprocess.Popen", return_value=fake_proc), pytest.raises(SystemExit) as excinfo:
            _cmd_daemon(_daemon_args(pid_file=str(pid_file)))
        assert excinfo.value.code == 0
        assert not pid_file.exists(), "pidfile must be unlinked after a clean exit"

    def test_cmd_daemon_unlinks_pidfile_on_sigint(self, tmp_path):
        from general_ludd.cli import _cmd_daemon

        pid_file = tmp_path / "daemon.pid"
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.wait.side_effect = KeyboardInterrupt
        with patch("subprocess.Popen", return_value=fake_proc), pytest.raises(SystemExit) as excinfo:
            _cmd_daemon(_daemon_args(pid_file=str(pid_file)))
        assert excinfo.value.code == 128 + 2
        assert not pid_file.exists(), "pidfile must be unlinked after a SIGINT-style shutdown"


class TestMoleculeConvergePidFileWiring:
    def test_converge_passes_pid_file_to_daemon(self):
        converge = _PROJECT_ROOT / "molecule" / "playbooks" / "daemon_lifecycle" / "default" / "converge.yml"
        text = converge.read_text()
        assert "--pid-file" in text, (
            "converge.yml must pass --pid-file to `general_ludd.cli daemon` so the "
            "daemon owns the pidfile it unlinks on graceful shutdown"
        )
        assert "{{ pidfile }}" in text, "converge.yml must wire the scenario pidfile var into the daemon start command"
