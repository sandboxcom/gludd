"""Tests for scripts/adaptive_test.py — the memory-bounded pytest runner.

Fast + coreutils-free: the NPROC formula, env-override precedence, OOM-exit
detection, the pytest-cmd builder, and the halving-retry loop are all pure
functions / injectable, so no real pytest subprocess is spawned here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "adaptive_test.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adaptive_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


at = _load()


# ---- NPROC formula -------------------------------------------------------

@pytest.mark.parametrize(
    "avail_gb, cpu_count, per_worker, expected",
    [
        (16.0, 8, 1.5, 8),    # RAM-rich: capped by cpu_count
        (6.0, 8, 1.5, 4),     # 6 // 1.5 == 4 workers, under cpu cap
        (1.4, 8, 1.5, 1),     # < one worker's budget -> floor of 1
        (0.0, 8, 1.5, 1),     # zero available -> floor of 1
        (100.0, 4, 1.5, 4),   # capped by cpu_count
        (12.0, 8, 3.0, 4),    # bigger per-worker budget -> fewer workers
    ],
)
def test_compute_nproc_formula(avail_gb, cpu_count, per_worker, expected) -> None:
    assert at.compute_nproc(avail_gb, cpu_count, per_worker) == expected


def test_compute_nproc_no_psutil_falls_back_to_cpu() -> None:
    # avail_gb None (psutil missing) -> cpu-only count.
    assert at.compute_nproc(None, 6, 1.5) == 6
    assert at.compute_nproc(None, 0, 1.5) == 1  # cpu floor


def test_compute_nproc_zero_per_worker_is_cpu_only() -> None:
    assert at.compute_nproc(8.0, 5, 0.0) == 5


# ---- env override precedence --------------------------------------------

def test_env_override_nproc_wins() -> None:
    assert at.env_override({"NPROC": "2"}) == 2


def test_env_override_gludd_xdist_int() -> None:
    assert at.env_override({"GLUDD_XDIST": "3"}) == 3


def test_env_override_auto_is_ignored() -> None:
    # GLUDD_XDIST=auto (the CI-faithfulness value) must NOT be treated as override.
    assert at.env_override({"GLUDD_XDIST": "auto"}) is None


def test_env_override_absent_or_nonpositive() -> None:
    assert at.env_override({}) is None
    assert at.env_override({"NPROC": "0"}) is None
    assert at.env_override({"NPROC": "garbage"}) is None


def test_decide_nproc_override_beats_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(at, "available_gb", lambda: 0.5)  # would force n=1
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
    ["... node down: Not properly terminated", "replacing crashed worker gw3",
     "worker CRASHED while running test"],
)
def test_is_oom_exit_output_markers(output) -> None:
    assert at.is_oom_exit(1, output) is True


def test_is_oom_exit_clean_failure_is_not_oom() -> None:
    assert at.is_oom_exit(1, "1 failed, 3 passed") is False
    assert at.is_oom_exit(0, "") is False


# ---- pytest command builder ---------------------------------------------

def test_build_pytest_cmd_adds_n_and_dist() -> None:
    cmd = at.build_pytest_cmd(["tests/unit", "-q"], 4)
    assert "-n" in cmd and "4" in cmd
    assert "--dist" in cmd and "loadgroup" in cmd


def test_build_pytest_cmd_no_dup_when_caller_sets_them() -> None:
    cmd = at.build_pytest_cmd(["tests/unit", "-n", "2", "--dist", "no"], 8)
    assert cmd.count("-n") == 1
    assert cmd.count("--dist") == 1
    assert "8" not in cmd  # our count was not appended


# ---- retry / halving loop ------------------------------------------------

def test_run_retries_halving_on_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(at, "decide_nproc", lambda env=None: 8)
    seen: list[int] = []

    def fake_runner(cmd):
        # cmd is [python, -m, pytest, ..., -n, <N>, --dist, loadgroup]
        n = int(cmd[cmd.index("-n") + 1])
        seen.append(n)
        # OOM until we get down to 1 worker, then succeed.
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
