"""Behavioral tests for parent-readable observed command execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.observed_command_native_fixtures import (
    assert_native_import_shadow_control,
    make_eof_heartbeat_case,
)

if TYPE_CHECKING:
    import stream_command
else:
    from scripts import stream_command

ROOT = Path(__file__).resolve().parents[2]
STREAM_RUNNER = ROOT / "scripts" / "stream_command.py"
STATUS_FIELDS = {
    "schema_version",
    "kind",
    "label",
    "run_id",
    "state",
    "owner_pid",
    "child_pid",
    "started_at",
    "updated_at",
    "heartbeat_seq",
    "elapsed_seconds",
    "last_output_at",
    "quiet_seconds",
    "bytes_written",
    "lines_written",
    "exit_code",
    "termination_reason",
    "log_path",
    "trace_path",
}


def _command(tmp_path: Path, child: str, *observer_args: str, run_id: str = "run-1") -> list[str]:
    return [
        sys.executable,
        str(STREAM_RUNNER),
        "--root",
        str(tmp_path / "observed"),
        "--label",
        "demo",
        "--run-id",
        run_id,
        *observer_args,
        "--",
        sys.executable,
        "-c",
        child,
    ]


def _current_status(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "observed" / "demo" / "current.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_observed_command_publishes_atomic_terminal_status_and_log(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "print('visible child output', flush=True)"), capture_output=True, check=False
    )

    status = _current_status(tmp_path)
    run_status = tmp_path / "observed" / "demo" / "run-1.json"

    assert completed.returncode == 0
    assert b"visible child output\n" in completed.stdout
    assert completed.stderr == b""
    assert set(status) == STATUS_FIELDS
    assert status["schema_version"] == 1
    assert status["kind"] == "observed_command"
    assert status["label"] == "demo"
    assert status["run_id"] == "run-1"
    assert status["state"] == "passed"
    assert status["exit_code"] == 0
    assert status["termination_reason"] is None
    assert status["bytes_written"] == len(b"visible child output\n")
    assert status["lines_written"] == 1
    assert Path(status["log_path"]).read_bytes() == b"visible child output\n"
    assert status["trace_path"] is None
    assert json.loads(run_status.read_text(encoding="utf-8")) == status
    assert not list(run_status.parent.glob(".*.tmp"))


@pytest.mark.parametrize("artifact", ["run-1.json", "run-1.log", "run-1.pytest.jsonl", "custom.log", "dangling"])
def test_observed_start_refuses_existing_artifacts_before_running_child(tmp_path: Path, artifact: str) -> None:
    directory = tmp_path / "observed/demo"
    directory.mkdir(parents=True)
    current = directory / "current.json"
    current.write_bytes(b"prior current receipt\n")
    log_args: tuple[str, ...] = ()
    if artifact == "custom.log":
        protected = tmp_path / artifact
        log_args = ("--log", str(protected))
    else:
        protected = directory / ("run-1.json" if artifact == "dangling" else artifact)
    if artifact == "dangling":
        protected.symlink_to(directory / "missing.json")
    else:
        protected.write_bytes(b"prior evidence\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    marker = tmp_path / "child-executed"
    result = subprocess.run(
        _command(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')", *log_args),
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 125, result.stdout + result.stderr
    assert not marker.exists()
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    if artifact == "dangling":
        assert protected.is_symlink() and not protected.exists()


def test_observed_start_rejects_current_alias_as_writable_run_id(tmp_path: Path) -> None:
    current = tmp_path / "observed/demo/current.json"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"prior current receipt\n")
    result = subprocess.run(
        _command(tmp_path, "print('must not run')", run_id="current"), capture_output=True, check=False, timeout=10
    )
    assert result.returncode == 125, result.stdout + result.stderr
    assert current.read_bytes() == b"prior current receipt\n"
    assert not (current.parent / "current.log").exists()


def test_competing_observers_claim_one_run_identity_atomically(tmp_path: Path) -> None:
    marker = tmp_path / "child-executed"
    child = (
        f"from pathlib import Path; import time; Path({str(marker)!r}).open('a').write('executed\\n'); time.sleep(0.2)"
    )
    processes = [
        subprocess.Popen(_command(tmp_path, child), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)
    ]
    outputs = [process.communicate(timeout=10) for process in processes]
    assert sorted(process.returncode for process in processes) == [0, 125], outputs
    assert marker.read_text().splitlines() == ["executed"]
    status = _current_status(tmp_path)
    assert status["state"] == "passed" and status["exit_code"] == 0
    assert json.loads((tmp_path / "observed/demo/run-1.json").read_text()) == status


def test_exclusive_atomic_status_claim_never_replaces_existing_evidence(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    original = b"immutable old receipt\n"
    target.write_bytes(original)
    with pytest.raises(FileExistsError):
        stream_command._atomic_write_json(target, {"state": "starting"}, exclusive=True)
    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_log_created_after_start_check_is_not_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "observed/demo"
    log_path = directory / "run-1.log"
    original_write = stream_command._atomic_write_json

    def competing_log(path: Path, payload: Any, *, exclusive: bool = False) -> None:
        original_write(path, payload, exclusive=exclusive)
        if exclusive:
            log_path.write_bytes(b"competing log evidence\n")

    monkeypatch.setattr(stream_command, "_atomic_write_json", competing_log)
    result = stream_command.stream_command(
        [sys.executable, "-c", "raise RuntimeError('must not run')"],
        log_path,
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
    )
    assert result == 125
    assert log_path.read_bytes() == b"competing log evidence\n"
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["child_pid"] is None
    assert status["termination_reason"] == "observer-log-open-failed"


def test_trace_allocated_after_precheck_preserves_competing_live_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "observed/demo/run-1.pytest.jsonl"
    release = tmp_path / "release-competing-child"
    marker = tmp_path / "trace-child-executed"
    competitors: list[subprocess.Popen[bytes]] = []
    original_write = stream_command._atomic_write_json
    original_bytes = b"competing live log evidence\n"

    def claim_competing_log(path: Path, payload: Any, *, exclusive: bool = False) -> None:
        original_write(path, payload, exclusive=exclusive)
        if not exclusive or path.name != "run-1.json":
            return
        child = (
            "import time; from pathlib import Path; "
            "print('competing live log evidence', flush=True)\n"
            f"while not Path({str(release)!r}).exists(): time.sleep(0.01)"
        )
        competitors.append(
            subprocess.Popen(
                _command(tmp_path, child, "--log", str(trace), run_id="other"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if trace.exists() and trace.read_bytes() == original_bytes:
                break
            time.sleep(0.01)
        assert trace.read_bytes() == original_bytes
        assert competitors[0].poll() is None

    monkeypatch.setattr(stream_command, "_atomic_write_json", claim_competing_log)
    child = (
        "from pathlib import Path; from types import SimpleNamespace; "
        "from scripts import xdist_trace_plugin; "
        f"Path({str(marker)!r}).touch(); "
        "xdist_trace_plugin.pytest_sessionstart(SimpleNamespace(config=None))"
    )
    try:
        result = stream_command.stream_command(
            [sys.executable, "-c", child],
            tmp_path / "observed/demo/run-1.log",
            observed_root=tmp_path / "observed",
            label="demo",
            run_id="run-1",
            pytest_trace=True,
            quiet=True,
            max_seconds=10,
        )
        assert result == 125
        assert not marker.exists()
        assert trace.read_bytes() == original_bytes
        assert _current_status(tmp_path)["termination_reason"] == "observer-trace-open-failed"
    finally:
        release.touch()
        for process in competitors:
            output = process.communicate(timeout=10)
            assert process.returncode == 0, output


def test_fresh_observed_trace_is_allocated_before_native_handoff_without_reset(tmp_path: Path) -> None:
    child = (
        "import os; from pathlib import Path; from types import SimpleNamespace; "
        "from scripts import xdist_trace_plugin; "
        "trace = Path(os.environ['GLUDD_XDIST_TRACE_LOG']); "
        "assert trace.exists() and trace.read_bytes() == b''; "
        "assert os.environ['GLUDD_XDIST_TRACE_TRUNCATE'] == '0'; "
        "xdist_trace_plugin.pytest_sessionstart(SimpleNamespace(config=None))"
    )
    result = subprocess.run(_command(tmp_path, child, "--pytest-trace"), capture_output=True, check=False, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    status = _current_status(tmp_path)
    event = json.loads(Path(status["trace_path"]).read_text())
    assert event["event"] == "RUN_START" and event["run_id"] == "run-1"


def test_retention_preserves_late_finishing_current_run_and_its_log(tmp_path: Path) -> None:
    root = tmp_path / "observed"
    old = stream_command._StatusPublisher(root, "demo", "old")
    newer = stream_command._StatusPublisher(root, "demo", "newer")
    status = stream_command._base_status(
        label="demo",
        run_id="old",
        started_at="2026-10-14T00:00:00+00:00",
        log_path=old.directory / "old.log",
        trace_path=None,
    )
    old.publish(status)
    other = stream_command._base_status(
        label="demo",
        run_id="newer",
        started_at="2026-10-14T00:00:01+00:00",
        log_path=newer.directory / "newer.log",
        trace_path=None,
    )
    other.update(state="failed", exit_code=1)
    newer.publish(other)
    (newer.directory / "newer.log").write_bytes(b"newer failure\n")
    status.update(state="failed", exit_code=1)
    old.publish(status)
    (old.directory / "old.log").write_bytes(b"late current failure\n")
    stream_command._prune_terminal_runs(old.directory, retain_runs=1)
    assert old.run_path.exists(), "current run was pruned because its start time is older"
    assert (old.directory / "old.log").read_bytes() == b"late current failure\n"
    assert stream_command.observed_tail(root, "demo", run_id="current", lines=1) == 0


def test_atomic_status_writer_fsyncs_then_replaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "status.json"
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", record_replace)

    stream_command._atomic_write_json(target, {"state": "running"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "running"}
    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == target.parent
    assert temporary != target
    assert destination == target
    assert not temporary.exists()


def test_quiet_child_emits_heartbeats_and_updates_status(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "import time; time.sleep(0.22)", "--heartbeat-secs", "0.03", "--max-secs", "2"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.count(b"[observed demo] heartbeat") >= 2
    assert status["heartbeat_seq"] >= 2
    assert status["quiet_seconds"] >= 0.1


def test_observer_keeps_monitoring_after_child_closes_output(tmp_path: Path) -> None:
    case = make_eof_heartbeat_case(tmp_path, _command)
    completed = subprocess.run(case.command, capture_output=True, check=False, timeout=5, env=case.environment)

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.count(b"[observed demo] heartbeat") >= 2
    assert status["state"] == "passed"
    case.assert_released(status)


def test_eof_heartbeat_guard_rejects_child_banners_without_native_ticks(tmp_path: Path) -> None:
    case = make_eof_heartbeat_case(tmp_path, _command, heartbeat_seconds="0", spoof_banners=True)
    completed = subprocess.run(case.command, capture_output=True, check=False, timeout=5, env=case.environment)
    assert completed.stdout.count(b"[observed demo] heartbeat") >= 2
    assert completed.returncode == 124
    case.assert_no_native_ticks(_current_status(tmp_path))


def test_post_eof_metadata_has_active_native_import_shadow_control(tmp_path: Path) -> None:
    assert_native_import_shadow_control(make_eof_heartbeat_case(tmp_path, _command), _command)


def test_quiet_mode_captures_without_streaming_child_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "print('captured only', flush=True)", "--quiet"), capture_output=True, check=False
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert b"captured only" not in completed.stdout
    assert Path(status["log_path"]).read_text(encoding="utf-8") == "captured only\n"


def test_observer_preserves_child_failure_code(tmp_path: Path) -> None:
    completed = subprocess.run(_command(tmp_path, "import sys; sys.exit(7)"), capture_output=True, check=False)

    status = _current_status(tmp_path)
    assert completed.returncode == 7
    assert status["state"] == "failed"
    assert status["exit_code"] == 7
    assert status["termination_reason"] is None


def test_native_owned_group_zero_lifecycle_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Observe group-zero across retained exit and reap; never send a signal."""
    import errno

    import psutil

    if os.name != "posix":
        print("NATIVE_GROUP_ZERO unavailable-non-posix")
        return
    assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    grace = 0.05
    budget = max(grace, 1.0)
    descendant_source = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "(root / 'descendant-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-descendant').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    leader_source = (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        f"descendant_source = {descendant_source!r}\n"
        "descendant = subprocess.Popen([sys.executable, '-c', descendant_source, str(root)], "
        "env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "(root / 'leader-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-leader').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "os._exit(17)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", leader_source, str(tmp_path)],
        cwd=tmp_path,
        env=os.environ.copy(),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    native_methods = {name: getattr(process, name) for name in ("poll", "wait", "send_signal", "kill", "terminate")}
    native_wait = process.wait
    final_wait_calls = 0
    final_status: int | None = None
    descendant_pid: int | None = None
    descendant_birth: float | None = None
    member_deadline: float | None = None
    evidence: dict[str, Any] = {
        "runtime": sys.version.split()[0],
        "platform": sys.platform,
        "leader_pid": process.pid,
        "physical_signals_sent": False,
        "stages": [],
        "post_reap_absence_proved": False,
        "leader_wait_budget": budget,
        "total_member_observation_budget": budget,
    }

    def forbidden_reap(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Private leader must remain unreaped until its one final wait")

    for name in native_methods:
        monkeypatch.setattr(process, name, forbidden_reap)

    def observe_group(stage: str) -> int:
        try:
            os.killpg(process.pid, 0)
            outcome = 0
        except OSError as error:
            evidence["stages"].append({"stage": stage, "errno": error.errno, "result": "native-error"})
            if error.errno not in (errno.EPERM, errno.ESRCH):
                raise
            return error.errno
        evidence["stages"].append({"stage": stage, "errno": None, "result": 0})
        return outcome

    def final_reap() -> None:
        nonlocal final_wait_calls, final_status
        for name, method in native_methods.items():
            monkeypatch.setattr(process, name, method)
        assert final_wait_calls == 0
        final_wait_calls += 1
        final_status = native_wait(timeout=budget)

    try:
        deadline = time.monotonic() + budget
        while not (tmp_path / "descendant-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert int((tmp_path / "leader-ready").read_text()) == process.pid
        leader = psutil.Process(process.pid)
        leader_birth = leader.create_time()
        assert leader.ppid() == os.getpid() and leader.status() != psutil.STATUS_ZOMBIE
        assert leader.cwd() == str(tmp_path.resolve())
        assert os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        descendant_pid = int((tmp_path / "descendant-ready").read_text())
        descendant = psutil.Process(descendant_pid)
        descendant_birth = descendant.create_time()
        assert descendant.ppid() == process.pid and descendant.status() != psutil.STATUS_ZOMBIE
        assert descendant.cwd() == str(tmp_path.resolve())
        assert os.getpgid(descendant_pid) == os.getsid(descendant_pid) == process.pid
        evidence["leader_birth"] = leader_birth
        evidence["descendant_pid"] = descendant_pid
        evidence["descendant_birth"] = descendant_birth
        assert observe_group("live-leader-and-descendant") == 0

        (tmp_path / "release-leader").touch()
        deadline = time.monotonic() + budget
        while psutil.Process(process.pid).status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.01)
        zombie = psutil.Process(process.pid)
        assert zombie.status() == psutil.STATUS_ZOMBIE
        assert zombie.create_time() == leader_birth and zombie.ppid() == os.getpid()
        assert process.returncode is None
        fresh_descendant = psutil.Process(descendant_pid)
        assert fresh_descendant.create_time() == descendant_birth
        assert fresh_descendant.status() != psutil.STATUS_ZOMBIE
        assert fresh_descendant.cwd() == str(tmp_path.resolve())
        assert os.getpgid(descendant_pid) == os.getsid(descendant_pid) == process.pid
        evidence["positive_retained_zombie"] = True
        evidence["fresh_original_descendant_live"] = True
        observe_group("zombie-leader-and-live-original-descendant")

        member_deadline = time.monotonic() + budget
        (tmp_path / "release-descendant").touch()
        while True:
            try:
                terminal_descendant = psutil.Process(descendant_pid)
                assert terminal_descendant.create_time() == descendant_birth
                if terminal_descendant.status() == psutil.STATUS_ZOMBIE:
                    evidence["original_descendant_terminal"] = "zombie"
                    break
            except psutil.ZombieProcess:
                raise
            except psutil.NoSuchProcess:
                evidence["original_descendant_terminal"] = "gone"
                break
            assert time.monotonic() < member_deadline, "Original member observation budget expired"
            time.sleep(0.01)
        zombie = psutil.Process(process.pid)
        assert zombie.status() == psutil.STATUS_ZOMBIE
        assert zombie.create_time() == leader_birth and zombie.ppid() == os.getpid()
        assert process.returncode is None
        observe_group("zombie-leader-known-original-descendant-terminal")
        final_reap()
        assert final_status == process.returncode == 17
        while True:
            outcome = observe_group("after-final-wait-observation-only")
            if outcome == errno.ESRCH:
                evidence["post_reap_absence_proved"] = True
                break
            assert time.monotonic() < member_deadline, "Post-reap group absence remains unproved"
            time.sleep(0.01)
    finally:
        (tmp_path / "release-leader").touch(exist_ok=True)
        (tmp_path / "release-descendant").touch(exist_ok=True)
        try:
            if descendant_pid is not None and descendant_birth is not None:
                if member_deadline is None:
                    member_deadline = time.monotonic() + budget
                while True:
                    try:
                        remaining = psutil.Process(descendant_pid)
                        assert remaining.create_time() == descendant_birth
                        if remaining.status() == psutil.STATUS_ZOMBIE:
                            break
                    except psutil.ZombieProcess:
                        raise
                    except psutil.NoSuchProcess:
                        break
                    assert time.monotonic() < member_deadline, "Owned fixture cleanup budget expired"
                    time.sleep(0.01)
        finally:
            if final_wait_calls == 0:
                final_reap()
            evidence["final_wait_calls"] = final_wait_calls
            evidence["final_native_status"] = final_status
            evidence["final_returncode"] = process.returncode
            print("NATIVE_GROUP_ZERO " + json.dumps(evidence, sort_keys=True))
            assert final_wait_calls == 1 and final_status == process.returncode == 17


@pytest.mark.parametrize("reader_mode", ("module", "status", "tail"))
def test_passive_observer_readers_require_only_the_standard_library(tmp_path: Path, reader_mode: str) -> None:
    """Passive readers must not import the execution-only process dependency."""
    log = tmp_path / "observed" / "demo" / "run-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("owned passive line\n", encoding="utf-8")
    payload = stream_command._base_status(
        label="demo", run_id="run-1", started_at=stream_command._utc_now(), log_path=log, trace_path=None
    )
    payload.update(state="passed", exit_code=0)
    stream_command._StatusPublisher(tmp_path / "observed", "demo", "run-1").publish(payload)
    program = (
        "import runpy, sys\n"
        "class RefuseExecutionDependency:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'psutil' or fullname.startswith('psutil.'):\n"
        "            raise ModuleNotFoundError('controlled execution-only dependency refusal')\n"
        "        return None\n"
        "sys.meta_path.insert(0, RefuseExecutionDependency())\n"
        "mode = sys.argv[1]\n"
        "sys.argv = sys.argv[2:]\n"
        "runpy.run_path(sys.argv[0], run_name='passive_reader_probe' if mode == 'module' else '__main__')\n"
    )
    reader_arguments = (
        []
        if reader_mode == "module"
        else [
            "--root",
            str(tmp_path / "observed"),
            "--label",
            "demo",
            *(["--status"] if reader_mode == "status" else ["--tail", "1"]),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", program, reader_mode, str(STREAM_RUNNER), *reader_arguments],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr.decode()
    if reader_mode == "status":
        assert json.loads(completed.stdout)["state"] == "passed"
    elif reader_mode == "tail":
        assert completed.stdout == b"owned passive line\n"
    else:
        assert not completed.stdout


def test_native_nonreaping_platform_capability_observation() -> None:
    """Report public metadata; missing APIs are refusal inputs, not product failures."""
    import select
    import selectors

    wait_names = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")
    wait_flags = {name: getattr(os, name, None) for name in wait_names}
    wait_missing = [name for name, value in wait_flags.items() if not isinstance(value, int)]
    if not callable(getattr(os, "waitid", None)):
        wait_missing.append("waitid")
    queue_names = (
        "KQ_FILTER_PROC",
        "KQ_NOTE_EXIT",
        "KQ_EV_ADD",
        "KQ_EV_ENABLE",
        "KQ_EV_ONESHOT",
        "KQ_EV_ERROR",
    )
    queue_flags = {name: getattr(select, name, None) for name in queue_names}
    queue_missing = [name for name, value in queue_flags.items() if not isinstance(value, int)]
    for name in ("kqueue", "kevent"):
        if not callable(getattr(select, name, None)):
            queue_missing.append(name)
    candidate = (
        "waitid" if not wait_missing else ("kqueue" if sys.platform == "darwin" and not queue_missing else "refused")
    )
    evidence = {
        "platform": sys.platform,
        "runtime": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "waitid_callable": callable(getattr(os, "waitid", None)),
        "wait_flags": wait_flags,
        "wait_missing": wait_missing,
        "kqueue_callable": callable(getattr(select, "kqueue", None)),
        "kevent_callable": callable(getattr(select, "kevent", None)),
        "queue_flags": queue_flags,
        "queue_missing": queue_missing,
        "optional_queue_flags": {"KQ_EV_RECEIPT": getattr(select, "KQ_EV_RECEIPT", None)},
        "select_source": getattr(select, "__file__", None),
        "select_origin": getattr(getattr(select, "__spec__", None), "origin", None),
        "selector_backend": selectors.DefaultSelector.__name__,
        "backend_candidate": candidate,
        "native_behavior_proved": False,
        "child_created": False,
        "kernel_queue_created": False,
        "waitid_called": False,
    }
    print("NATIVE_NONREAPING_CAPABILITY " + json.dumps(evidence, sort_keys=True))
    for name in ("kqueue", "kevent"):
        native = getattr(select, name, None)
        doc = getattr(native, "__doc__", None) if native is not None else None
        print("NATIVE_SELECT_DOC " + name + " " + (doc.splitlines()[0] if isinstance(doc, str) else "unavailable"))


@pytest.mark.parametrize("registration_timing", ("before-exit", "after-zombie"))
def test_native_kqueue_retained_leader_and_session_prerequisite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registration_timing: str
) -> None:
    """Observe retained leader/session identity and actual late registration."""
    import errno
    import select

    import psutil

    queue_factory = getattr(select, "kqueue", None)
    event_factory = getattr(select, "kevent", None)
    required = ("KQ_FILTER_PROC", "KQ_NOTE_EXIT", "KQ_EV_ADD", "KQ_EV_ENABLE", "KQ_EV_ONESHOT", "KQ_EV_ERROR")
    if (
        not callable(queue_factory)
        or not callable(event_factory)
        or any(not isinstance(getattr(select, name, None), int) for name in required)
    ):
        print(
            "NATIVE_KQUEUE_PREREQUISITE "
            + json.dumps(
                {
                    "runtime": sys.version.split()[0],
                    "platform": sys.platform,
                    "registration_timing": registration_timing,
                    "outcome": "refused-unavailable",
                    "child_created": False,
                    "kernel_queue_created": False,
                    "session_prerequisite_proved": False,
                },
                sort_keys=True,
            )
        )
        return

    assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, "Requires default SIGCHLD/no auto-reap"
    descendant_source = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "(root / 'descendant-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 8\n"
        "while not (root / 'release-descendant').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    leader_source = (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        f"descendant_source = {descendant_source!r}\n"
        "descendant = subprocess.Popen([sys.executable, '-c', descendant_source, str(root)], "
        "env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "(root / 'leader-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-leader').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "os._exit(17)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", leader_source, str(tmp_path)],
        cwd=tmp_path,
        env=os.environ.copy(),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    methods = {name: getattr(process, name) for name in ("poll", "wait", "send_signal", "kill", "terminate")}
    original_wait = process.wait
    forbidden_calls: list[str] = []

    def forbidden_method(*_args: object, **_kwargs: object) -> None:
        forbidden_calls.append("retained-window-process-method")
        raise AssertionError("Popen poll/wait/send_signal/kill/terminate is forbidden before final reap")

    for name in methods:
        monkeypatch.setattr(process, name, forbidden_method)

    evidence: dict[str, object] = {
        "runtime": sys.version.split()[0],
        "platform": sys.platform,
        "registration_timing": registration_timing,
        "child_pid": process.pid,
        "child_created": True,
        "kernel_queue_created": False,
        "default_sigchld": True,
        "sole_reaper_contract": "owned-instance guards and one final Popen.wait",
        "raw_events": [],
        "registration_errno": None,
        "session_prerequisite_proved": False,
        "event_data_is_exit_code_authority": False,
    }
    queue = None
    descendant_pid: int | None = None
    descendant_birth: float | None = None
    final_wait_calls = 0
    final_status: int | None = None
    try:
        deadline = time.monotonic() + 2
        while not (tmp_path / "descendant-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert int((tmp_path / "leader-ready").read_text()) == process.pid
        leader = psutil.Process(process.pid)
        leader_birth = leader.create_time()
        assert leader.ppid() == os.getpid()
        assert leader.status() != psutil.STATUS_ZOMBIE
        assert leader.cwd() == str(tmp_path.resolve())
        assert os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        assert process.returncode is None
        descendant_pid = int((tmp_path / "descendant-ready").read_text())
        descendant = psutil.Process(descendant_pid)
        descendant_birth = descendant.create_time()
        assert descendant.ppid() == process.pid
        assert descendant.status() != psutil.STATUS_ZOMBIE
        assert descendant.cwd() == str(tmp_path.resolve())
        assert os.getpgid(descendant_pid) == os.getsid(descendant_pid) == process.pid
        evidence["live_leader"] = {
            "pid": leader.pid,
            "birth": leader_birth,
            "ppid": leader.ppid(),
            "cwd": leader.cwd(),
            "pgid": os.getpgid(process.pid),
            "sid": os.getsid(process.pid),
        }
        evidence["live_descendant"] = {
            "pid": descendant_pid,
            "birth": descendant_birth,
            "ppid": descendant.ppid(),
            "cwd": descendant.cwd(),
            "pgid": os.getpgid(descendant_pid),
            "sid": os.getsid(descendant_pid),
        }

        if registration_timing == "after-zombie":
            (tmp_path / "release-leader").touch()
            deadline = time.monotonic() + 2
            while psutil.Process(process.pid).status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
                time.sleep(0.01)
            zombie = psutil.Process(process.pid)
            assert zombie.status() == psutil.STATUS_ZOMBIE
            assert zombie.create_time() == leader_birth and zombie.ppid() == os.getpid()
            assert process.returncode is None
            evidence["zombie_before_registration"] = True
        else:
            evidence["zombie_before_registration"] = False

        queue = queue_factory()
        evidence["kernel_queue_created"] = True
        event = event_factory(
            process.pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        events = []
        native_error: OSError | None = None
        try:
            events = queue.control([event], 1, 0)
        except OSError as error:
            native_error = error
        if registration_timing == "before-exit":
            (tmp_path / "release-leader").touch()
        if native_error is None and not events:
            events = queue.control([], 1, 2)
        evidence["raw_events"] = [
            {
                "ident": int(item.ident),
                "filter": int(item.filter),
                "flags": int(item.flags),
                "fflags": int(item.fflags),
                "data": int(item.data),
            }
            for item in events
        ]
        errors = [int(item.data) for item in events if item.flags & select.KQ_EV_ERROR]
        if native_error is not None or errors:
            actual_errno = native_error.errno if native_error is not None else errors[0]
            evidence["registration_errno"] = actual_errno
            evidence["outcome"] = "refused-native-registration"
            if native_error is not None:
                evidence["registration_error"] = str(native_error)
            assert registration_timing == "after-zombie" and actual_errno == errno.ESRCH
        else:
            assert len(events) == 1, "Native NOTE_EXIT event was not delivered within the owned bound"
            observed = events[0]
            assert observed.ident == process.pid and observed.filter == select.KQ_FILTER_PROC
            assert observed.fflags & select.KQ_NOTE_EXIT
            evidence["outcome"] = "exit-event-observed"

        deadline = time.monotonic() + 2
        while psutil.Process(process.pid).status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.01)
        zombie = psutil.Process(process.pid)
        assert zombie.status() == psutil.STATUS_ZOMBIE
        assert zombie.create_time() == leader_birth and zombie.ppid() == os.getpid()
        assert process.returncode is None
        evidence["retained_leader"] = {
            "pid": zombie.pid,
            "birth": zombie.create_time(),
            "ppid": zombie.ppid(),
            "state": zombie.status(),
            "returncode": process.returncode,
        }
        try:
            evidence["post_zombie_leader_pgid"] = os.getpgid(process.pid)
        except ProcessLookupError as error:
            evidence["post_zombie_leader_pgid"] = "unknown"
            evidence["post_zombie_leader_pgid_errno"] = error.errno
        fresh_descendant = psutil.Process(descendant_pid)
        assert fresh_descendant.create_time() == descendant_birth
        assert fresh_descendant.status() != psutil.STATUS_ZOMBIE
        assert fresh_descendant.cwd() == str(tmp_path.resolve())
        assert os.getpgid(descendant_pid) == os.getsid(descendant_pid) == process.pid
        assert not forbidden_calls
        evidence["fresh_live_descendant"] = {
            "pid": fresh_descendant.pid,
            "birth": fresh_descendant.create_time(),
            "ppid": fresh_descendant.ppid(),
            "state": fresh_descendant.status(),
            "cwd": fresh_descendant.cwd(),
            "pgid": os.getpgid(descendant_pid),
            "sid": os.getsid(descendant_pid),
        }
        evidence["session_prerequisite_proved"] = native_error is None and not errors
    finally:
        (tmp_path / "release-leader").touch(exist_ok=True)
        (tmp_path / "release-descendant").touch(exist_ok=True)
        try:
            if descendant_pid is not None and descendant_birth is not None:
                deadline = time.monotonic() + 3
                while True:
                    try:
                        terminal_descendant = psutil.Process(descendant_pid)
                        assert terminal_descendant.create_time() == descendant_birth
                        if terminal_descendant.status() == psutil.STATUS_ZOMBIE:
                            evidence["descendant_cleanup_state"] = "zombie"
                            break
                    except psutil.NoSuchProcess:
                        evidence["descendant_cleanup_state"] = "gone"
                        break
                    assert time.monotonic() < deadline, "Owned descendant did not expire within cleanup bound"
                    time.sleep(0.01)
            assert not forbidden_calls
        finally:
            for name, original in methods.items():
                monkeypatch.setattr(process, name, original)
            try:
                final_wait_calls += 1
                final_status = original_wait(timeout=6)
            finally:
                if queue is not None:
                    queue.close()
                evidence["final_wait_calls"] = final_wait_calls
                evidence["final_native_status"] = final_status
                evidence["final_returncode"] = process.returncode
                evidence["queue_closed"] = queue is None or queue.closed
                print("NATIVE_KQUEUE_PREREQUISITE " + json.dumps(evidence, sort_keys=True))
            assert final_wait_calls == 1 and final_status == process.returncode == 17


@pytest.mark.parametrize("close_boundary", ("stdout", "selector"))
def test_close_failure_still_seals_status_and_restores_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, close_boundary: str
) -> None:
    """Owned native close failures cannot skip other teardown or terminal receipt."""
    import selectors
    from types import SimpleNamespace

    import psutil

    if os.name != "posix":
        return
    original_popen = subprocess.Popen
    original_selector = selectors.DefaultSelector
    original_open = Path.open
    log = tmp_path / "observed" / "demo" / "run-1.log"
    logs: list[Any] = []
    children: list[tuple[subprocess.Popen[bytes], Any, Any]] = []
    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    class FailingPipe:
        def __init__(self, native: Any) -> None:
            self.native = native

        def fileno(self) -> int:
            return cast(int, self.native.fileno())

        def close(self) -> None:
            self.native.close()
            raise OSError("controlled stdout close failure")

    def tracked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        handle = original_open(path, *args, **kwargs)
        if path == log:
            logs.append(handle)
        return handle

    def start_child(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original_popen(*args, **kwargs)
        native_pipe = child.stdout
        assert native_pipe is not None
        children.append((child, child.wait, native_pipe))
        deadline = time.monotonic() + 1
        while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.01)
        owned = psutil.Process(child.pid)
        assert owned.status() == psutil.STATUS_ZOMBIE and owned.ppid() == os.getpid()
        assert owned.create_time() > 0 and child.returncode is None
        if close_boundary == "stdout":
            child.stdout = cast(Any, FailingPipe(native_pipe))
        return child

    def create_selector() -> Any:
        selector = original_selector()
        native_close = selector.close

        def fail_close() -> None:
            native_close()
            raise OSError("controlled selector close failure")

        if close_boundary == "selector":
            monkeypatch.setattr(selector, "close", fail_close)
        return selector

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(
        stream_command,
        "subprocess",
        SimpleNamespace(
            Popen=start_child, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired
        ),
    )
    monkeypatch.setattr(
        stream_command,
        "selectors",
        SimpleNamespace(
            DefaultSelector=create_selector, BaseSelector=selectors.BaseSelector, EVENT_READ=selectors.EVENT_READ
        ),
    )
    # This control isolates finalization from the separately retained kernel
    # EPERM finding; its sole native child is already positively zombie.
    monkeypatch.setattr(stream_command, "_terminate_process_group", lambda _child, _grace: None)
    failure: OSError | None = None
    returncode: int | None = None
    try:
        try:
            returncode = stream_command.stream_command(
                [sys.executable, "-c", "import os; os._exit(17)"],
                log,
                observed_root=tmp_path / "observed",
                label="demo",
                run_id="run-1",
                quiet=True,
                cwd=tmp_path,
            )
        except OSError as error:
            failure = error
        after_handlers = {signum: signal.getsignal(signum) for signum in handlers}
        logs_closed = bool(logs) and all(handle.closed for handle in logs)
        status = _current_status(tmp_path)
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        for child, native_wait, native_pipe in children:
            if child.returncode is None:
                native_wait(timeout=3)
            native_pipe.close()
        for handle in logs:
            handle.close()

    assert failure is None, "Native close failure must become a failed observer result"
    assert returncode == 125
    assert after_handlers == handlers
    assert logs_closed
    assert status["state"] == "failed" and status["exit_code"] == 125
    assert status["termination_reason"] == f"observer-{close_boundary}-close-failed"


def test_fast_exited_posix_leader_is_retained_until_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-zombie private child still needs cleanup before its sole reap."""
    from types import SimpleNamespace

    import psutil

    if os.name != "posix":
        return
    assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    original_popen = subprocess.Popen
    original_cleanup = stream_command._terminate_process_group
    children: list[subprocess.Popen[bytes]] = []
    original_methods: list[tuple[Any, Any]] = []
    reaps: list[tuple[str, bool, int | None]] = []
    cleaned: list[int] = []
    cleanup_finished = False

    def start_already_zombie(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original_popen(*args, **kwargs)
        children.append(child)
        native_wait = child.wait
        native_poll = child.poll
        original_methods.append((native_wait, native_poll))
        deadline = time.monotonic() + 2
        while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.01)
        owned = psutil.Process(child.pid)
        if owned.status() != psutil.STATUS_ZOMBIE or owned.ppid() != os.getpid():
            raise OSError("Fast owned child did not reach positive native zombie state")
        assert owned.create_time() > 0 and child.returncode is None

        def record_poll() -> int | None:
            reaps.append(("poll", cleanup_finished, child.returncode))
            return native_poll()

        def record_wait(*wait_args: Any, **wait_kwargs: Any) -> int:
            reaps.append(("wait", cleanup_finished, child.returncode))
            return native_wait(*wait_args, **wait_kwargs)

        monkeypatch.setattr(child, "poll", record_poll)
        monkeypatch.setattr(child, "wait", record_wait)
        return child

    def record_cleanup(child: subprocess.Popen[bytes], grace: float) -> Any:
        nonlocal cleanup_finished
        cleaned.append(child.pid)
        try:
            return original_cleanup(child, grace)
        finally:
            cleanup_finished = True

    monkeypatch.setattr(
        stream_command,
        "subprocess",
        SimpleNamespace(
            Popen=start_already_zombie,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    monkeypatch.setattr(stream_command, "_terminate_process_group", record_cleanup)
    try:
        returncode = stream_command.stream_command(
            [sys.executable, "-c", "import os; os._exit(17)"],
            tmp_path / "observed" / "demo" / "run-1.log",
            observed_root=tmp_path / "observed",
            label="demo",
            run_id="run-1",
            termination_grace_seconds=0.05,
            quiet=True,
            cwd=tmp_path,
        )
    finally:
        for child, (native_wait, native_poll) in zip(children, original_methods, strict=True):
            monkeypatch.setattr(child, "wait", native_wait)
            monkeypatch.setattr(child, "poll", native_poll)
            if child.returncode is None:
                native_wait(timeout=3)
            if child.stdout is not None:
                child.stdout.close()
    assert len(children) == 1
    assert returncode == 17
    assert reaps == [("wait", True, None)]
    assert cleaned == [children[0].pid]
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 17
    assert status["termination_reason"] is None


@pytest.mark.parametrize("post_reap_probe", ("native-live", "permission", "unknown"))
def test_deferred_group_eperm_cannot_hide_a_live_original_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post_reap_probe: str
) -> None:
    """A real live member remains failure after controlled signal refusals."""
    import errno
    from types import SimpleNamespace

    import psutil

    if os.name != "posix":
        return
    descendant_source = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "(root / 'descendant-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-descendant').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    leader_source = (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        f"descendant_source = {descendant_source!r}\n"
        "descendant = subprocess.Popen([sys.executable, '-c', descendant_source, str(root)], "
        "env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "(root / 'leader-ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-leader').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "os._exit(17)\n"
    )
    native_popen = subprocess.Popen
    native_publish = stream_command._StatusPublisher.publish
    children: list[tuple[subprocess.Popen[bytes], Any]] = []
    events: list[tuple[str, int, bool]] = []
    reaped = False
    identities: dict[str, tuple[int, float]] = {}

    def start_child(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = native_popen(*args, **kwargs)
        native_wait = child.wait
        children.append((child, native_wait))

        def wait_once(*wait_args: Any, **wait_kwargs: Any) -> int:
            nonlocal reaped
            events.append(("wait", 0, reaped))
            result = native_wait(*wait_args, **wait_kwargs)
            reaped = True
            events.append(("reaped", result, reaped))
            return result

        monkeypatch.setattr(child, "wait", wait_once)
        return child

    def release_after_live_binding(publisher: Any, payload: dict[str, object]) -> None:
        native_publish(publisher, payload)
        if payload["state"] != "running" or identities:
            return
        deadline = time.monotonic() + 3
        while not (tmp_path / "descendant-ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        leader_pid = int((tmp_path / "leader-ready").read_text())
        descendant_pid = int((tmp_path / "descendant-ready").read_text())
        assert leader_pid == payload["child_pid"]
        for role, pid in (("leader", leader_pid), ("descendant", descendant_pid)):
            owned = psutil.Process(pid)
            assert owned.status() != psutil.STATUS_ZOMBIE
            assert owned.cwd() == str(tmp_path.resolve())
            assert os.getpgid(pid) == os.getsid(pid) == leader_pid
            assert owned.ppid() == (os.getpid() if role == "leader" else leader_pid)
            identities[role] = (pid, owned.create_time())
        (tmp_path / "release-leader").touch()

    class ControlledOS:
        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def killpg(self, pid: int, signum: int) -> None:
            assert pid == identities["leader"][0]
            events.append(("group", int(signum), reaped))
            if signum != 0:
                assert not reaped
                raise PermissionError(errno.EPERM, "controlled group signal refusal")
            assert reaped
            if post_reap_probe == "permission":
                raise PermissionError(errno.EPERM, "controlled post-reap observation refusal")
            if post_reap_probe == "unknown":
                raise OSError(errno.EIO, "controlled post-reap observation error")
            os.killpg(pid, 0)

        def kill(self, pid: int, signum: int) -> None:
            assert pid == identities["leader"][0] and not reaped
            zombie = psutil.Process(pid)
            assert zombie.create_time() == identities["leader"][1]
            assert zombie.status() == psutil.STATUS_ZOMBIE and zombie.ppid() == os.getpid()
            events.append(("pid", int(signum), reaped))
            os.kill(pid, signum)

    monkeypatch.setattr(
        stream_command,
        "subprocess",
        SimpleNamespace(
            Popen=start_child, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired
        ),
    )
    monkeypatch.setattr(stream_command, "os", ControlledOS())
    monkeypatch.setattr(stream_command._StatusPublisher, "publish", release_after_live_binding)
    # A census omission must never make EPERM into absence; the real native
    # group-zero observation still sees the positively bound live descendant.
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(()))
    try:
        returncode = stream_command.stream_command(
            [sys.executable, "-c", leader_source, str(tmp_path)],
            tmp_path / "observed" / "demo" / "run-1.log",
            observed_root=tmp_path / "observed",
            label="demo",
            run_id="run-1",
            termination_grace_seconds=0.05,
            quiet=True,
            cwd=tmp_path,
        )
        descendant_pid, descendant_birth = identities["descendant"]
        fresh = psutil.Process(descendant_pid)
        live_at_return = (
            fresh.create_time() == descendant_birth
            and fresh.status() != psutil.STATUS_ZOMBIE
            and fresh.cwd() == str(tmp_path.resolve())
            and os.getpgid(descendant_pid) == os.getsid(descendant_pid) == identities["leader"][0]
        )
    finally:
        (tmp_path / "release-leader").touch(exist_ok=True)
        (tmp_path / "release-descendant").touch(exist_ok=True)
        for child, native_wait in children:
            if child.returncode is None:
                native_wait(timeout=1)
            if child.stdout is not None:
                child.stdout.close()
        if "descendant" in identities:
            pid, birth = identities["descendant"]
            deadline = time.monotonic() + 6
            while True:
                try:
                    remaining = psutil.Process(pid)
                    assert remaining.create_time() == birth
                    if remaining.status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.ZombieProcess:
                    raise
                except psutil.NoSuchProcess:
                    break
                assert time.monotonic() < deadline, "Owned fixture did not expire naturally"
                time.sleep(0.01)

    assert returncode == 125 and live_at_return
    assert [item for item in events if item[0] == "reaped"] == [("reaped", 17, True)]
    after_reap = events[events.index(("reaped", 17, True)) + 1 :]
    assert after_reap and all(kind == "group" and signum == 0 for kind, signum, _flag in after_reap)
    assert any(kind == "group" and signum == 0 and flag for kind, signum, flag in events)
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 125
    assert status["termination_reason"] == "observer-process-group-cleanup-failed"


def test_failed_leader_with_closed_pipes_drains_positively_owned_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal leader and EOF cannot certify its still-live owned session."""
    import psutil

    child_source = (
        "import json, os, signal, sys, time\n"
        "from pathlib import Path\n"
        "import psutil\n"
        "root = Path(sys.argv[1])\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "record = {'pid': os.getpid(), 'born': psutil.Process().create_time(), "
        "'pgid': os.getpgid(0), 'sid': os.getsid(0), 'cwd': os.getcwd()}\n"
        "(root / 'descendant.json').write_text(json.dumps(record))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-leader').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "time.sleep(1.5)\n"
    )
    leader_source = (
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "import psutil\n"
        "root = Path(sys.argv[1])\n"
        f"child_source = {child_source!r}\n"
        "child_environment = os.environ.copy()\n"
        "subprocess.Popen([sys.executable, '-c', child_source, str(root)], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_environment)\n"
        "record = {'pid': os.getpid(), 'born': psutil.Process().create_time(), "
        "'pgid': os.getpgid(0), 'sid': os.getsid(0), 'cwd': os.getcwd()}\n"
        "(root / 'leader.json').write_text(json.dumps(record))\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / 'release-leader').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "print('owned leader exits17 with closed descendant pipes', flush=True)\n"
        "os._exit(17)\n"
    )
    positively_owned: list[psutil.Process] = []

    def live_owned() -> list[psutil.Process]:
        live: list[psutil.Process] = []
        for process in positively_owned:
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    live.append(process)
            except psutil.NoSuchProcess:
                continue
        return live

    original_publish = stream_command._StatusPublisher.publish

    def publish_with_identity_barrier(publisher: stream_command._StatusPublisher, payload: dict[str, object]) -> None:
        original_publish(publisher, payload)
        if payload["state"] != "running" or positively_owned:
            return
        deadline = time.monotonic() + 3
        while not (tmp_path / "descendant.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        leader = cast(dict[str, object], json.loads((tmp_path / "leader.json").read_text()))
        descendant = cast(dict[str, object], json.loads((tmp_path / "descendant.json").read_text()))
        leader_pid = payload["child_pid"]
        assert type(leader_pid) is int and leader_pid > 1
        assert leader["pid"] == leader_pid
        for record in (leader, descendant):
            pid = record["pid"]
            assert type(pid) is int and pid > 1
            process = psutil.Process(pid)
            assert process.create_time() == record["born"]
            assert process.status() != psutil.STATUS_ZOMBIE
            assert process.cwd() == str(tmp_path.resolve()) == record["cwd"]
            assert os.getpgid(pid) == leader_pid == record["pgid"]
            assert os.getsid(pid) == leader_pid == record["sid"]
            positively_owned.append(process)
        (tmp_path / "release-leader").touch()

    monkeypatch.setattr(stream_command._StatusPublisher, "publish", publish_with_identity_barrier)
    live_at_return: list[int] = []
    try:
        returncode = stream_command.stream_command(
            [sys.executable, "-c", leader_source, str(tmp_path)],
            tmp_path / "observed" / "demo" / "run-1.log",
            observed_root=tmp_path / "observed",
            label="demo",
            run_id="run-1",
            max_seconds=8,
            quiet_seconds=0,
            quiet=True,
            termination_grace_seconds=0.05,
            environment=os.environ.copy(),
            cwd=tmp_path,
        )
        live_at_return = [process.pid for process in live_owned()]
    finally:
        # No teardown signal or guessed PID: the owned child expires naturally.
        (tmp_path / "release-leader").touch(exist_ok=True)
        deadline = time.monotonic() + 6
        remaining = positively_owned
        while remaining and time.monotonic() < deadline:
            remaining = live_owned()
            if remaining:
                time.sleep(0.02)
        assert not remaining, "self-expiring owned fixture did not reach native terminal state"
    assert len(positively_owned) == 2
    assert returncode == 17
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 17
    assert status["termination_reason"] is None
    assert Path(status["log_path"]).read_text() == "owned leader exits17 with closed descendant pipes\n"
    assert not live_at_return, f"positively owned descendants survived observer terminal: {live_at_return}"


def test_observer_records_child_signal_with_shell_exit_semantics(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"), capture_output=True, check=False
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 128 + signal.SIGTERM
    assert status["state"] == "interrupted"
    assert status["termination_reason"] == "child-signal:SIGTERM"


@pytest.mark.parametrize("contradiction", ("wrong-ppid", "changed-birth", "consumed-status"))
def test_positive_child_identity_contradiction_cannot_authorize_numeric_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contradiction: str
) -> None:
    """A positive contradiction cannot authorize actual numeric signal/reap boundaries."""
    from types import SimpleNamespace

    import psutil

    if os.name != "posix":
        return
    operations: list[str] = []
    samples = [0]
    pipes: list[Any] = []

    class PrivateChild:
        pid = 1_000_000_001

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
            pipes.append(self.stdout)
            self.returncode = 17 if contradiction == "consumed-status" else None

        def wait(self, *, timeout: float | None = None) -> int:
            if self.returncode is None:
                operations.append("numeric-wait")
                self.returncode = 17
            return self.returncode

    class ControlledOS:
        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        def killpg(self, pid: int, signum: int) -> None:
            assert pid == PrivateChild.pid
            operations.append("numeric-killpg")
            raise ProcessLookupError("controlled absent group after boundary capture")

        def kill(self, pid: int, signum: int) -> None:
            assert pid == PrivateChild.pid
            operations.append("numeric-kill")

    def fresh_identity(pid: int) -> object:
        assert pid == PrivateChild.pid
        samples[0] += 1
        birth = 100.0 + (contradiction == "changed-birth" and samples[0] > 1)
        return SimpleNamespace(
            create_time=lambda: birth,
            ppid=lambda: os.getpid() + (contradiction == "wrong-ppid"),
            status=lambda: psutil.STATUS_ZOMBIE,
        )

    controlled_psutil = SimpleNamespace(
        Process=fresh_identity,
        process_iter=lambda: iter(()),
        Error=psutil.Error,
        ZombieProcess=psutil.ZombieProcess,
        NoSuchProcess=psutil.NoSuchProcess,
        STATUS_ZOMBIE=psutil.STATUS_ZOMBIE,
    )
    monkeypatch.setattr(
        stream_command,
        "subprocess",
        SimpleNamespace(
            Popen=PrivateChild, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired
        ),
    )
    monkeypatch.setattr(stream_command, "os", ControlledOS())
    monkeypatch.setattr(stream_command, "psutil", controlled_psutil, raising=False)
    try:
        with monkeypatch.context() as local:
            local.setitem(sys.modules, "psutil", controlled_psutil)
            returncode = stream_command.stream_command(
                ["controlled-no-native-child"],
                tmp_path / "observed" / "demo" / "run-1.log",
                observed_root=tmp_path / "observed",
                label="demo",
                run_id="run-1",
                quiet=True,
            )
    finally:
        for pipe in pipes:
            pipe.close()

    assert returncode == 125
    assert not operations, "Positive contradiction must refuse numeric signal/native-reap boundaries"
    if contradiction == "changed-birth":
        assert samples[0] >= 2
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 125
    assert status["termination_reason"] == "observer-child-inspection-failed"


@pytest.mark.parametrize("failure_kind", ("census", "member", "identity", "other-errno"))
def test_group_absence_never_clears_an_independent_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    """Native absence can reconcile group EPERM only, never another error."""
    import errno
    from types import SimpleNamespace

    failure = OSError(errno.EACCES, "controlled " + failure_kind + " failure")
    group_error = PermissionError(errno.EPERM, "controlled original group refusal")
    cleanup = stream_command._PosixGroupCleanup((), (group_error,), failure)
    observations: list[int] = []

    def native_absence(_pid: int, signum: int) -> None:
        observations.append(signum)
        assert signum == 0
        raise ProcessLookupError(errno.ESRCH, "controlled group absence")

    monkeypatch.setattr(stream_command, "os", SimpleNamespace(killpg=native_absence))
    with pytest.raises(OSError) as caught:
        stream_command._finish_posix_group_cleanup(1_000_000_001, cleanup, 0.05, reaped=True)
    assert caught.value is failure
    assert not observations


@pytest.mark.parametrize("terminal_outcome", ("absent", "unknown"))
def test_transient_post_reap_eperm_is_silent_only_after_verified_absence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], terminal_outcome: str
) -> None:
    """A potentially resolved observation is buffered without losing real failures."""
    import errno
    from types import SimpleNamespace

    pid = 1_000_000_001
    original_context = (
        f"original group operation=os.killpg pid={pid} signal=15 errno=1 deferred_from=before-reap",
        f"original group operation=os.killpg pid={pid} signal=9 errno=1 deferred_from=before-reap",
    )
    group_error = PermissionError(errno.EPERM, "controlled original group refusal")
    cleanup = stream_command._PosixGroupCleanup((), (group_error,), None, original_context)
    calls: list[tuple[int, int]] = []

    def controlled_native_probe(actual_pid: int, signum: int) -> None:
        assert actual_pid == pid and signum == 0
        calls.append((actual_pid, signum))
        if len(calls) == 1:
            raise PermissionError(errno.EPERM, "controlled transient post-reap permission result")
        assert len(calls) == 2, "A terminal native outcome must stop the bounded probe"
        if terminal_outcome == "absent":
            raise ProcessLookupError(errno.ESRCH, "controlled native absence")
        raise OSError(errno.EIO, "controlled unknown observation failure")

    monkeypatch.setattr(stream_command, "os", SimpleNamespace(killpg=controlled_native_probe))
    if terminal_outcome == "absent":
        stream_command._finish_posix_group_cleanup(pid, cleanup, 0.05, reaped=True)
        assert capsys.readouterr().err == ""
    else:
        with pytest.raises(OSError) as caught:
            stream_command._finish_posix_group_cleanup(pid, cleanup, 0.05, reaped=True)
        assert caught.value.errno == errno.EIO
        stderr = capsys.readouterr().err
        assert all(context in stderr for context in original_context)
        assert "signal=0 errno=1" in stderr and "signal=0 errno=5" in stderr
    assert calls == [(pid, 0), (pid, 0)]


def test_unreaped_child_never_authorizes_a_post_reap_group_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed final wait leaves group EPERM unresolved without another signal."""
    import errno
    from types import SimpleNamespace

    group_error = PermissionError(errno.EPERM, "controlled original group refusal")
    cleanup = stream_command._PosixGroupCleanup((), (group_error,), None)
    observations: list[int] = []

    def forbidden_observation(_pid: int, signum: int) -> None:
        observations.append(signum)
        raise AssertionError("No post-reap observation exists before a successful final wait")

    monkeypatch.setattr(stream_command, "os", SimpleNamespace(killpg=forbidden_observation))
    with pytest.raises(OSError) as caught:
        stream_command._finish_posix_group_cleanup(1_000_000_001, cleanup, 0.05, reaped=False)
    assert caught.value is group_error
    assert not observations


def test_unavailable_child_inspection_still_cleans_the_private_pinned_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unavailable inspection differs from positive foreign identity."""
    import psutil

    if os.name != "posix":
        return
    original_cleanup = stream_command._terminate_process_group
    children: list[tuple[subprocess.Popen[bytes], float, Any]] = []
    cleanup_pids: list[int] = []

    def unavailable_state(child: subprocess.Popen[bytes], _birth: float | None) -> tuple[float, bool]:
        fresh = psutil.Process(child.pid)
        assert fresh.ppid() == os.getpid() and child.returncode is None
        children.append((child, fresh.create_time(), child.wait))
        raise psutil.AccessDenied(pid=child.pid)

    def record_cleanup(child: subprocess.Popen[bytes], grace: float) -> Any:
        cleanup_pids.append(child.pid)
        return original_cleanup(child, grace)

    monkeypatch.setattr(stream_command, "_posix_child_state", unavailable_state)
    monkeypatch.setattr(stream_command, "_terminate_process_group", record_cleanup)
    try:
        returncode = stream_command.stream_command(
            [sys.executable, "-c", "import time; time.sleep(1.5)"],
            tmp_path / "observed" / "demo" / "run-1.log",
            observed_root=tmp_path / "observed",
            label="demo",
            run_id="run-1",
            termination_grace_seconds=0.05,
            quiet=True,
            cwd=tmp_path,
        )
        assert len(children) == 1
        child, _birth, _native_wait = children[0]
        with pytest.raises(psutil.NoSuchProcess):
            psutil.Process(child.pid)
    finally:
        for child, _birth, native_wait in children:
            if child.returncode is None:
                native_wait(timeout=3)
            if child.stdout is not None:
                child.stdout.close()

    assert returncode == 125 and cleanup_pids == [children[0][0].pid]
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 125
    assert status["termination_reason"] == "observer-child-inspection-failed"


@pytest.mark.parametrize("failed_group_signal", (None, signal.SIGTERM, signal.SIGKILL))
def test_posix_group_cleanup_keeps_leader_unreaped_through_last_signal(
    monkeypatch: pytest.MonkeyPatch, failed_group_signal: signal.Signals | None
) -> None:
    """Native group cleanup cannot delegate to a Popen method that polls/reaps."""
    from types import SimpleNamespace

    import psutil

    events: list[tuple[str, object]] = []
    clock = [0.0]

    class OwnedProcess:
        pid = 999_999_999
        returncode: int | None = None

        def forbidden(self, *_args: object, **_kwargs: object) -> None:
            events.append(("forbidden-popen", self.returncode))
            raise AssertionError("Owned Popen must remain unreaped until all group operations finish")

        poll = wait = terminate = kill = forbidden

    process = OwnedProcess()

    def signal_group(pid: int, signum: signal.Signals) -> None:
        assert pid == process.pid and process.returncode is None
        events.append(("group", signum))
        if signum == failed_group_signal:
            raise PermissionError("controlled group refusal")

    def signal_leader(pid: int, signum: signal.Signals) -> None:
        assert pid == process.pid and process.returncode is None
        events.append(("leader-fallback", signum))

    def grace_sleep(seconds: float) -> None:
        events.append(("grace", seconds))
        clock[0] += seconds

    def forbidden_member_wait(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Native member drain must observe without reaping")

    monkeypatch.setattr(stream_command, "os", SimpleNamespace(name="posix", killpg=signal_group, kill=signal_leader))
    monkeypatch.setattr(stream_command, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=grace_sleep))
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(()))
    monkeypatch.setattr(psutil, "wait_procs", forbidden_member_wait)

    cleanup = stream_command._terminate_process_group(cast(Any, process), 0.1)
    assert cleanup is not None
    if failed_group_signal is None:
        assert cleanup.failure is None and not cleanup.group_eperm
    else:
        assert isinstance(cleanup.failure, OSError)
        with pytest.raises(OSError, match="controlled group refusal"):
            stream_command._finish_posix_group_cleanup(process.pid, cleanup, 0.1, reaped=False)

    assert process.returncode is None
    assert not any(kind == "forbidden-popen" for kind, _value in events)
    term_index = events.index(("group", signal.SIGTERM))
    kill_index = events.index(("group", signal.SIGKILL))
    grace_indexes = [index for index, (kind, _value) in enumerate(events) if kind == "grace"]
    assert grace_indexes and term_index < grace_indexes[0] < kill_index
    assert clock[0] >= 0.1
    if failed_group_signal is not None:
        fallback_index = events.index(("leader-fallback", failed_group_signal))
        group_index = events.index(("group", failed_group_signal))
        assert group_index < fallback_index
        assert all(kind != "group" for kind, _value in events[kill_index + 1 :])


@pytest.mark.parametrize("sigchld_policy", ("ignored", "custom"))
def test_posix_observer_refuses_nondefault_reaper_before_child_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sigchld_policy: str
) -> None:
    """A known competing/automatic-reap policy must not create an owned child."""
    from types import SimpleNamespace

    if os.name != "posix":
        return
    original_getsignal = signal.getsignal
    original_sigchld = original_getsignal(signal.SIGCHLD)

    def custom_handler(_signum: int, _frame: object) -> None:
        return None

    policy = signal.SIG_IGN if sigchld_policy == "ignored" else custom_handler
    child_starts: list[str] = []

    def forbidden_child_start(*_args: object, **_kwargs: object) -> None:
        child_starts.append("attempted")
        raise OSError("Controlled no-child start refused")

    def observed_policy(signum: int) -> object:
        return policy if signum == signal.SIGCHLD else original_getsignal(signum)

    monkeypatch.setattr(signal, "getsignal", observed_policy)
    monkeypatch.setattr(
        stream_command,
        "subprocess",
        SimpleNamespace(Popen=forbidden_child_start, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT),
    )
    returncode = stream_command.stream_command(
        ["controlled-unused-child"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
    )

    assert returncode == 125
    assert not child_starts
    assert original_getsignal(signal.SIGCHLD) is original_sigchld
    status = _current_status(tmp_path)
    assert status["state"] == "failed" and status["exit_code"] == 125
    assert status["termination_reason"] == "observer-child-ownership-refused"


def test_observer_child_start_failure_is_fail_closed(tmp_path: Path) -> None:
    returncode = stream_command.stream_command(
        [str(tmp_path / "missing-executable")],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
    )

    status = _current_status(tmp_path)
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["termination_reason"] == "observer-child-start-failed"


def test_observer_rejects_unsafe_names_and_invalid_durations(tmp_path: Path) -> None:
    for unsafe in ("", ".hidden", "-option", "../escape", "has space"):
        with pytest.raises(ValueError):
            stream_command._safe_name(unsafe, field="run_id")

    with pytest.raises(ValueError, match="command"):
        stream_command.stream_command([], tmp_path / "unused.log")
    with pytest.raises(ValueError, match="durations"):
        stream_command.stream_command([sys.executable, "-c", "pass"], tmp_path / "unused.log", max_seconds=-1)
    with pytest.raises(ValueError, match="grace"):
        stream_command.stream_command(
            [sys.executable, "-c", "pass"], tmp_path / "unused.log", termination_grace_seconds=0
        )
    with pytest.raises(ValueError, match="retain_runs"):
        stream_command.stream_command([sys.executable, "-c", "pass"], tmp_path / "unused.log", retain_runs=0)


@pytest.mark.parametrize(
    ("limit_args", "reason"),
    [
        (("--max-secs", "0.1"), "max-runtime-timeout"),
        (("--quiet-secs", "0.1"), "quiet-output-timeout"),
    ],
)
def test_observer_times_out_the_owned_process_group(tmp_path: Path, limit_args: tuple[str, str], reason: str) -> None:
    completed = subprocess.run(
        _command(tmp_path, "import time; time.sleep(10)", "--heartbeat-secs", "0.02", *limit_args),
        capture_output=True,
        check=False,
        timeout=5,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 124
    assert status["state"] == "timed_out"
    assert status["exit_code"] == 124
    assert status["termination_reason"] == reason


def test_observer_forwards_signals_and_records_interruption(tmp_path: Path) -> None:
    process = subprocess.Popen(
        _command(tmp_path, "import time; time.sleep(10)", "--heartbeat-secs", "0.03", "--max-secs", "30"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    current = tmp_path / "observed" / "demo" / "current.json"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if current.exists():
            status = json.loads(current.read_text(encoding="utf-8"))
            if status["state"] == "running":
                break
        time.sleep(0.01)
    else:
        process.kill()
        raise AssertionError("observer never published running state")

    os.kill(process.pid, signal.SIGTERM)
    process.communicate(timeout=5)
    returncode = process.returncode

    status = _current_status(tmp_path)
    assert returncode == 128 + signal.SIGTERM
    assert status["state"] == "interrupted"
    assert status["exit_code"] == 128 + signal.SIGTERM
    assert status["termination_reason"] == "observer-signal:SIGTERM"


@pytest.mark.parametrize("watched_signal", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("publication_fails", [False, True])
def test_running_publication_requires_armed_and_restored_signal_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watched_signal: signal.Signals, publication_fails: bool
) -> None:
    previous_handler = signal.getsignal(watched_signal)
    published_handlers: list[Any] = []
    published_child_pids: list[int] = []
    original_publish = stream_command._StatusPublisher.publish

    def record_running(publisher: stream_command._StatusPublisher, payload: dict[str, object]) -> None:
        if payload["state"] == "running":
            published_handlers.append(signal.getsignal(watched_signal))
            published_child_pids.append(cast(int, payload["child_pid"]))
            if publication_fails:
                raise OSError("running publication unavailable")
        original_publish(publisher, payload)

    monkeypatch.setattr(stream_command._StatusPublisher, "publish", record_running)
    returncode = stream_command.stream_command(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
    )

    assert signal.getsignal(watched_signal) is previous_handler
    assert returncode == (125 if publication_fails else 0)
    assert len(published_child_pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(published_child_pids[0], 0)
    assert len(published_handlers) == 1
    assert callable(published_handlers[0])
    assert published_handlers[0] is not previous_handler


@pytest.mark.parametrize("watched_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_received_at_running_publication_is_sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watched_signal: signal.Signals
) -> None:
    previous_handler = signal.getsignal(watched_signal)
    original_publish = stream_command._StatusPublisher.publish

    def interrupt_running(publisher: stream_command._StatusPublisher, payload: dict[str, object]) -> None:
        original_publish(publisher, payload)
        if payload["state"] == "running":
            handler = signal.getsignal(watched_signal)
            if callable(handler) and handler is not previous_handler:
                handler(watched_signal, None)

    monkeypatch.setattr(stream_command._StatusPublisher, "publish", interrupt_running)
    returncode = stream_command.stream_command(
        [sys.executable, "-c", "import time; time.sleep(0.05)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
    )

    status = _current_status(tmp_path)
    assert signal.getsignal(watched_signal) is previous_handler
    assert returncode == 128 + watched_signal
    assert status["state"] == "interrupted"
    assert status["exit_code"] == returncode
    assert status["termination_reason"] == f"observer-signal:{watched_signal.name}"
    with pytest.raises(ProcessLookupError):
        os.kill(status["child_pid"], 0)


def test_observer_log_open_failure_is_fail_closed(tmp_path: Path) -> None:
    blocked_log = tmp_path / "blocked.log"
    blocked_log.mkdir()
    completed = subprocess.run(
        _command(tmp_path, "print('must not run')", "--log", str(blocked_log)), capture_output=True, check=False
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-log-open-failed"


def test_observer_status_write_failure_terminates_child_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_publish = stream_command._StatusPublisher.publish
    publish_count = 0

    def fail_heartbeat_publish(publisher: stream_command._StatusPublisher, payload: dict[str, object]) -> None:
        nonlocal publish_count
        publish_count += 1
        if publish_count >= 3:
            raise OSError("status volume unavailable")
        original_publish(publisher, payload)

    monkeypatch.setattr(stream_command._StatusPublisher, "publish", fail_heartbeat_publish)

    returncode = stream_command.stream_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        heartbeat_seconds=0.02,
        quiet=True,
    )

    assert returncode == 125
    assert publish_count >= 3


def test_observer_log_write_failure_terminates_child_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_chunk: bytes, _log_file: object, *, quiet: bool) -> None:
        raise OSError("log volume unavailable")

    monkeypatch.setattr(stream_command, "_write_chunk", fail_write)

    returncode = stream_command.stream_command(
        [
            sys.executable,
            "-c",
            "import time; print('output', flush=True); time.sleep(10)",
        ],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        heartbeat_seconds=0.02,
        quiet=True,
    )

    status = _current_status(tmp_path)
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-output-write-failed"


def test_pytest_trace_path_and_run_id_are_exported_to_child(tmp_path: Path) -> None:
    child = (
        "import os,pathlib; "
        "pathlib.Path(os.environ['GLUDD_XDIST_TRACE_LOG']).write_text("
        "os.environ['GLUDD_XDIST_TRACE_RUN_ID'], encoding='utf-8')"
    )
    completed = subprocess.run(_command(tmp_path, child, "--pytest-trace"), capture_output=True, check=False)

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert status["trace_path"] is not None
    assert Path(status["trace_path"]).read_text(encoding="utf-8") == "run-1"


def test_status_infers_orphaned_running_owner(tmp_path: Path) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    payload = {
        "state": "running",
        "owner_pid": 999_999_999,
        "updated_at": "2000-01-01T00:00:00+00:00",
        "log_path": str(label_dir / "run.log"),
    }
    (label_dir / "current.json").write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(STREAM_RUNNER),
            "--status",
            "--root",
            str(tmp_path / "observed"),
            "--label",
            "demo",
            "--stale-secs",
            "0.01",
        ],
        capture_output=True,
        check=False,
    )

    status = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert status["state"] == "running"
    assert status["effective_state"] == "orphaned"
    assert status["active"] is False
    assert status["stale"] is True


def test_status_distinguishes_live_fresh_and_live_stale_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    current = label_dir / "current.json"
    payload = {
        "state": "running",
        "owner_pid": os.getpid(),
        "updated_at": "2000-01-01T00:00:00",
        "log_path": str(label_dir / "run.log"),
    }
    current.write_text(json.dumps(payload), encoding="utf-8")

    assert stream_command.observed_status(tmp_path / "observed", "demo", stale_seconds=0.01) == 3
    stale = json.loads(capsys.readouterr().out)
    assert stale["effective_state"] == "stale"
    assert stale["active"] is False

    payload["updated_at"] = stream_command._utc_now()
    current.write_text(json.dumps(payload), encoding="utf-8")
    assert stream_command.observed_status(tmp_path / "observed", "demo", stale_seconds=90) == 0
    fresh = json.loads(capsys.readouterr().out)
    assert fresh["effective_state"] == "running"
    assert fresh["active"] is True


def test_status_and_tail_helpers_reject_invalid_inputs(tmp_path: Path) -> None:
    assert stream_command._pid_alive(None) is False
    assert stream_command._age_seconds(None) == float("inf")
    assert stream_command._age_seconds("not-a-timestamp") == float("inf")

    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and 1000"):
        stream_command.observed_tail(tmp_path / "observed", "demo", lines=0)
    with pytest.raises(ValueError, match="log_path"):
        stream_command.observed_tail(tmp_path / "observed", "demo", lines=1)


def test_retention_prunes_only_superseded_terminal_run_artifacts(tmp_path: Path) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text("{}", encoding="utf-8")

    for index, state in enumerate(("passed", "failed", "running"), start=1):
        run_id = f"run-{index}"
        log_path = label_dir / f"{run_id}.log"
        trace_path = label_dir / f"{run_id}.pytest.jsonl"
        log_path.write_text("log", encoding="utf-8")
        trace_path.write_text("trace", encoding="utf-8")
        (label_dir / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "observed_command",
                    "label": "demo",
                    "run_id": run_id,
                    "state": state,
                    "started_at": f"2026-09-02T00:00:0{index}+00:00",
                    "log_path": str(log_path),
                    "trace_path": str(trace_path),
                }
            ),
            encoding="utf-8",
        )

    malformed_log = label_dir / "run-malformed.log"
    malformed_log.write_text("preserve", encoding="utf-8")
    (label_dir / "run-malformed.json").write_text(
        json.dumps(
            {
                "run_id": "run-malformed",
                "state": "passed",
                "started_at": "2000-01-01T00:00:00+00:00",
                "log_path": str(malformed_log),
            }
        ),
        encoding="utf-8",
    )

    stream_command._prune_terminal_runs(label_dir, retain_runs=1)

    assert not (label_dir / "run-1.json").exists()
    assert not (label_dir / "run-1.log").exists()
    assert not (label_dir / "run-1.pytest.jsonl").exists()
    for run_id in ("run-2", "run-3"):
        assert (label_dir / f"{run_id}.json").exists()
        assert (label_dir / f"{run_id}.log").exists()
        assert (label_dir / f"{run_id}.pytest.jsonl").exists()
    assert (label_dir / "run-malformed.json").exists()
    assert malformed_log.exists()
    assert (label_dir / "current.json").exists()


def test_observer_prunes_to_retention_bound_after_terminal_publish(tmp_path: Path) -> None:
    for index in range(1, 4):
        completed = subprocess.run(
            _command(tmp_path, f"print('run-{index}', flush=True)", "--retain-runs", "2", run_id=f"run-{index}"),
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0

    label_dir = tmp_path / "observed" / "demo"
    assert {path.name for path in label_dir.glob("run-*.json")} == {
        "run-2.json",
        "run-3.json",
    }
    assert {path.name for path in label_dir.glob("run-*.log")} == {
        "run-2.log",
        "run-3.log",
    }
    assert _current_status(tmp_path)["run_id"] == "run-3"


def test_status_and_tail_address_retained_terminal_run_after_current_advances(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An immutable run ID keeps terminal evidence queryable until rotation."""

    for run_id in ("run-1", "run-2"):
        log_path = (
            tmp_path / "custom-retained.log" if run_id == "run-1" else tmp_path / "observed" / "demo" / f"{run_id}.log"
        )
        returncode = stream_command.stream_command(
            [sys.executable, "-c", f"print('{run_id}', flush=True)"],
            log_path,
            observed_root=tmp_path / "observed",
            label="demo",
            run_id=run_id,
            quiet=True,
            retain_runs=2,
        )
        assert returncode == 0

    capsys.readouterr()
    assert stream_command.observed_status(tmp_path / "observed", "demo", run_id="run-1", stale_seconds=90) == 0
    retained = json.loads(capsys.readouterr().out)
    assert retained["run_id"] == "run-1"
    assert retained["state"] == "passed"

    assert stream_command.observed_tail(tmp_path / "observed", "demo", run_id="run-1", lines=1) == 0
    assert capsys.readouterr().out == "run-1\n"


def test_current_run_id_alias_resolves_payload_identity_and_preserves_exact_lookup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The explicit current alias resolves the real run without weakening exact IDs."""

    for run_id in ("run-1", "run-2"):
        assert (
            stream_command.stream_command(
                [sys.executable, "-c", "pass"],
                tmp_path / "observed" / "demo" / f"{run_id}.log",
                observed_root=tmp_path / "observed",
                label="demo",
                run_id=run_id,
                quiet=True,
                retain_runs=2,
            )
            == 0
        )

    capsys.readouterr()
    for requested_run_id, expected_run_id in (("current", "run-2"), ("run-1", "run-1")):
        assert (
            stream_command.main(
                [
                    "--status",
                    "--root",
                    str(tmp_path / "observed"),
                    "--label",
                    "demo",
                    "--run-id",
                    requested_run_id,
                    "--stale-secs",
                    "90",
                ]
            )
            == 0
        )
        status = json.loads(capsys.readouterr().out)
        assert status["run_id"] == expected_run_id


def test_current_run_id_alias_rejects_unsafe_payload_run_identity(tmp_path: Path) -> None:
    """A current pointer cannot use its payload to escape retained-run identity."""

    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "observed_command",
                "label": "demo",
                "run_id": "../escape",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="run_id must be a safe"):
        stream_command._load_status(tmp_path / "observed", "demo", run_id="current")


def test_exact_terminal_lookup_obeys_retention_rotation(tmp_path: Path) -> None:
    """Exact-run readers cannot resurrect artifacts pruned by the bounded policy."""

    for run_id in ("run-1", "run-2"):
        assert (
            stream_command.stream_command(
                [sys.executable, "-c", "pass"],
                tmp_path / "observed" / "demo" / f"{run_id}.log",
                observed_root=tmp_path / "observed",
                label="demo",
                run_id=run_id,
                quiet=True,
                retain_runs=1,
            )
            == 0
        )

    with pytest.raises(FileNotFoundError):
        stream_command.observed_status(tmp_path / "observed", "demo", run_id="run-1", stale_seconds=90)


def test_retention_failure_after_child_exit_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_retention(_directory: Path, *, retain_runs: int) -> None:
        raise OSError(f"retention volume unavailable ({retain_runs})")

    monkeypatch.setattr(stream_command, "_prune_terminal_runs", fail_retention)

    returncode = stream_command.stream_command(
        [sys.executable, "-c", "print('child completed', flush=True)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
        retain_runs=2,
    )

    status = _current_status(tmp_path)
    assert Path(status["log_path"]).read_text(encoding="utf-8") == "child completed\n"
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-retention-failed"


def test_tail_reads_only_a_bounded_snapshot_from_current_run(tmp_path: Path) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    log_path = label_dir / "run.log"
    log_path.write_text("".join(f"line-{index}\n" for index in range(100)), encoding="utf-8")
    (label_dir / "current.json").write_text(json.dumps({"log_path": str(log_path)}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(STREAM_RUNNER),
            "--tail",
            "4",
            "--root",
            str(tmp_path / "observed"),
            "--label",
            "demo",
        ],
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.decode().splitlines() == [
        "line-96",
        "line-97",
        "line-98",
        "line-99",
    ]
