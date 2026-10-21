"""Native agent-watchdog startup, singleton and bounded drain controls."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import kill_owned_gate as cleanup
    import task_watchdog as native
else:
    from scripts import kill_owned_gate as cleanup
    from scripts import task_watchdog as native

OwnedAgent = tuple[Path, list[cleanup.ProcessRecord], dict[int, Path | None], list[tuple[int, int]]]

agent = importlib.import_module("scripts.agent_watchdog")


@pytest.mark.parametrize("arguments", [
    ["--start", "--validate-only", "bad"],
    ["--stop", "--validate-only", "0 1"],
    ["--start", "--stop"],
    ["--validate-only", "0"],
])
def test_lifecycle_rejects_malformed_policy_before_work(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        agent.main(arguments)
    assert stopped.value.code == 2


def test_start_plan_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/agent_watchdog.py").write_text("# owned fixture\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_WORKSPACE", root)
    monkeypatch.setattr(native, "_repository_roots", lambda: (root,))
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    monkeypatch.setattr(agent, "scan_tasks_dir", lambda _path: [])
    assert agent.main(["--start", "--validate-only", "1"]) == 0
    assert "plan writes=false spawn=false signals=false" in capsys.readouterr().out
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "checkout", "checkout/scripts", "checkout/scripts/agent_watchdog.py",
    ]


def _confined_source(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "native-agent"
    (root / "scripts").mkdir(parents=True)
    owner = Path(__file__).resolve().parents[2]
    source = (owner / "scripts/agent_watchdog.py").read_text(encoding="utf-8")
    injection = (
        'if __name__ == "__main__":\n'
        '    from scripts import task_watchdog as _fixture_native\n'
        '    _fixture_native._repository_roots = lambda: (_WORKSPACE,)\n'
        '    _log = lambda message: print(message, flush=True)\n'
        '    HIBERNATION_MARKER = _WORKSPACE / "hibernating"\n'
        '    POLL_SECS = 0.02\n'
        '    for _name in ("_check_plugin_liveness_on_startup", "check_and_reset",\n'
        '                  "_check_force_dispatch", "check_running_tasks", "check_push_status",\n'
        '                  "_check_gate_background", "_check_load_average",\n'
        '                  "_check_plugin_liveness_periodic", "_rotate_watchdog_logs"):\n'
        '        globals()[_name] = lambda: None\n'
        '    _fixture_deadline = time.monotonic() + 12\n'
        '    def _bounded_check():\n'
        '        if time.monotonic() >= _fixture_deadline:\n'
        '            raise SystemExit(0)\n'
        '    check_and_reset = _bounded_check\n'
    )
    (root / "scripts/agent_watchdog.py").write_text(
        source.replace('if __name__ == "__main__":\n', injection), encoding="utf-8",
    )
    environment = dict(
        os.environ, PYTHONPATH=os.pathsep.join((str(owner), str(owner / "scripts"))),
        GLUDD_WORKSPACE_ROOT=str(root),
        GLUDD_RESOURCE_ROOT=str(tmp_path / "resources"), GLUDD_WATCHDOG_VERSION="1.0",
    )
    return root, environment


def test_native_sigterm_releases_the_actual_lease(tmp_path: Path) -> None:
    root, environment = _confined_source(tmp_path)
    child = subprocess.Popen(
        [sys.executable, str(root / "scripts/agent_watchdog.py")], cwd=root,
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    locks: list[Path] = []
    try:
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            locks = list((tmp_path / "resources").rglob("agent-watchdog.lock"))
            if locks:
                break
            time.sleep(0.02)
        assert child.poll() is None, child.communicate(timeout=1)[1]
        assert len(locks) == 1
        assert json.loads(locks[0].read_text(encoding="utf-8"))["pid"] == child.pid
        os.kill(child.pid, signal.SIGTERM)
        child.wait(timeout=5)
        assert not locks[0].exists(), "SIGTERM must run the native lease owner's finally"
    finally:
        if child.poll() is None:
            child.terminate()
        child.communicate(timeout=5)


@pytest.mark.parametrize("phase", ("fsync", "acquire-return"))
def test_native_sigterm_during_acquisition_releases_the_actual_lease(
    tmp_path: Path, phase: str,
) -> None:
    """Deliver the real signal while the native creator still owns acquisition."""
    root, environment = _confined_source(tmp_path)
    script = root / "scripts/agent_watchdog.py"
    if phase == "fsync":
        instrumentation = (
            "    _fixture_fsync = os.fsync\n"
            "    def _signal_during_fsync(fd):\n"
            "        _fixture_fsync(fd)\n"
            "        os.kill(os.getpid(), signal.SIGTERM)\n"
            "    os.fsync = _signal_during_fsync\n"
        )
    else:
        instrumentation = (
            "    _fixture_acquire = acquire_watchdog_lock\n"
            "    def _signal_before_acquire_returns():\n"
            "        _lease = _fixture_acquire()\n"
            "        os.kill(os.getpid(), signal.SIGTERM)\n"
            "        return _lease\n"
            "    acquire_watchdog_lock = _signal_before_acquire_returns\n"
        )
    original = "    raise SystemExit(main())\n"
    content = script.read_text(encoding="utf-8")
    assert content.count(original) == 1
    script.write_text(content.replace(original, instrumentation + original), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script)], cwd=root, env=environment,
        capture_output=True, text=True, check=False, timeout=12,
    )

    assert not list((tmp_path / "resources").rglob("agent-watchdog.lock")), (
        f"SIGTERM during {phase} retained the native lease: "
        f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / ".gate-logs/watchdog.pid").exists()
    assert "agent_watchdog started:" not in result.stdout


def test_native_start_acknowledges_one_child_and_stop_drains(tmp_path: Path) -> None:
    root, environment = _confined_source(tmp_path)
    command = [sys.executable, str(root / "scripts/agent_watchdog.py")]
    child_pid: int | None = None
    try:
        for policy in ("1", "0", "0", "1"):
            result = subprocess.run(
                [*command, "--start", "--validate-only", policy], cwd=root, env=environment,
                capture_output=True, text=True, check=False, timeout=12,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            if policy == "1" and child_pid is None:
                assert "plan writes=false spawn=false signals=false" in result.stdout
                assert not (root / ".gate-logs").exists()
                continue
            pid = int((root / ".gate-logs/watchdog.pid").read_text(encoding="utf-8"))
            if child_pid is None:
                child_pid = pid
                assert "AGENT-WATCHDOG-START acknowledged" in result.stdout
                assert os.getpgid(pid) == os.getsid(pid) == pid
            else:
                assert pid == child_pid and "already-running" in result.stdout
        plan = subprocess.run(
            [*command, "--stop", "--validate-only", "1"], cwd=root, env=environment,
            capture_output=True, text=True, check=False, timeout=12,
        )
        assert plan.returncode == 0, plan.stdout + plan.stderr
        assert (root / ".gate-logs/watchdog.pid").exists()
    finally:
        stopped = subprocess.run(
            [*command, "--stop", "--validate-only", "0"], cwd=root, env=environment,
            capture_output=True, text=True, check=False, timeout=12,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert child_pid is not None
    assert not (root / ".gate-logs/watchdog.pid").exists()
    assert not list((tmp_path / "resources").rglob("agent-watchdog.lock"))


def test_native_readiness_failure_never_publishes_success(tmp_path: Path) -> None:
    root, environment = _confined_source(tmp_path)
    script = root / "scripts/agent_watchdog.py"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "    POLL_SECS = 0.02",
        "    def _failed_readiness():\n"
        "        raise RuntimeError('isolated readiness failure')\n"
        "    _check_plugin_liveness_on_startup = _failed_readiness\n"
        "    POLL_SECS = 0.02",
    ).replace(
        '        globals()[_name] = lambda: None',
        '        if _name != "_check_plugin_liveness_on_startup":\n'
        '            globals()[_name] = lambda: None',
    ), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "--start", "--validate-only", "0"], cwd=root,
        env=environment, capture_output=True, text=True, check=False, timeout=12,
    )
    assert result.returncode == 1 and "refused:" in result.stdout
    assert "acknowledged" not in result.stdout
    assert not (root / ".gate-logs/watchdog.pid").exists()
    assert not list((tmp_path / "resources").rglob("agent-watchdog.lock"))
    assert "isolated readiness failure" in (root / ".gate-logs/watchdog.log").read_text(encoding="utf-8")


@pytest.fixture
def owned_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OwnedAgent:

    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/agent_watchdog.py").write_text("# native owned fixture\n", encoding="utf-8")
    monkeypatch.setattr(agent, "_WORKSPACE", root)
    monkeypatch.setattr(native, "_repository_roots", lambda: (root,))
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    records = [cleanup.ProcessRecord(
        54001, 54000, 400, "python3 scripts/agent_watchdog.py", pgid=54001, started_at="native-start",
    )]
    cwds: dict[int, Path | None] = {54001: root}
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(cleanup, "_records", lambda _root: list(records))
    monkeypatch.setattr(cleanup, "_process_cwd", lambda pid: cwds.get(pid))
    monkeypatch.setattr(agent, "_process_start_time", lambda _pid: "native-start")
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getsid", lambda pid: pid)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    lock = agent.watchdog_lock_path()
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({
        "pid": 54001, "pid_start_time": "native-start", "started_at": 1.0,
        "version": agent.WATCHDOG_VERSION, "namespace": agent.project_namespace(root),
        "token": "fixture-native-token",
    }), encoding="utf-8")

    def send(pid: int, signum: int) -> None:
        sent.append((pid, signum))
        records[:] = [record for record in records if record.pid != pid]

    monkeypatch.setattr(os, "kill", send)
    return root, records, cwds, sent


@pytest.mark.parametrize("apply", [False, True])
def test_legacy_lease_drains_only_verified_child_then_natural_uv(
    owned_agent: OwnedAgent, monkeypatch: pytest.MonkeyPatch, apply: bool,
) -> None:

    root, records, cwds, sent = owned_agent
    records.insert(0, cleanup.ProcessRecord(
        54000, 1, 400, "uv run python3 scripts/agent_watchdog.py", started_at="uv-start",
    ))
    cwds[54000] = root
    monkeypatch.setattr(time, "sleep", lambda _seconds: records.clear())
    before = agent.watchdog_lock_path().read_bytes()
    assert agent.main(["--stop", "--validate-only", "0" if apply else "1"]) == 0
    assert sent == ([(54001, signal.SIGTERM)] if apply else [])
    assert not agent.watchdog_lock_path().exists() if apply else agent.watchdog_lock_path().read_bytes() == before


@pytest.mark.parametrize("defect", [
    "namespace", "version", "token", "start", "foreign", "unknown-cwd",
    "command", "reused", "corrupt", "symlink", "missing-live", "missing-uv",
])
def test_uncertain_lease_never_authorizes_stop_or_new_start(
    owned_agent: OwnedAgent, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, defect: str,
) -> None:

    _root, records, cwds, sent = owned_agent
    lock = agent.watchdog_lock_path()
    owner = json.loads(lock.read_text(encoding="utf-8"))
    if defect in {"namespace", "version", "token", "start"}:
        owner[{"start": "pid_start_time"}.get(defect, defect)] = "" if defect == "token" else "foreign"
        lock.write_text(json.dumps(owner), encoding="utf-8")
    elif defect in {"foreign", "unknown-cwd"}:
        cwds[54001] = tmp_path if defect == "foreign" else None
    elif defect in {"command", "reused"}:
        records[:] = [cleanup.ProcessRecord(
            54001, 54000, 400, "foreign command" if defect == "command" else records[0].command,
            started_at="reused-start" if defect == "reused" else records[0].started_at,
        )]
        if defect == "reused":
            monkeypatch.setattr(agent, "_process_start_time", lambda _pid: "reused-start")
    elif defect == "corrupt":
        lock.write_text("{invalid", encoding="utf-8")
    elif defect == "symlink":
        foreign = tmp_path / "foreign-lease"
        lock.rename(foreign)
        lock.symlink_to(foreign)
    else:
        lock.unlink()
        if defect == "missing-uv":
            records[:] = [cleanup.ProcessRecord(
                54001, 1, 400, "uv run python3 scripts/agent_watchdog.py", started_at="uv-start",
            )]
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert agent.main(["--stop", "--validate-only", "0"]) == 1
    assert agent.main(["--start", "--validate-only", "1"]) == 1
    assert sent == []
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("change", ["token", "inode", "start", "pointer"])
def test_stop_rechecks_lease_before_first_signal(
    owned_agent: OwnedAgent, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:

    root, records, _cwds, sent = owned_agent
    lock = agent.watchdog_lock_path()
    pointer = root / ".gate-logs/watchdog.pid"
    pointer.parent.mkdir()
    pointer.write_text("54001", encoding="utf-8")
    reads = 0

    def changed(_root: Path) -> list[cleanup.ProcessRecord]:
        nonlocal reads
        reads += 1
        if reads == 2:
            if change == "start":
                monkeypatch.setattr(agent, "_process_start_time", lambda _pid: "reused")
            elif change == "pointer":
                pointer.write_text("54002", encoding="utf-8")
            else:
                content = lock.read_text(encoding="utf-8")
                if change == "inode":
                    lock.unlink()
                lock.write_text(content.replace("fixture-native-token", "replacement") if change == "token"
                                else content, encoding="utf-8")
        return list(records)

    monkeypatch.setattr(cleanup, "_records", changed)
    assert agent.main(["--stop", "--validate-only", "0"]) == 1
    assert sent == [] and lock.exists()


def test_stale_start_requires_explicit_owned_absence_recovery(
    owned_agent: OwnedAgent,
) -> None:
    _root, records, _cwds, sent = owned_agent
    records.clear()
    assert agent.main(["--start", "--validate-only", "1"]) == 1
    assert agent.watchdog_lock_path().exists()
    assert agent.main(["--stop", "--validate-only", "1"]) == 0
    assert agent.watchdog_lock_path().exists()
    assert agent.main(["--stop", "--validate-only", "0"]) == 0
    assert not agent.watchdog_lock_path().exists() and sent == []


def test_real_legacy_uv_supervisor_exits_without_being_signalled(tmp_path: Path) -> None:
    import shutil

    root, environment = _confined_source(tmp_path)
    uv = shutil.which("uv")
    assert uv is not None
    (root / "pyproject.toml").write_text(
        '[project]\nname="gludd-agent-lifecycle-fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\n',
        encoding="utf-8",
    )
    environment.update(UV_PROJECT_ENVIRONMENT=sys.prefix, UV_NO_SYNC="1",
                       UV_PYTHON_DOWNLOADS="never", UV_PYTHON=sys.executable)
    command = [sys.executable, str(root / "scripts/agent_watchdog.py")]
    supervisor_receipt = root / "uv.pid"
    caller_source = (
        "import subprocess\nfrom pathlib import Path\n"
        f"child = subprocess.Popen([{uv!r}, 'run', 'python3', 'scripts/agent_watchdog.py'])\n"
        f"Path({str(supervisor_receipt)!r}).write_text(str(child.pid))\n"
        "raise SystemExit(child.wait(timeout=15))\n"
    )
    caller = subprocess.Popen(
        [sys.executable, "-c", caller_source], cwd=root, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        leases: list[Path] = []
        while time.monotonic() < deadline and caller.poll() is None:
            leases = list((tmp_path / "resources").rglob("agent-watchdog.lock"))
            if leases and supervisor_receipt.exists():
                break
            time.sleep(0.02)
        assert caller.poll() is None, caller.communicate(timeout=1)
        assert len(leases) == 1 and not (root / ".gate-logs/watchdog.pid").exists()
        daemon_pid = json.loads(leases[0].read_text(encoding="utf-8"))["pid"]
        supervisor_pid = int(supervisor_receipt.read_text(encoding="utf-8"))
        result = subprocess.run(
            [*command, "--stop", "--validate-only", "0"], cwd=root, env=environment,
            capture_output=True, text=True, check=False, timeout=12,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"signal=SIGTERM pid={daemon_pid} " in result.stdout
        assert f"signal=SIGTERM pid={supervisor_pid} " not in result.stdout
        assert caller.wait(timeout=3) == 0
        assert not leases[0].exists()
    finally:
        if caller.poll() is None:
            subprocess.run(
                [*command, "--stop", "--validate-only", "0"], cwd=root, env=environment,
                capture_output=True, text=True, check=False, timeout=12,
            )
        caller.communicate(timeout=15)


@pytest.mark.parametrize("defect", ["namespace", "version", "token", "start", "corrupt", "duplicate"])
def test_absent_pid_does_not_authorize_foreign_or_corrupt_lease_removal(
    owned_agent: OwnedAgent, defect: str,
) -> None:
    _root, records, _cwds, sent = owned_agent
    records.clear()
    lock = agent.watchdog_lock_path()
    owner = json.loads(lock.read_text(encoding="utf-8"))
    if defect == "corrupt":
        lock.write_text("{broken", encoding="utf-8")
    elif defect == "duplicate":
        lock.write_text('{"namespace":"foreign",' + json.dumps(owner)[1:], encoding="utf-8")
    else:
        owner[{"start": "pid_start_time"}.get(defect, defect)] = ""
        lock.write_text(json.dumps(owner), encoding="utf-8")
    before = lock.read_bytes()
    assert agent.main(["--stop", "--validate-only", "0"]) == 1
    assert lock.read_bytes() == before and sent == []


def test_surviving_uv_supervisor_refuses_success_without_another_signal(
    owned_agent: OwnedAgent,
) -> None:
    root, records, cwds, sent = owned_agent
    records.insert(0, cleanup.ProcessRecord(
        54000, 1, 400, "uv run python3 scripts/agent_watchdog.py", started_at="uv-start",
    ))
    cwds[54000] = root
    assert agent.main(["--stop", "--validate-only", "0"]) == 1
    assert sent == [(54001, signal.SIGTERM)]
    assert records[0].pid == 54000 and agent.watchdog_lock_path().exists()


def test_signal_handler_setup_failure_releases_acquired_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = importlib.import_module("scripts.watchdog_components.lifecycle")

    monkeypatch.setattr(agent, "_WORKSPACE", tmp_path)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))

    def failed_signal_setup() -> None:
        raise ValueError("isolated signal setup refusal")

    monkeypatch.setattr(lifecycle, "install_termination_handler", failed_signal_setup)
    with pytest.raises(ValueError, match="isolated signal setup refusal"):
        agent.main([])
    assert not agent.watchdog_lock_path().exists()


def test_unacknowledged_lease_cannot_be_adopted(owned_agent: OwnedAgent) -> None:
    _root, _records, _cwds, sent = owned_agent
    before = agent.watchdog_lock_path().read_bytes()
    assert agent.main(["--start", "--validate-only", "1"]) == 1
    assert agent.watchdog_lock_path().read_bytes() == before and sent == []


def test_interrupted_creator_cannot_adopt_a_real_unready_child(tmp_path: Path) -> None:
    root, environment = _confined_source(tmp_path)
    script = root / "scripts/agent_watchdog.py"
    script.write_text(script.read_text(encoding="utf-8").replace(
        "    check_and_reset = _bounded_check",
        "    check_and_reset = _bounded_check\n"
        "    _check_plugin_liveness_on_startup = lambda: time.sleep(4)",
    ), encoding="utf-8")
    command = [sys.executable, str(script)]
    creator = subprocess.Popen(
        [*command, "--start", "--validate-only", "0"], cwd=root, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        leases: list[Path] = []
        while creator.poll() is None and time.monotonic() < deadline:
            leases = list((tmp_path / "resources").rglob("agent-watchdog.lock"))
            if leases:
                break
            time.sleep(0.02)
        assert leases and not (root / ".gate-logs/watchdog.pid").exists()
        creator.terminate()
        creator.communicate(timeout=3)
        adopter = subprocess.run(
            [*command, "--start", "--validate-only", "1"], cwd=root, env=environment,
            capture_output=True, text=True, check=False, timeout=5,
        )
        assert adopter.returncode == 1, adopter.stdout + adopter.stderr
        assert "already-running" not in adopter.stdout
    finally:
        if creator.poll() is None:
            creator.terminate()
        creator.communicate(timeout=3)
        stopped = subprocess.run(
            [*command, "--stop", "--validate-only", "0"], cwd=root, env=environment,
            capture_output=True, text=True, check=False, timeout=12,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


@pytest.mark.parametrize("unready", [None, False, True, 0])
def test_an_old_matching_pid_pointer_is_not_checked_readiness(owned_agent: OwnedAgent, unready: object) -> None:
    root, _records, _cwds, sent = owned_agent
    lock = agent.watchdog_lock_path()
    owner = json.loads(lock.read_text(encoding="utf-8"))
    owner["ready"] = unready
    lock.write_text(json.dumps(owner), encoding="utf-8")
    pointer = root / ".gate-logs/watchdog.pid"
    pointer.parent.mkdir()
    pointer.write_text("54001", encoding="utf-8")
    assert agent.main(["--start", "--validate-only", "1"]) == 1
    assert sent == []


def test_lease_owner_acknowledges_only_its_original_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_owner = importlib.import_module("scripts.watchdog_components.lease")
    monkeypatch.setattr(agent, "_WORKSPACE", tmp_path)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    lease = agent.acquire_watchdog_lock()
    assert lease is not None
    try:
        assert json.loads(lease.path.read_text(encoding="utf-8"))["ready"] == 0
        lease_owner.acknowledge_watchdog(agent._WATCHDOG_RUNTIME, lease)
        assert json.loads(lease.path.read_text(encoding="utf-8"))["ready"] == 1
        content = lease.path.read_bytes()
        lease.path.unlink()
        lease.path.write_bytes(content)
        with pytest.raises(ValueError, match="descriptor"):
            lease_owner.acknowledge_watchdog(agent._WATCHDOG_RUNTIME, lease)
    finally:
        agent.release_watchdog_lock(lease)


@pytest.mark.parametrize("failure", ["partial", "fsync-before", "fsync-after", "interrupted"])
def test_ready_fault_keeps_valid_unready_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    lease_owner = importlib.import_module("scripts.watchdog_components.lease")
    monkeypatch.setattr(agent, "_WORKSPACE", tmp_path)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    lease = agent.acquire_watchdog_lock()
    assert lease is not None
    write = os.pwrite
    fsync = os.fsync
    calls = 0

    def broken_write(fd: int, content: bytes, offset: int) -> int:
        prefix = content[:content.index(b"true") + 1] if len(content) > 1 else b""
        return write(fd, prefix, offset)

    def broken_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == (2 if failure in {"fsync-after", "interrupted"} else 1):
            if failure == "interrupted":
                raise SystemExit(7)
            raise OSError("isolated readiness fsync failure")
        fsync(fd)

    try:
        with monkeypatch.context() as fault:
            if failure == "partial":
                fault.setattr(os, "pwrite", broken_write)
            else:
                fault.setattr(os, "fsync", broken_fsync)
            with pytest.raises((OSError, SystemExit)):
                lease_owner.acknowledge_watchdog(agent._WATCHDOG_RUNTIME, lease)
        owner = json.loads(lease.path.read_text(encoding="utf-8"))
        assert owner["ready"] in (False, 0) and owner["token"] == lease.token
    finally:
        agent.release_watchdog_lock(lease)


def test_native_resource_alias_does_not_hide_a_verified_lease(
    owned_agent: OwnedAgent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, _records, _cwds, sent = owned_agent
    original = agent.watchdog_lock_path()
    target = tmp_path / "native-tmp"
    target.mkdir()
    alias = tmp_path / "native-tmp-alias"
    alias.symlink_to(target, target_is_directory=True)
    monkeypatch.delenv("GLUDD_RESOURCE_ROOT")
    monkeypatch.setenv("TMPDIR", str(alias))
    canonical = agent.watchdog_lock_path()
    canonical.parent.mkdir(parents=True)
    original.rename(canonical)
    assert agent.main(["--stop", "--validate-only", "1"]) == 0
    assert canonical.exists() and sent == []


@pytest.mark.parametrize("failure", ["log-flush", "log-fsync", "pid-fsync", "token"])
def test_failed_publication_never_leaves_an_adoptable_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from unittest.mock import MagicMock

    root = tmp_path / "publication"
    (root / ".gate-logs").mkdir(parents=True)
    log = root / ".gate-logs/watchdog.log"
    log.touch()
    pointer = root / ".gate-logs/watchdog.pid"
    monkeypatch.setattr(agent, "_WORKSPACE", root)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    monkeypatch.setenv("GLUDD_AGENT_WATCHDOG_PID", str(pointer))
    monkeypatch.setenv("GLUDD_AGENT_WATCHDOG_LOG", str(log))
    monkeypatch.setattr(agent, "_log", lambda _message: None)
    monkeypatch.setattr(agent, "_check_plugin_liveness_on_startup", lambda: None)
    lease_path = agent.watchdog_lock_path()
    original_open = Path.open
    original_fsync = os.fsync
    replacement: list[bytes] = []

    def opened(path: Path, mode: str = "r", buffering: int = -1, encoding: str | None = None,
               errors: str | None = None, newline: str | None = None) -> object:
        handle = original_open(path, mode, buffering, encoding, errors, newline)
        if path != log or failure != "log-flush":
            return handle
        proxy = MagicMock(wraps=handle)
        proxy.__enter__.return_value = proxy
        proxy.__exit__.side_effect = lambda *_args: handle.close()
        proxy.flush.side_effect = OSError("isolated ready log flush failure")
        return proxy

    def checked_fsync(fd: int) -> None:
        opened_stat = os.fstat(fd)
        log_stat = log.stat()
        is_log = (opened_stat.st_dev, opened_stat.st_ino) == (log_stat.st_dev, log_stat.st_ino)
        is_pointer = False
        if pointer.exists():
            pointer_stat = pointer.stat()
            is_pointer = (opened_stat.st_dev, opened_stat.st_ino) == (pointer_stat.st_dev, pointer_stat.st_ino)
        if (failure == "log-fsync" and is_log) or (failure == "pid-fsync" and is_pointer):
            raise OSError("isolated ready publication fsync failure")
        if failure == "token" and is_pointer:
            owner = json.loads(lease_path.read_text(encoding="utf-8"))
            owner["token"] = "replacement-owner"
            lease_path.write_text(json.dumps(owner), encoding="utf-8")
            replacement.append(lease_path.read_bytes())
        original_fsync(fd)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "open", opened)
        fault.setattr(os, "fsync", checked_fsync)
        with pytest.raises((OSError, ValueError)):
            agent.main([])
    assert not pointer.exists()
    if failure == "token":
        assert replacement and lease_path.read_bytes() == replacement[0]
        assert json.loads(lease_path.read_text(encoding="utf-8"))["ready"] == 0
    else:
        assert not lease_path.exists()


def test_ready_commit_does_not_roll_back_a_replacement_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_owner = importlib.import_module("scripts.watchdog_components.lease")
    monkeypatch.setattr(agent, "_WORKSPACE", tmp_path)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "resources"))
    lease = agent.acquire_watchdog_lock()
    assert lease is not None
    original_fsync = os.fsync
    calls = 0
    replacement: list[bytes] = []

    def replace_after_commit(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            owner = json.loads(lease.path.read_text(encoding="utf-8"))
            owner.update(token="replacement-owner", ready=0)
            lease.path.unlink()
            lease.path.write_text(json.dumps(owner), encoding="utf-8")
            replacement.append(lease.path.read_bytes())
        original_fsync(fd)

    try:
        with monkeypatch.context() as fault:
            fault.setattr(os, "fsync", replace_after_commit)
            with pytest.raises(ValueError, match="rollback refused a replaced owner") as refused:
                lease_owner.acknowledge_watchdog(agent._WATCHDOG_RUNTIME, lease)
            assert isinstance(refused.value.__cause__, ValueError)
        assert replacement and lease.path.read_bytes() == replacement[0]
    finally:
        agent.release_watchdog_lock(lease)
    assert lease.path.read_bytes() == replacement[0]
