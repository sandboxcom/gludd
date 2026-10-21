"""Confined native watchdog secrets-scan lifecycle regression tests."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Protocol, cast

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]
LABEL = "watchdog-secrets"
OUTPUT = "confined-watchdog-output-not-a-real-secret"
_FIXTURE = """import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(__file__).parent
if len(sys.argv) > 1 and sys.argv[1] == "worker":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(1.8)
    raise SystemExit(0)
mode = (root / "mode").read_text()
children = []
if mode == "timeout":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    children = [subprocess.Popen([sys.executable, __file__, "worker"]) for _ in range(2)]
(root / "owned.json").write_text(json.dumps({
    "pids": [os.getpid(), *[child.pid for child in children]],
    "prefix": sys.prefix,
    "uv_python": os.environ.get("UV_PYTHON"),
    "uv_environment": os.environ.get("UV_PROJECT_ENVIRONMENT"),
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "uv_no_sync": os.environ.get("UV_NO_SYNC"),
    "uv_downloads": os.environ.get("UV_PYTHON_DOWNLOADS"),
}))
print("confined-watchdog-output-not-a-real-secret", flush=True)
print("confined-watchdog-stderr-not-a-real-secret", file=sys.stderr, flush=True)
if mode == "timeout":
    for child in children:
        child.wait()
    time.sleep(0.1)
raise SystemExit(9 if mode == "failure" else 0)
"""


class Observer(Protocol):
    """Only the existing public observer arguments used by the adapter."""

    def __call__(
        self, command: Sequence[str], log_path: Path, *,
        observed_root: Path | None = None, label: str | None = None,
        run_id: str | None = None, heartbeat_seconds: float = 0,
        quiet_seconds: float = 0, max_seconds: float = 0, quiet: bool = False,
        retain_runs: int = 20, termination_grace_seconds: float = 0.5,
        environment: Mapping[str, str] | None = None, cwd: Path | str | None = None,
    ) -> int:
        """Return the existing native observer's terminal exit status."""
        ...


@dataclass
class Observation:
    command: tuple[str, ...]
    cwd: Path | str | None
    max_seconds: float
    quiet_seconds: float
    heartbeat_seconds: float
    quiet: bool
    retain_runs: int
    environment: dict[str, str] = field(repr=False)


@dataclass
class ScanFixture:
    root: Path
    module: ModuleType
    runtime: SimpleNamespace
    observations: list[Observation]
    legacy_calls: list[tuple[tuple[str, ...], str, int, bool, bool]]
    cooldowns: list[tuple[str, int]]
    marks: list[str]
    messages: list[str]


def _record(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _live_owned(root: Path) -> list[psutil.Process]:
    path = root / "owned.json"
    if not path.exists():
        return []
    pids = _record(path)["pids"]
    assert isinstance(pids, list)
    live: list[psutil.Process] = []
    for pid in pids:
        assert type(pid) is int and pid > 1
        try:
            process = psutil.Process(pid)
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                live.append(process)
        except psutil.NoSuchProcess:
            continue
    return live


@pytest.fixture
def watchdog_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScanFixture:
    """Only synthetic Make can run, including on the unfixed legacy path."""
    source = ROOT / "scripts/watchdog_components/release.py"
    spec = importlib.util.spec_from_file_location("watchdog_secrets_lifecycle_source", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = importlib.import_module("scripts.stream_command")
    native_observe = cast(Observer, observed.stream_command)
    legacy_calls: list[tuple[tuple[str, ...], str, int, bool, bool]] = []
    observations: list[Observation] = []
    cooldowns: list[tuple[str, int]] = []
    marks: list[str] = []
    messages: list[str] = []
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    for key in tuple(os.environ):
        if key.startswith(("MAKE", "MFLAGS", "GLUDD_XDIST_TRACE_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", sys.prefix)
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    monkeypatch.setenv("UV_NO_SYNC", "1")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    script = tmp_path / "synthetic_scan.py"
    script.write_text(_FIXTURE, encoding="utf-8")
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        ".PHONY: secrets-scan\nsecrets-scan:\n\t@"
        + shlex.quote(sys.executable) + " " + shlex.quote(str(script)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "mode").write_text("success", encoding="utf-8")

    def legacy_run(
        command: list[str], *, capture_output: bool, text: bool,
        timeout: int, cwd: str,
    ) -> subprocess.CompletedProcess[str]:
        legacy_calls.append((tuple(command), cwd, timeout, capture_output, text))
        if command != ["make", "secrets-scan"] or Path(cwd) != tmp_path:
            raise RuntimeError("Refusing non-fixture legacy scanner invocation")
        if not makefile.read_text(encoding="utf-8").startswith(".PHONY: secrets-scan\n"):
            raise RuntimeError("Refusing a changed synthetic Makefile")
        return subprocess.run(
            command, capture_output=capture_output, text=text, timeout=0.35,
            cwd=cwd, env=dict(os.environ), start_new_session=True, check=False,
        )

    def bounded_observer(
        command: Sequence[str], log_path: Path, *,
        observed_root: Path | None = None, label: str | None = None,
        run_id: str | None = None, heartbeat_seconds: float = 0,
        quiet_seconds: float = 0, max_seconds: float = 0, quiet: bool = False,
        retain_runs: int = 20, termination_grace_seconds: float = 0.5,
        environment: Mapping[str, str] | None = None, cwd: Path | str | None = None,
    ) -> int:
        observations.append(Observation(
            tuple(command), cwd, max_seconds, quiet_seconds, heartbeat_seconds,
            quiet, retain_runs, dict(environment or {}),
        ))
        if list(command) != ["make", "secrets-scan"] or Path(str(cwd)) != tmp_path:
            raise RuntimeError("Refusing non-fixture observed scanner invocation")
        return native_observe(
            command, log_path, observed_root=observed_root, label=label, run_id=run_id,
            heartbeat_seconds=0.1, quiet_seconds=quiet_seconds, max_seconds=0.35,
            quiet=quiet, retain_runs=retain_runs, termination_grace_seconds=0.05,
            environment=environment, cwd=cwd,
        )

    monkeypatch.setattr(observed, "stream_command", bounded_observer)

    def should_run(name: str, cooldown_secs: int) -> bool:
        cooldowns.append((name, cooldown_secs))
        return True

    runtime = SimpleNamespace(
        _WORKSPACE=tmp_path, Path=Path, json=json, time=time,
        subprocess=SimpleNamespace(run=legacy_run, TimeoutExpired=subprocess.TimeoutExpired),
        _should_run_check=should_run, _mark_check_run=marks.append, _log=messages.append,
        SECRETS_SCAN_COOLDOWN_SECS=300,
        SECRETS_VIOLATION_FILE=str(tmp_path / "violation.json"),
    )
    return ScanFixture(
        tmp_path, module, runtime, observations, legacy_calls, cooldowns, marks, messages,
    )


@pytest.mark.parametrize(
    ("watchdog_scan_mode", "watchdog_scan_code", "watchdog_scan_state"),
    [("success", 0, "passed"), ("failure", 2, "failed"), ("timeout", 124, "timed_out")],
)
def test_native_scan_owns_terminal_receipt_and_descendants(
    watchdog_scan: ScanFixture, capsys: pytest.CaptureFixture[str],
    watchdog_scan_mode: str, watchdog_scan_code: int, watchdog_scan_state: str,
) -> None:
    """Production keeps60s; only this confined native fault clock is accelerated."""
    fixture = watchdog_scan
    (fixture.root / "mode").write_text(watchdog_scan_mode, encoding="utf-8")
    live_at_return: list[int] = []
    if watchdog_scan_mode == "timeout":
        Path(fixture.runtime.SECRETS_VIOLATION_FILE).write_text(
            '{"ts": 0, "violation": false}', encoding="utf-8",
        )
    try:
        result: object = fixture.module._check_secrets_committed(fixture.runtime)
        live = _live_owned(fixture.root)
        live_at_return = [process.pid for process in live]
    finally:
        # Legacy RED children are bounded synthetic workers. Wait for their own
        # natural exit; no host scanner, guessed PID, signal or daemon mutation.
        remaining = _live_owned(fixture.root)
        if remaining:
            _gone, still_running = psutil.wait_procs(remaining, timeout=4)
            assert not still_running, "confined fixture failed to terminate naturally"
    assert not live_at_return, f"owned native descendants survived: {live_at_return}"
    assert fixture.legacy_calls == []
    assert fixture.cooldowns == [("secrets_scan", 300)]
    assert fixture.marks == ["secrets_scan"]
    assert len(fixture.observations) == 1
    call = fixture.observations[0]
    assert call.command == ("make", "secrets-scan")
    assert Path(str(call.cwd)) == fixture.root
    assert call.max_seconds == 60
    assert call.quiet_seconds == 0
    assert call.heartbeat_seconds > 0
    assert call.quiet is True
    assert call.retain_runs == 20
    assert call.environment["UV_NO_SYNC"] == "1"
    assert call.environment["UV_PYTHON_DOWNLOADS"] == "never"
    assert call.environment["UV_PYTHON"] == sys.executable
    assert call.environment["UV_PROJECT_ENVIRONMENT"] == sys.prefix
    assert call.environment["VIRTUAL_ENV"] == sys.prefix
    directory = fixture.root / ".gate-logs/observed" / LABEL
    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    current = _record(directory / "current.json")
    assert current["kind"] == "observed_command"
    assert current["state"] == watchdog_scan_state
    assert current["exit_code"] == watchdog_scan_code
    run_id = current["run_id"]
    assert isinstance(run_id, str)
    assert _record(directory / f"{run_id}.json") == current
    log = current["log_path"]
    assert isinstance(log, str)
    assert OUTPUT in Path(log).read_text(encoding="utf-8")
    assert not list(directory.glob(".*.tmp"))
    terminal = _record(Path(fixture.runtime.SECRETS_VIOLATION_FILE))
    if watchdog_scan_mode == "success":
        assert result is None
        assert terminal["violation"] is False
    else:
        assert isinstance(result, dict)
        assert terminal == result
        if watchdog_scan_mode == "failure":
            assert result["violation"] is True
            assert result["exit_code"] == 2
            assert len(str(result["output_snippet"])) <= 500
        else:
            assert result["violation"] is None
            assert result["reason"] == "timeout"
            assert "SECRETS SCAN TIMEOUT: >60s" in fixture.messages
            assert current["termination_reason"] == "max-runtime-timeout"
    captured = capsys.readouterr()
    assert OUTPUT not in captured.out + captured.err
    assert "confined-watchdog-stderr" not in captured.out + captured.err
    assert f"[observed {LABEL}] start" in captured.out
    assert f"[observed {LABEL}] result" in captured.out


def test_scan_cooldown_does_not_start_or_publish(watchdog_scan: ScanFixture) -> None:
    fixture = watchdog_scan
    fixture.runtime._should_run_check = lambda *_args, **_kwargs: False
    assert fixture.module._check_secrets_committed(fixture.runtime) is None
    assert fixture.legacy_calls == []
    assert fixture.observations == []
    assert fixture.marks == []
    assert not (fixture.root / "violation.json").exists()
    assert not (fixture.root / ".gate-logs").exists()


def test_scan_snippet_reads_only500_bytes(
    watchdog_scan: ScanFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import call, mock_open

    observed = importlib.import_module("scripts.stream_command")
    monkeypatch.setattr(observed, "stream_command", lambda *_args, **_kwargs: 2)
    reader = mock_open(read_data=b"x" * 700)
    monkeypatch.setattr(Path, "open", reader)
    result = watchdog_scan.module._run_secrets_scan(watchdog_scan.root)
    assert result.returncode == 2
    assert result.stdout == "x" * 500
    assert reader().read.call_args_list == [call(500)]
    assert watchdog_scan.legacy_calls == []


@pytest.mark.parametrize("watchdog_observer_fault", ["status125", "io-error"])
def test_scan_observer_error_invalidates_stale_success(
    watchdog_scan: ScanFixture, monkeypatch: pytest.MonkeyPatch,
    watchdog_observer_fault: str,
) -> None:
    fixture = watchdog_scan
    path = Path(fixture.runtime.SECRETS_VIOLATION_FILE)
    path.write_text('{"ts": 0, "violation": false}', encoding="utf-8")

    def failure(*_args: object, **_kwargs: object) -> int:
        if watchdog_observer_fault == "io-error":
            raise OSError("confined native observer I/O failure")
        return 125

    observed = importlib.import_module("scripts.stream_command")
    monkeypatch.setattr(observed, "stream_command", failure)
    result: object = fixture.module._check_secrets_committed(fixture.runtime)
    assert isinstance(result, dict)
    assert result["violation"] is None
    assert result["reason"] == "scan-error"
    assert _record(path) == result
    assert fixture.marks == ["secrets_scan"]
    assert fixture.legacy_calls == []


def test_scan_refuses_symlink_evidence_before_start(
    watchdog_scan: ScanFixture,
) -> None:
    fixture = watchdog_scan
    root = fixture.root / ".gate-logs/observed"
    root.mkdir(parents=True)
    destination = fixture.root / "other-fixture-evidence"
    destination.mkdir()
    (root / LABEL).symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        fixture.module._run_secrets_scan(fixture.root)
    assert fixture.observations == []
    assert fixture.legacy_calls == []
    assert not list(destination.iterdir())
