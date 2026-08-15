"""Tests for scripts/adaptive_test.py — the LOAD-AVERAGE-AWARE, memory-bounded
pytest runner.

``scripts/adaptive_test.py`` is a standalone script (not an installed module), so
it is loaded here by file path via importlib. The sizing logic is pure/injectable,
so no real pytest subprocess is spawned.

``compute_nproc`` now folds THREE independent caps and takes the smallest:
  * by_mem  — one worker per PER_WORKER_GB of available RAM
  * by_load — load headroom: 2.5*cores - load5 (5-minute load average)
  * cpu_count
Because ``by_load`` reads the *live* machine load, every ``compute_nproc`` test
here pins ``general_ludd.system.monitor.get_load_average`` so results are
deterministic regardless of the load on the machine running the suite.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "adaptive_test.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adaptive_test_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


at = _load()


def _pin_load(monkeypatch: pytest.MonkeyPatch, load5: float) -> None:
    """Pin the 5-minute load average so the load cap is deterministic."""
    import general_ludd.system.monitor as monitor_mod

    monkeypatch.setattr(monitor_mod, "get_load_average", lambda: (0.0, load5, 0.0))


@pytest.fixture
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default fixture: zero load, so the load cap never dominates."""
    _pin_load(monkeypatch, 0.0)


# ---- compute_nproc: the three caps ---------------------------------------


def test_compute_nproc_caps_by_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """A high 5-minute load collapses the worker count toward 1 (load dominates)."""
    # The load cap only applies OFF-CI; ensure CI is unset so this is deterministic
    # even when the suite itself is executed on a CI runner (which sets CI=true).
    monkeypatch.delenv("CI", raising=False)
    cores = 8
    # load5 = 2.4*cores -> by_load = int(2.5*cores - 2.4*cores) = int(0.1*cores) = 0 -> floored to 1.
    _pin_load(monkeypatch, 2.4 * cores)
    # RAM is abundant so only the load cap can be responsible for the drop to 1.
    assert at.compute_nproc(avail_gb=1000.0, cpu_count=cores, gb_per_worker=1.5) == 1


def test_compute_nproc_caps_by_mem(idle: None) -> None:
    """Low available RAM bounds the worker count (mem cap dominates)."""
    # 3 GiB / 1.5 GiB-per-worker = 2, below 8 cores and below the idle load cap.
    assert at.compute_nproc(avail_gb=3.0, cpu_count=8, gb_per_worker=1.5) == 2


# ---- CI: the load cap is BYPASSED (task #59) -----------------------------


def test_compute_nproc_ci_bypasses_load_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """On CI (CI=true) a high 5-minute load must NOT throttle workers.

    CI shards run on fresh, isolated 2-core runners with ample per-shard RAM and
    do not OOM, so the #45 load-average cap (which is a shared-LOCAL-box guard)
    over-throttles them toward -n 1. With CI=true the count is sized ONLY by
    min(cpu_count, RAM-bound) — the load cap is bypassed.
    """
    monkeypatch.setenv("CI", "true")
    cores = 2
    # A crushing 5-minute load: were the load cap applied, by_load would floor to
    # 1 (int(2.5*2 - 100) < 0 -> max(1, ...) == 1). RAM is abundant so the only
    # thing that could drop the count is the (now bypassed) load cap.
    _pin_load(monkeypatch, 100.0)
    n = at.compute_nproc(avail_gb=1000.0, cpu_count=cores, gb_per_worker=1.5)
    assert n == cores  # == min(cpu_count, RAM-bound); load cap BYPASSED
    assert n != 1  # explicitly NOT the load-throttled value


def test_compute_nproc_no_ci_still_applies_load_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI unset: the #45 local load cap STILL applies (regression guard).

    This is the LOCAL shared-box behaviour and must be UNCHANGED — a high load
    on a developer laptop still collapses the worker count toward 1.
    """
    monkeypatch.delenv("CI", raising=False)
    cores = 8
    # load5 = 2.4*cores -> by_load floors to 1 (same setup as the by_load test).
    _pin_load(monkeypatch, 2.4 * cores)
    assert at.compute_nproc(avail_gb=1000.0, cpu_count=cores, gb_per_worker=1.5) == 1


def test_compute_nproc_ci_still_applies_ram_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On CI the RAM cap + OOM backstop stay: tiny RAM still bounds workers."""
    monkeypatch.setenv("CI", "true")
    # 3 GiB / 1.5 = 2 workers even though load is high and cores/RAM headroom exist.
    _pin_load(monkeypatch, 100.0)
    assert at.compute_nproc(avail_gb=3.0, cpu_count=8, gb_per_worker=1.5) == 2


def test_compute_nproc_caps_by_cores(idle: None) -> None:
    """Plenty of RAM + zero load -> exactly the core count, never more."""
    assert at.compute_nproc(avail_gb=1000.0, cpu_count=4, gb_per_worker=1.5) == 4


@pytest.mark.parametrize(
    "avail_gb, cpu_count, per_worker, expected",
    [
        (6.0, 8, 1.5, 4),    # 6 // 1.5 == 4 workers, under cpu + load caps
        (1.4, 8, 1.5, 1),    # < one worker's budget -> floor of 1
        (0.0, 8, 1.5, 1),    # zero available -> floor of 1
        (12.0, 8, 3.0, 4),   # bigger per-worker budget -> fewer workers
    ],
)
def test_compute_nproc_mem_formula_when_idle(
    idle: None, avail_gb, cpu_count, per_worker, expected
) -> None:
    assert at.compute_nproc(avail_gb, cpu_count, per_worker) == expected


def test_compute_nproc_never_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extreme starvation (1 core, tiny RAM, huge load) still returns >= 1."""
    _pin_load(monkeypatch, 999.0)
    n = at.compute_nproc(avail_gb=0.1, cpu_count=1, gb_per_worker=1.5)
    assert n == 1


def test_compute_nproc_none_avail_gb_uses_cpu_for_mem(idle: None) -> None:
    """No psutil reading (avail_gb=None) falls back to cores for the RAM cap."""
    assert at.compute_nproc(avail_gb=None, cpu_count=3, gb_per_worker=1.5) == 3


def test_compute_nproc_zero_per_worker_is_cpu_only(idle: None) -> None:
    assert at.compute_nproc(avail_gb=8.0, cpu_count=5, gb_per_worker=0.0) == 5


def test_compute_nproc_import_error_falls_back_to_ram_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the system monitor cannot be imported, sizing falls back to RAM-only (no crash)."""
    # Poison the import so `from general_ludd.system.monitor import ...` raises ImportError.
    monkeypatch.setitem(sys.modules, "general_ludd.system.monitor", None)
    # 6 GiB / 1.5 = 4 workers, capped at 8 cores -> 4. The load cap is skipped entirely.
    assert at.compute_nproc(avail_gb=6.0, cpu_count=8, gb_per_worker=1.5) == 4


# ---- env override precedence ---------------------------------------------


def test_env_override_nproc_wins() -> None:
    assert at.env_override({"NPROC": "2"}) == 2


def test_env_override_gludd_xdist_int() -> None:
    assert at.env_override({"GLUDD_XDIST_WORKERS": "3"}) == 3


def test_env_override_auto_is_ignored() -> None:
    # GLUDD_XDIST_WORKERS=auto (the CI-faithfulness value) must NOT be treated as override.
    assert at.env_override({"GLUDD_XDIST_WORKERS": "auto"}) is None


def test_env_override_absent_or_nonpositive() -> None:
    assert at.env_override({}) is None
    assert at.env_override({"NPROC": "0"}) is None
    assert at.env_override({"NPROC": "garbage"}) is None


def test_decide_nproc_override_beats_adaptive(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit positive-int override short-circuits the RAM+load computation."""
    monkeypatch.setattr(at, "available_gb", lambda: 0.5)  # would otherwise force n=1
    assert at.decide_nproc({"NPROC": "7"}) == 7


def test_per_worker_gb_env_tunable() -> None:
    assert at.per_worker_gb({"PER_WORKER_GB": "2.0"}) == 2.0
    assert at.per_worker_gb({}) == at.DEFAULT_PER_WORKER_GB
    assert at.per_worker_gb({"PER_WORKER_GB": "nope"}) == at.DEFAULT_PER_WORKER_GB


# ---- OOM-exit detection --------------------------------------------------


@pytest.mark.parametrize("rc", [-9, 137])
def test_is_oom_exit_signal_codes(rc) -> None:
    assert at.is_oom_exit(rc, "") is True


@pytest.mark.parametrize(
    "output",
    [
        "[gw2] node down: Not properly terminated",
        "replacing crashed worker gw3",
        "worker gw1 CRASHED while running tests/unit/test_demo.py::test_case",
    ],
)
def test_is_oom_exit_output_markers(output) -> None:
    assert at.is_oom_exit(1, output) is True


@pytest.mark.parametrize(
    "output",
    [
        'E assert "worker crashed" in incident_text',
        "Failure report mentions node down: Not properly terminated in prose",
        "docs say replacing crashed worker gw3 during restart",
        "assertion: worker gw1 crashed while running was the expected message",
    ],
)
def test_is_oom_exit_ignores_failure_prose(output) -> None:
    assert at.is_oom_exit(1, output) is False


def test_is_oom_exit_ignores_marker_on_success() -> None:
    assert at.is_oom_exit(0, "[gw2] node down: Not properly terminated") is False


def test_is_oom_exit_clean_failure_is_not_oom() -> None:
    assert at.is_oom_exit(1, "1 failed, 3 passed") is False
    assert at.is_oom_exit(0, "") is False


@pytest.mark.parametrize("returncode", [-15, 124, 130, 143])
def test_is_oom_exit_distinguishes_signal_and_timeout(returncode: int) -> None:
    assert at.is_oom_exit(returncode, "orchestrator stopped the shard") is False


# ---- pytest command builder (now bounds via --maxprocesses) --------------


def test_build_pytest_cmd_adds_n_maxprocesses_and_dist() -> None:
    cmd = at.build_pytest_cmd(["tests/unit", "-q"], 4)
    assert "-n" in cmd
    assert cmd[cmd.index("-n") + 1] == "4"
    assert "--maxprocesses" in cmd
    assert cmd[cmd.index("--maxprocesses") + 1] == "4"
    assert "--dist" in cmd
    assert cmd[cmd.index("--dist") + 1] == "loadgroup"


def test_build_pytest_cmd_no_dup_when_caller_sets_them() -> None:
    cmd = at.build_pytest_cmd(
        ["tests/unit", "-n", "2", "--maxprocesses", "2", "--dist", "no"], 8
    )
    assert cmd.count("-n") == 1
    assert cmd.count("--maxprocesses") == 1
    assert cmd.count("--dist") == 1
    assert "8" not in cmd  # our computed count was not appended anywhere


# ---- retry / halving loop ------------------------------------------------


def test_run_retries_halving_on_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 8)
    seen: list[int] = []

    def fake_runner(cmd):
        n = int(cmd[cmd.index("-n") + 1])
        seen.append(n)
        if n > 1:
            return 137, "worker crashed"
        return 0, "ok"

    rc = at.run(["tests/unit"], env={}, runner=fake_runner)
    assert rc == 0
    assert seen == [8, 4, 2, 1]  # halving path


def test_run_gives_up_at_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 2)
    calls: list[int] = []

    def always_oom(cmd):
        calls.append(int(cmd[cmd.index("-n") + 1]))
        return -9, ""

    rc = at.run(["tests/unit"], env={}, runner=always_oom)
    assert rc == -9
    assert calls == [2, 1]  # halved once to 1, then gave up


def test_run_returns_immediately_on_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 4)
    n_calls = 0

    def runner(cmd):
        nonlocal n_calls
        n_calls += 1
        return 1, "1 failed"  # a real test failure, not OOM

    rc = at.run(["tests/unit"], env={}, runner=runner)
    assert rc == 1
    assert n_calls == 1  # no retry on a non-OOM failure


def test_run_does_not_retry_failure_prose_with_crash_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 4)
    n_calls = 0

    def runner(cmd):
        nonlocal n_calls
        n_calls += 1
        return 1, 'E assert "worker crashed" in incident_text'

    rc = at.run(["tests/unit"], env={}, runner=runner)
    assert rc == 1
    assert n_calls == 1


def test_run_never_retries_after_orchestrator_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 8)
    calls = 0

    def terminated_runner(cmd):
        nonlocal calls
        calls += 1
        return 137, "", "orchestrator-signal:SIGTERM"

    rc = at.run(["tests/unit"], env={}, runner=terminated_runner)

    assert rc == 137
    assert calls == 1


def test_no_progress_timeout_has_a_positive_bounded_default() -> None:
    assert at.no_progress_timeout_seconds({}) == at.DEFAULT_NO_PROGRESS_SECS
    assert at.no_progress_timeout_seconds({"GLUDD_ADAPTIVE_NO_PROGRESS_SECS": "12.5"}) == 12.5
    assert (
        at.no_progress_timeout_seconds({"GLUDD_ADAPTIVE_NO_PROGRESS_SECS": "invalid"})
        == at.DEFAULT_NO_PROGRESS_SECS
    )


# ---- shared-tmp-root isolation (the popen-gwN FileNotFoundError race) -----


def test_has_basetemp_detects_both_forms() -> None:
    assert at.has_basetemp(["tests/unit", "--basetemp=/tmp/x"]) is True
    assert at.has_basetemp(["tests/unit", "--basetemp", "/tmp/x"]) is True
    assert at.has_basetemp(["tests/unit", "-q"]) is False


def test_unique_basetemp_is_unique_and_namespaced() -> None:
    a = at.unique_basetemp()
    b = at.unique_basetemp()
    assert a != b  # uuid-keyed uniqueness
    assert Path(a).name.startswith(f"gludd-at-{os.getpid()}-")
    # A path is returned, not created — pytest creates/wipes the basetemp itself.
    assert not os.path.exists(a)


@pytest.mark.skipif(os.name != "posix", reason="AF_UNIX path limit is POSIX-only")
def test_unique_basetemp_leaves_af_unix_socket_headroom() -> None:
    """Nested tmp_path sockets must remain within Darwin's 103-byte limit."""
    socket_path = os.path.join(
        at.unique_basetemp(),
        "popen-gw99",
        "test_unix_http_connection_connect_uses_af_unix0",
        "fc.sock",
    )

    assert len(os.fsencode(socket_path)) <= 103


def test_run_injects_unique_basetemp_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run with no caller basetemp gets a process-unique --basetemp so the
    shard's workers never share pytest's default pytest-of-<user> tmp root."""
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 1)
    seen: dict[str, list[str]] = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)
        return 0, "ok"

    rc = at.run(["tests/unit"], env={}, runner=runner)
    assert rc == 0
    injected = [a for a in seen["cmd"] if a.startswith("--basetemp=")]
    assert len(injected) == 1
    assert "gludd-at-" in injected[0]


def test_run_respects_caller_basetemp(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_gate.sh already passes --basetemp; we must NOT add a second one."""
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 1)
    seen: dict[str, list[str]] = {}

    def runner(cmd):
        seen["cmd"] = list(cmd)
        return 0, "ok"

    at.run(["tests/unit", "--basetemp=/tmp/gludd-gate-abc123"], env={}, runner=runner)
    basetemps = [a for a in seen["cmd"] if a.startswith("--basetemp")]
    assert basetemps == ["--basetemp=/tmp/gludd-gate-abc123"]


def test_run_reuses_same_basetemp_across_oom_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injected basetemp is stable across the OOM-halving retries (each retry
    wipes+recreates the SAME dir) — not a fresh dir per attempt."""
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 4)
    basetemps: list[str] = []

    def runner(cmd):
        bt = next(a for a in cmd if a.startswith("--basetemp="))
        basetemps.append(bt)
        n = int(cmd[cmd.index("-n") + 1])
        return (137, "worker crashed") if n > 1 else (0, "ok")

    rc = at.run(["tests/unit"], env={}, runner=runner)
    assert rc == 0
    assert len(basetemps) == 3  # -n 4, 2, 1
    assert len(set(basetemps)) == 1  # all identical across retries


def test_stream_run_emits_heartbeat_and_persists_counters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Quiet child output still produces observable and durable progress."""
    progress_file = tmp_path / "adaptive-progress.json"
    monkeypatch.setenv("GLUDD_ADAPTIVE_HEARTBEAT_SECS", "0.01")
    monkeypatch.setenv("GLUDD_ADAPTIVE_PROGRESS_FILE", str(progress_file))

    class _QuietStream:
        def __iter__(self):
            time.sleep(0.03)
            yield "pytest finished\n"

    class _Process:
        stdout = _QuietStream()
        returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(at.subprocess, "Popen", lambda *args, **kwargs: _Process())

    result = at._stream_run(["pytest", "tests/"])

    assert result.returncode == 0
    assert result.output == "pytest finished\n"
    assert result.termination_reason is None
    payload = json.loads(progress_file.read_text())
    assert payload["status"] == "finished"
    assert payload["lines"] == 1
    assert payload["heartbeat_count"] >= 1
    assert "heartbeat" in capsys.readouterr().out


def test_stream_run_terminates_at_visible_no_progress_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    progress_file = tmp_path / "adaptive-timeout.json"
    monkeypatch.setenv("GLUDD_ADAPTIVE_HEARTBEAT_SECS", "1")
    monkeypatch.setenv("GLUDD_ADAPTIVE_NO_PROGRESS_SECS", "0.02")
    monkeypatch.setenv("GLUDD_ADAPTIVE_PROGRESS_FILE", str(progress_file))

    class _Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.terminated = False
            self.stdout = self._quiet_stream()

        def _quiet_stream(self):
            while not self.terminated:
                time.sleep(0.002)
            if False:
                yield ""

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def wait(self):
            return self.returncode

    process = _Process()
    monkeypatch.setattr(at.subprocess, "Popen", lambda *args, **kwargs: process)

    started = time.monotonic()
    result = at._stream_run(["pytest", "tests/"])

    assert result.returncode == 124
    assert result.termination_reason == "no-progress-timeout"
    assert time.monotonic() - started < 0.25
    payload = json.loads(progress_file.read_text())
    assert payload["status"] == "terminated"
    assert payload["termination_reason"] == "no-progress-timeout"
    output = capsys.readouterr().out
    assert "no-progress=" in output
    assert "terminating reason=no-progress-timeout" in output


def test_stream_run_records_orchestrator_signal_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_handlers: dict[int, object] = {}
    monkeypatch.setattr(at.signal, "getsignal", lambda _signum: at.signal.SIG_DFL)
    monkeypatch.setattr(
        at.signal,
        "signal",
        lambda signum, handler: installed_handlers.__setitem__(signum, handler),
    )

    class _Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = self._signal_stream()

        def _signal_stream(self):
            handler = installed_handlers[at.signal.SIGTERM]
            assert callable(handler)
            handler(at.signal.SIGTERM, None)
            if False:
                yield ""

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self):
            return self.returncode

    monkeypatch.setattr(at.subprocess, "Popen", lambda *args, **kwargs: _Process())

    result = at._stream_run(["pytest", "tests/"])

    assert result.returncode == 143
    assert result.termination_reason == "orchestrator-signal:SIGTERM"
