#!/usr/bin/env python3
from pathlib import Path

runner = Path("scripts/run_ci_shards_parallel.py")
text = runner.read_text()
old_guard = """def _install_sigterm_guard(lock: threading.Lock):
    previous = signal.getsignal(signal.SIGTERM)

    def _handler(_signum, _frame) -> None:
        _emit(
            lock,
            "ci-shards-parallel: received SIGTERM; continuing to supervise child shards",
        )

    signal.signal(signal.SIGTERM, _handler)
    return previous
"""
new_guard = """def _install_sigterm_guard(lock: threading.Lock) -> tuple[signal.Handlers, threading.Event]:
    previous = signal.getsignal(signal.SIGTERM)
    received = threading.Event()

    def _handler(_signum, _frame) -> None:
        received.set()
        _emit(
            lock,
            "ci-shards-parallel: received unexpected SIGTERM; continuing to supervise child shards",
        )

    signal.signal(signal.SIGTERM, _handler)
    return previous, received
"""
text = text.replace(old_guard, new_guard)
old_install = """    previous_sigterm = _install_sigterm_guard(lock) if install_signal_guard else None
    env = child_env(workers_per_shard)
"""
new_install = """    previous_sigterm = None
    sigterm_received = threading.Event()
    if install_signal_guard:
        previous_sigterm, sigterm_received = _install_sigterm_guard(lock)
    env = child_env(workers_per_shard)
"""
text = text.replace(old_install, new_install)
old_failed = """        failed = [result for result in results if result.returncode != 0]
        _emit(lock, "ci-shards-parallel: logs in " + str(log_dir))
"""
new_failed = """        failed = [result for result in results if result.returncode != 0]
        unexpected_sigterm = sigterm_received.is_set()
        _emit(lock, "ci-shards-parallel: logs in " + str(log_dir))
"""
text = text.replace(old_failed, new_failed)
old_return = """        return 1 if failed else 0
"""
new_return = """        if unexpected_sigterm:
            _emit(lock, "ci-shards-parallel: unexpected SIGTERM observed; marking run failed")
            return 2
        return 1 if failed else 0
"""
text = text.replace(old_return, new_return)
runner.write_text(text)

test = Path("tests/unit/test_ci_shards_parallel.py")
text = test.read_text()
text = text.replace("from pathlib import Path" + chr(10), "import os" + chr(10) + "import signal" + chr(10) + "from pathlib import Path" + chr(10))
old_test = """def test_parallel_runner_never_terminates_sibling_processes() -> None:
    source = Path(runner.__file__).read_text()
    assert ".terminate(" not in source
    assert ".kill(" not in source
    assert "signal.signal(signal.SIGTERM" in source
"""
new_test = """def test_parallel_runner_never_terminates_sibling_processes() -> None:
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
"""
text = text.replace(old_test, new_test)
test.write_text(text)
