"""Behavioral coverage for the split agent-watchdog service boundaries."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "agent_watchdog.py"


def _load_watchdog():
    name = "agent_watchdog_component_behavior"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


aw = _load_watchdog()


@pytest.fixture(autouse=True)
def _block_external_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    def completed(command, *_args, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(aw.subprocess, "run", completed)


def _task_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = {
        "TASK_DEADLINES_FILE": "deadlines.json",
        "STALLED_TASKS_FILE": "stalled.txt",
        "TASK_TIMING_FILE": "timings.json",
        "TASK_STATE_FILE": "task-state.json",
        "TASK_STATE_SNAPSHOT": "snapshot.json",
        "ANOMALY_COUNT_FILE": "anomaly-count.json",
        "EX_ANOMALIES_FILE": "anomalies.json",
        "STOP_STATE": "stop-state.json",
        "FALSE_DONE_BLOCKS": "false-done.json",
    }
    for name, filename in paths.items():
        monkeypatch.setattr(aw, name, str(tmp_path / filename))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate.pid")


def _enforcement_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    names = {
        "BLOCK_COUNTER_FILE": "block-counter.json",
        "CONTINUE_DIRECTIVE": "continue.json",
        "DISENGAGE_FILE": "disengage.json",
        "FORCE_DISPATCH_FILE": "force.json",
        "HEALTH_SCORE_FILE": "health.json",
        "LIVENESS_STARTUP_BACKOFF_FILE": "liveness.json",
        "MULTITASK_STATE_FILE": "multitask.json",
        "ORCHESTRATOR_STATE_FILE": "orchestrator.json",
        "PURE_IDLE_DIRECTIVE": "continue.txt",
    }
    for name, filename in names.items():
        monkeypatch.setattr(aw, name, str(tmp_path / filename))
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")


def test_task_state_timing_and_classification_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    timings = {
        "gate": {"average_duration_seconds": 20.5, "count": 2},
        "ignored": "not-a-record",
    }
    aw._write_task_timings(timings)
    assert aw._read_task_timings() == {
        "gate": {"average_duration_seconds": 20.5, "count": 2}
    }
    assert aw._normalize_task_state({"name": "gate", "started": 10, "pid": 7}) == {
        "name": "gate",
        "started": 10.0,
        "pid": 7,
    }
    assert aw._normalize_task_state({"name": "gate"}) is None

    Path(aw.TASK_STATE_FILE).write_text('{"name":"gate","started":10}')
    assert aw._read_task_state() == [{"name": "gate", "started": 10.0}]
    Path(aw.TASK_STATE_FILE).write_text(
        '{"name":"gate","started":10,"ended":11}'
    )
    assert aw._read_task_state() == []
    aw._write_task_state([{"name": "lint", "started": 12.0}])
    assert aw._read_previous_state() == [{"name": "lint", "started": 12.0}]

    aw._update_timing("gate", 30.5)
    aw._update_timing("lint", 5.0)
    updated = aw._read_task_timings()
    assert updated["gate"] == {
        "average_duration_seconds": pytest.approx(23.8333333333),
        "count": 3,
    }
    assert updated["lint"] == {"average_duration_seconds": 5.0, "count": 1}

    assert aw._load_stalled_tasks() == set()
    aw._record_stalled("b-task")
    aw._record_stalled("a-task")
    assert aw._load_stalled_tasks() == {"a-task", "b-task"}
    assert Path(aw.STALLED_TASKS_FILE).read_text().splitlines() == ["a-task", "b-task"]

    expected = aw.EXPECTED_DURATIONS
    commands = {
        "make git-push": expected["git-push"],
        "make git-status": expected["git-status"],
        "make ci-verdict": expected["ci-verdict"],
        "make lint": expected["lint"],
        "make typecheck": expected["typecheck"],
        "make collect-check": expected["collect-check"],
        "make test-unit": expected["test-unit"],
        "make gate": expected["gate"],
        "make test-specific": expected["test-specific"],
    }
    assert {command: aw._find_expected_duration(command) for command in commands} == commands
    assert aw._find_expected_duration("make help") is None
    assert [aw._guess_task_type(value) for value in ("push-x", "test-x", "commit-x", "gate-x", "misc")] == [
        "git-push",
        "test",
        "commit",
        "gate",
        "general",
    ]
    assert [aw._detect_task_type(value) for value in ("build-x", "pytest-x", "audit-x", "ship-x", "misc")] == [
        "gate",
        "test",
        "research",
        "push",
        "default",
    ]


def test_task_timing_cycle_records_completion_stall_and_anomaly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    running = [{"name": "live", "started": 100.0, "pid": 55}]
    previous = [
        {"name": "done", "started": 900.0},
        {"name": "live", "started": 100.0, "pid": 55},
    ]
    updates: list[tuple[str, float]] = []
    kills: list[tuple[str, int | None]] = []
    anomalies: list[tuple[str, float, float]] = []
    snapshots: list[list[dict[str, object]]] = []
    monkeypatch.setattr(aw, "_read_task_state", lambda: running)
    monkeypatch.setattr(aw, "_read_previous_state", lambda: previous)
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(aw, "_update_timing", lambda name, duration: updates.append((name, duration)))
    monkeypatch.setattr(aw, "_kill_stalled_task", lambda name, pid: kills.append((name, pid)))
    monkeypatch.setattr(
        aw,
        "_read_task_timings",
        lambda: {"live": {"average_duration_seconds": 10.0, "count": 2}},
    )
    monkeypatch.setattr(
        aw,
        "_flag_anomaly",
        lambda name, expected, actual: anomalies.append((name, expected, actual)),
    )
    monkeypatch.setattr(aw, "_write_task_state", lambda state: snapshots.append(state))
    monkeypatch.setattr(aw, "TASK_STALL_TIMEOUT", 30)
    monkeypatch.setattr(aw, "ANOMALY_MULTIPLIER", 2.0)

    aw.check_task_timings()

    assert updates == [("done", 100.0)]
    assert kills == [("live", 55)]
    assert anomalies == [("live", 10.0, 900.0)]
    assert snapshots == [running]


def test_task_kill_and_flag_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    logs: list[str] = []
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    monkeypatch.setattr(aw.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(aw.time, "sleep", lambda _seconds: None)

    aw.kill_stalled_task(11)
    aw._kill_stalled_task("gate", 12)
    aw._kill_stalled_task("no-pid", None)
    assert len(signals) == 4
    assert any("no pid" in message for message in logs)

    continue_file = tmp_path / "continue.txt"
    monkeypatch.setattr(aw, "Path", lambda _value: continue_file)
    monkeypatch.setattr(aw, "_now", lambda: "now")
    aw._flag_anomaly("gate", 10.0, 30.0)
    aw._flag_anomaly("gate", 0.0, 30.0)
    assert "TIMING ANOMALY" in continue_file.read_text()

    def missing(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(aw.os, "kill", missing)
    aw.kill_stalled_task(13)
    aw._kill_stalled_task("gone", 14)
    assert any("already gone" in message or "already exited" in message for message in logs)


def test_task_anomaly_detection_escalates_and_reports_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        aw,
        "EXPECTED_DURATIONS",
        {
            **aw.EXPECTED_DURATIONS,
            "push": 10,
            "test": 10,
            "default": 10,
            "lint": 10,
        },
    )
    monkeypatch.setattr(aw, "ANOMALY_ESCALATE_THRESHOLD", 1)
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    Path(aw.TASK_DEADLINES_FILE).write_text(
        json.dumps(
            {
                "stalled-push": 9_100.0,
                "slow-test": 9_975.0,
                "lint-task": {"start_ts": 9_970.0, "command": "make lint"},
                "unknown": {"start_ts": 1.0, "command": "make help"},
                "missing-start": {"command": "make lint"},
                "junk": "invalid",
            }
        )
    )
    aw.GATE_PID_FILE.write_text("1")
    os.utime(aw.GATE_PID_FILE, (0, 0))
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)

    findings = aw.check_task_anomalies()

    assert {entry["task_id"] for entry in findings["stalled"]} >= {
        "stalled-push",
        "gate-process",
    }
    assert {entry["task_id"] for entry in findings["anomalies"]} == {
        "slow-test",
        "lint-task",
    }
    assert findings["escalated"] is True
    assert Path(aw.EX_ANOMALIES_FILE).is_file()
    assert any("PUSH STALLED" in message for message in logs)


def test_agent_stall_and_anomaly_counter_file_behaviors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    stop = Path(aw.STOP_STATE)
    false_done = Path(aw.FALSE_DONE_BLOCKS)
    assert aw.check_agent_stalled(stop, false_done) is False
    stop.write_text('{"hasPendingWork":true}')
    assert aw.check_agent_stalled(stop, false_done) is True
    stop.write_text("invalid")
    false_done.write_text('{"consecutive":2}')
    assert aw.check_agent_stalled(stop, false_done) is True
    false_done.write_text("invalid")
    assert aw.check_agent_stalled(stop, false_done) is False

    assert aw._read_anomaly_count() == 0
    aw._write_anomaly_count(2)
    assert aw._increment_anomaly_count() == 3
    counts = {"anomaly:x": 1}
    assert aw._increment_anomaly_count("anomaly:x", counts) == 2
    assert json.loads(Path(aw.ANOMALY_COUNT_FILE).read_text())["anomaly:x"] == 2
    assert aw._gate_pid_elapsed_seconds() is None


def test_cli_modes_and_daemon_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(aw, "scan_tasks_dir", lambda _path: [])
    monkeypatch.setattr(aw, "stop_watchdog", lambda: True)
    assert aw._cli_classification(["--stop"]) == 0
    assert "stop requested" in capsys.readouterr().out

    monkeypatch.setattr(aw, "check_and_reset", lambda: {"ok": True})
    assert aw._cli_classification(["--once"]) == 0
    assert '"ok": true' in capsys.readouterr().out

    monkeypatch.setattr(aw, "acquire_watchdog_lock", lambda: None)
    monkeypatch.setattr(aw, "project_namespace", lambda _workspace: "test")
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    assert aw.main([]) == 0

    lease = object()
    released: list[object] = []
    calls: list[str] = []
    monkeypatch.setattr(aw, "acquire_watchdog_lock", lambda: lease)
    monkeypatch.setattr(aw, "release_watchdog_lock", released.append)
    monkeypatch.setattr(aw, "_check_plugin_liveness_on_startup", lambda: calls.append("startup"))
    for name in (
        "check_and_reset",
        "_check_force_dispatch",
        "check_running_tasks",
        "check_push_status",
        "_check_gate_background",
        "_check_load_average",
        "_check_plugin_liveness_periodic",
        "_rotate_watchdog_logs",
    ):
        monkeypatch.setattr(aw, name, lambda name=name: calls.append(name))
    monkeypatch.setattr(aw, "HIBERNATION_MARKER", tmp_path / "not-hibernating")
    monkeypatch.setattr(
        aw.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("one-cycle")),
    )
    with pytest.raises(RuntimeError, match="one-cycle"):
        aw.main([])
    assert calls[0] == "startup"
    assert "check_and_reset" in calls
    assert released == [lease]


def test_enforcement_directives_force_dispatch_and_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    aw._TASKS_MD.write_text("- [ ] ship item\n")
    monkeypatch.setattr(aw, "_now", lambda: "now")
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)

    directive = aw._build_continue_directive(
        ["local"], 3, True, 2, True, True, "123", "keep working", "escalate"
    )
    assert directive["dispatch_count"] == 3
    assert directive["source"] == "local"
    aw._write_continue_directive(
        ["local"], 3, True, 2, True, True, "123", "keep working", "escalate"
    )
    assert json.loads(Path(aw.CONTINUE_DIRECTIVE).read_text())["action"] == "FORCE_DISPATCH"
    assert "WATCHDOG CONTINUE DIRECTIVE" in Path(aw.PURE_IDLE_DIRECTIVE).read_text()

    force = Path(aw.FORCE_DISPATCH_FILE)
    force.write_text('{"level":4}')
    monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: True)
    monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 1)
    monkeypatch.setattr(aw, "_gate_status_is_red", lambda: True)
    assert aw._check_force_dispatch() is True
    forced = json.loads(Path(aw.CONTINUE_DIRECTIVE).read_text())
    assert forced["level"] == 4
    assert forced["dispatch_count"] == 3

    writes: list[str] = []
    monkeypatch.setattr(aw, "_pending_work_exists", lambda: True)
    monkeypatch.setattr(aw, "_write_prioritized_plain_directive", lambda text: writes.append(text) or True)
    monkeypatch.setattr(
        aw,
        "_read_multitask_state",
        lambda: {"thisMessageDispatches": 2, "zeroStreak": 1, "estimatedInFlight": 0},
    )
    aw._check_under_floor_dispatch()
    assert "UNDER-FLOOR" in writes[-1]
    monkeypatch.setattr(
        aw,
        "_read_multitask_state",
        lambda: {"thisMessageDispatches": 10, "zeroStreak": 1, "estimatedInFlight": 4},
    )
    aw._check_under_floor_dispatch()
    assert any("DISPATCH OK" in message for message in logs)


def test_enforcement_disengage_reengage_and_state_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    monkeypatch.setattr(aw, "_now", lambda: "now")
    aw._write_disengage_signal(minutes=1, reason="test")
    assert aw._is_disengage_active() is True

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
    monkeypatch.setattr(aw, "_is_push_running", lambda: False)
    aw._auto_reengage_enforcement(1.0)
    assert not Path(aw.DISENGAGE_FILE).exists()
    assert json.loads(Path(aw.BLOCK_COUNTER_FILE).read_text())["disengageUntil"] == 0
    assert any("auto-re-engaged" in message for message in logs)

    Path(aw.DISENGAGE_FILE).write_text('{"disengage_until":0}')
    aw._clear_disengage_signal()
    assert not Path(aw.DISENGAGE_FILE).exists()
    assert aw._read_multitask_state() == {}
    Path(aw.MULTITASK_STATE_FILE).write_text('{"zeroStreak":2}')
    assert aw._read_multitask_state() == {"zeroStreak": 2}

    monkeypatch.setattr(aw, "_compute_health_score", lambda *_args: 88)
    monkeypatch.setattr(aw, "_detect_ci_loop", lambda: False)
    monkeypatch.setattr(aw, "_detect_ci_true_stall", lambda: True)
    aw._write_orchestrator_state(True, 2, False, True, False, True, "42", True)
    state = json.loads(Path(aw.ORCHESTRATOR_STATE_FILE).read_text())
    assert state["health_score"] == 88
    assert state["ci_true_stall"] is True


def test_plugin_hash_and_liveness_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    plugin_dir = tmp_path / ".opencode" / "plugin"
    plugin_dir.mkdir(parents=True)
    plugin = plugin_dir / "enforce-stop.ts"
    plugin.write_text("export const one = 1\n")
    disengage: list[tuple[int, str]] = []
    logs: list[str] = []
    monkeypatch.setattr(
        aw,
        "_write_disengage_signal",
        lambda minutes=5, reason="": disengage.append((minutes, reason)),
    )
    monkeypatch.setattr(aw, "_log", logs.append)
    aw._check_plugin_hashes()
    assert (tmp_path / ".opencode" / "plugin-hashes.json").is_file()
    plugin.write_text("export const one = 2\n")
    aw._check_plugin_hashes()
    assert disengage and "plugin_version_mismatch" in disengage[0][1]

    result = SimpleNamespace(returncode=1, stdout="", stderr="broken plugin")
    monkeypatch.setattr(aw, "_liveness_startup_in_backoff", lambda: False)
    monkeypatch.setattr(aw.subprocess, "run", lambda *_args, **_kwargs: result)
    aw._check_plugin_liveness_on_startup()
    assert Path(aw.LIVENESS_STARTUP_BACKOFF_FILE).is_file()
    assert any("FAILED" in message for message in logs)
    monkeypatch.setattr(aw, "_last_liveness_check", 0.0)
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    aw._check_plugin_liveness_periodic()
    assert aw._last_liveness_check == 10_000.0


def test_push_and_force_dispatch_activity_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "push.lock"
    lock.write_text("")
    assert aw._is_push_running() is True
    lock.unlink()
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="123 git push origin development\n"),
    )
    assert aw._is_push_running() is True
    monkeypatch.setattr(aw.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))
    assert aw._is_push_running() is False
    assert aw._is_force_dispatch_active() is False
    Path(aw.FORCE_DISPATCH_FILE).write_text("{}")
    assert aw._is_force_dispatch_active() is True


def test_running_task_and_push_status_monitors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        aw,
        "EXPECTED_DURATIONS",
        {**aw.EXPECTED_DURATIONS, "gate": 10, "lint": 10, "default": 10},
    )
    monkeypatch.setattr(aw, "ANOMALY_ESCALATE_THRESHOLD", 1)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    Path(aw.TASK_DEADLINES_FILE).write_text(
        json.dumps(
            [
                "invalid",
                {"task_name": "missing-start"},
                {"task_name": "bad", "start_ts": "invalid"},
                {"task_name": "gate", "task_id": "one", "start_ts": 100},
                {"task_name": "lint", "task_id": "two", "start_ts": 960},
                {"task_name": "normal", "task_id": "three", "start_ts": 995},
            ]
        )
    )
    aw.check_running_tasks()
    assert Path(aw.STALLED_TASKS_FILE).read_text().strip() == "gate"
    assert any("TASK STALLED" in message for message in logs)
    assert any("TASK ANOMALY ESCALATED" in message for message in logs)

    Path(aw.STREAK_FILE).write_text('{"count":7}')
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["make", "git-status"], 0, stdout="ahead of origin/development", stderr=""
        ),
    )
    aw.check_push_status()
    assert any("ahead of remote" in message for message in logs)
    assert any("mainthread streak=7" in message for message in logs)


def test_ci_cache_and_stall_status_transitions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)

    assert aw._should_check_ci() is True
    aw._update_ci_cache(existing="yes")
    assert aw._should_check_ci() is False
    Path(aw.CI_CACHE_FILE).write_text('{"last_ci_check":0}')
    assert aw._should_check_ci() is True

    outputs = iter(
        [
            "conclusion: FAILURE\nrun_id: 101\n",
            "conclusion: PENDING\nrun_id: 102\n",
            "conclusion: PENDING\nrun_id: 102\n",
            "conclusion: SUCCESS\nrun_id: 102\n",
        ]
    )

    def ci_result(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=next(outputs), stderr="")

    monkeypatch.setattr(aw.subprocess, "run", ci_result)
    monkeypatch.setattr(aw, "_should_check_ci", lambda: True)
    aw._check_ci_stall()
    assert any("CI FAILED" in message for message in logs)
    aw._check_ci_stall()
    cache = json.loads(Path(aw.CI_CACHE_FILE).read_text())
    assert cache["last_ci_run_id"] == "102"
    cache["pending_first_seen"] = 0.1
    Path(aw.CI_CACHE_FILE).write_text(json.dumps(cache))
    aw._check_ci_stall()
    assert any("CI STALLED" in message for message in logs)
    aw._check_ci_stall()
    assert json.loads(Path(aw.CI_CACHE_FILE).read_text())["last_ci_status"] == "SUCCESS"

    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="head\n", stderr=""),
    )
    assert aw._get_local_head() == "head"

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["make", "ci-verdict"], 1)

    monkeypatch.setattr(aw.subprocess, "run", timeout)
    aw._check_ci_stall()
    assert any("CI CHECK TIMEOUT" in message for message in logs)


def test_gate_and_push_health_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate.pid")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly.json"))
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "GATE_MAX_RUNTIME_SECS", 100)
    monkeypatch.setattr(aw, "ANOMALY_ESCALATE_THRESHOLD", 1)
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(aw.time, "sleep", lambda _seconds: None)
    logs: list[str] = []
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    monkeypatch.setattr(aw.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    aw.GATE_PID_FILE.write_text("321")
    os.utime(aw.GATE_PID_FILE, (0, 0))
    aw._check_gate_background()
    assert not aw.GATE_PID_FILE.exists()
    assert signals[0] == (321, 0)
    assert "GATE_TIMEOUT" in aw._GATE_STATUS.read_text()

    os.utime(aw._GATE_STATUS, (0, 0))
    aw._check_gate_background()
    assert any("GATE STATUS STALE" in message for message in logs)

    monkeypatch.setattr(aw, "_get_local_head", lambda: "abc123")
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="2\n", stderr=""),
    )
    aw._check_push_health()
    assert any("PUSH NEEDED" in message for message in logs)
    assert any("PUSH ANOMALY ESCALATED" in message for message in logs)

    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="-1\n", stderr=""),
    )
    aw._check_push_health()
    assert any("PUSH VERIFICATION FAILED" in message for message in logs)


def test_duration_tracking_and_operation_timing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    durations = tmp_path / "durations.json"
    timing = tmp_path / "timing.json"
    push = tmp_path / "push.flag"
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(durations))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(timing))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(push))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tasks))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate.pid")
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(
        aw,
        "EXPECTED_DURATIONS",
        {**aw.EXPECTED_DURATIONS, "git-push": 10, "gate-run": 10, "subagent-task": 100},
    )
    monkeypatch.setattr(aw, "STALLED_PUSH_SECS", 10)
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)

    aw.track_task_duration("lint", 10.0)
    aw.track_task_duration("lint", 10.0)
    aw.track_task_duration("lint", 100.0)
    tracked = json.loads(durations.read_text())["lint"]
    assert tracked["count"] == 3
    assert any("ANOMALY: lint" in message for message in logs)

    push.write_text("")
    aw.GATE_PID_FILE.write_text("321")
    output = tasks / "task.output"
    output.write_text("working")
    for path in (push, aw.GATE_PID_FILE, output):
        os.utime(path, (950, 950))
    operations = aw._detect_operations()
    assert set(operations) == {"git-push", "gate-run", "subagent-task"}

    aw._write_timing_data(
        {
            "git-push": {
                "started_at": 900.0,
                "last_check": 900.0,
                "duration": 0.0,
                "status": "running",
            },
            "finished": {
                "started_at": 900.0,
                "last_check": 900.0,
                "duration": 0.0,
                "status": "running",
            },
        }
    )
    anomalies = aw._check_timing_anomalies()
    assert "git-push" in anomalies
    timing_data = aw._read_timing_data()
    assert timing_data["finished"]["status"] == "completed"

    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="2\n", stderr=""),
    )
    stalled = aw._detect_stalled_push()
    assert stalled is not None and "git-push" in stalled
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="0\n", stderr=""),
    )
    assert aw._detect_stalled_push() is None


@pytest.mark.parametrize(
    ("load", "expected_floor"),
    [(25.0, 0), (15.0, 3)],
)
def test_load_average_throttle_levels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    load: float,
    expected_floor: int,
) -> None:
    throttle = tmp_path / "load-throttle.json"
    monkeypatch.setattr(aw, "LOAD_THROTTLE_FILE", str(throttle))
    monkeypatch.setattr(aw.os, "getloadavg", lambda: (load, 0.0, 0.0))
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="1\n2\n3\n4\n"),
    )
    monkeypatch.setattr(aw, "MAX_CHILD_PROCESSES", 2)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    aw._check_load_average()
    assert json.loads(throttle.read_text())["floor"] == expected_floor
    assert any("CHILD PROCESS WARN" in message for message in logs)


def test_load_average_warning_and_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    throttle = tmp_path / "load-throttle.json"
    throttle.write_text("{}")
    monkeypatch.setattr(aw, "LOAD_THROTTLE_FILE", str(throttle))
    loads = iter([(9.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    monkeypatch.setattr(aw.os, "getloadavg", lambda: next(loads))
    monkeypatch.setattr(aw.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=""))
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    aw._check_load_average()
    assert not throttle.exists()
    throttle.write_text("{}")
    aw._check_load_average()
    assert not throttle.exists()
    assert any("LOAD WARN" in message for message in logs)


def test_log_rotation_pruning_and_cooldown_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rotating = tmp_path / "gludd-watchdog.log"
    skipped = tmp_path / "gludd-stderr-worker.log"
    rotating.write_bytes(b"old line\n" + b"x" * 100)
    skipped.write_bytes(b"keep")
    monkeypatch.setattr(aw.glob, "glob", lambda _pattern: [str(rotating), str(skipped)])
    monkeypatch.setattr(aw, "WATCHDOG_LOG_ROTATION_MB", 0.00001)
    monkeypatch.setattr(aw, "WATCHDOG_LOG_KEEP_MB", 0)
    monkeypatch.setattr(aw, "WATCHDOG_LOG_ROTATE_INTERVAL_SECS", 10)
    monkeypatch.setattr(aw, "_WATCHDOG_LAST_LOG_ROTATE", 0.0)
    monkeypatch.setattr(aw.time, "time", lambda: 100.0)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    aw._rotate_watchdog_logs()
    assert rotating.read_bytes() == b""
    assert skipped.read_bytes() == b"keep"
    assert any("LOG ROTATION" in message for message in logs)
    aw._rotate_watchdog_logs()

    monkeypatch.setattr(aw, "_alerted_anomalies", {"old": 1.0, "new": 99.0})
    monkeypatch.setattr(aw, "_ALERTED_PRUNE_SECS", 10)
    aw._prune_alerted_anomalies(100.0)
    assert aw._alerted_anomalies == {"new": 99.0}

    monkeypatch.setattr(aw, "LAST_FLAG_FILE", str(tmp_path / "last-flag.json"))
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    assert aw._read_last_flag_time() == 0.0
    aw._write_last_flag_time(42.0)
    assert aw._read_last_flag_time() == 42.0
    assert aw._read_check_cooldowns() == {}
    aw._mark_check_run("push")
    assert aw._should_run_check("push", cooldown_secs=1_000) is False


def test_prioritized_directive_and_periodic_stall_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directive = tmp_path / "continue.txt"
    cooldown = tmp_path / "cooldowns.json"
    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    push_lock = workspace / ".git" / "push.lock"
    push_lock.write_text("")
    os.utime(push_lock, (0, 0))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(directive))
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(cooldown))
    monkeypatch.setattr(aw, "_WORKSPACE", workspace)
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(aw, "_now", lambda: "now")
    monkeypatch.setattr(aw, "_should_run_check", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(aw, "_mark_check_run", lambda _name: None)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)

    directive.write_text("PUSH STALLED: preserve me")
    assert aw._write_prioritized_plain_directive("UNDER-FLOOR DETECTED: lower") is False
    assert "preserve me" in directive.read_text()
    assert aw._write_prioritized_plain_directive("PUSH STALLED: replacement") is True

    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="123 02:00 git push origin development\n"),
    )
    aw._check_push_stalled()
    assert any("push.lock" in message for message in logs)
    assert any("git push process" in message for message in logs)

    monkeypatch.setattr(
        aw,
        "_read_deadlines",
        lambda: [{"task_id": "long-task", "elapsed": 301.0}],
    )
    aw._check_task_anomaly_300s()
    assert any("task long-task" in message for message in logs)

    monkeypatch.setattr(aw, "_get_local_head", lambda: "abcdefgh1234")
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"status": "in_progress", "createdAt": "2020-01-01T00:00:00Z"})
        ),
    )
    aw._check_ci_pending_stall()
    assert any("CI STALLED" in message for message in logs)


def test_enforcement_reengage_caps_and_liveness_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    monkeypatch.setattr(aw, "DISENGAGE_MAX_SECS_CI_NOT_GREEN", 5)
    monkeypatch.setattr(aw, "AUTO_REENGAGE_DISENGAGE_AGE_SECS", 2)
    monkeypatch.setattr(aw, "AUTO_REENGAGE_AGENT_ACTIVE_SECS", 60)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "99"))
    monkeypatch.setattr(aw, "_is_push_running", lambda: False)

    disengage = Path(aw.DISENGAGE_FILE)
    disengage.write_text('{"disengage_until":999999999}')
    os.utime(disengage, (0, 0))
    aw._auto_reengage_enforcement(1.0)
    assert not disengage.exists()
    assert any("CI still pending" in message for message in logs)

    disengage.write_text('{"disengage_until":999999999}')
    os.utime(disengage, (0, 0))
    monkeypatch.setattr(aw, "_is_push_running", lambda: True)
    aw._auto_reengage_enforcement(1.0)
    assert not disengage.exists()

    disengage.write_text('{"disengage_until":999999999}')
    os.utime(disengage, (0, 0))
    aw._auto_reengage_enforcement(None)
    assert not disengage.exists()

    monkeypatch.setattr(aw, "_liveness_startup_in_backoff", lambda: True)
    aw._check_plugin_liveness_on_startup()
    assert any("backoff active" in message for message in logs)

    monkeypatch.setattr(aw, "_liveness_startup_in_backoff", lambda: False)
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    aw._check_plugin_liveness_on_startup()
    assert any("PASSED" in message for message in logs)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["make", "check-plugin-liveness"], 30)

    monkeypatch.setattr(aw.subprocess, "run", timeout)
    aw._check_plugin_liveness_on_startup()
    assert any("TIMEOUT" in message for message in logs)


def test_lease_identity_rejection_and_token_safety(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert aw._owner_is_alive({"pid": object()}) is False
    assert aw._owner_is_alive({"pid": "not-a-pid"}) is False
    assert aw._owner_is_alive({"pid": -1}) is False

    monkeypatch.setattr(aw, "_process_start_time", lambda _pid: "current")

    def denied(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(aw.os, "kill", denied)
    assert aw._owner_is_alive({"pid": 123, "pid_start_time": "current"}) is True
    assert aw._owner_is_alive({"pid": 123, "pid_start_time": "old"}) is False

    def missing(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(aw.os, "kill", missing)
    assert aw._owner_is_alive({"pid": 123}) is False

    lock = tmp_path / "watchdog.lock"
    lock.write_text('{"token":"new"}')
    aw._unlink_if_token_matches(lock, "old")
    assert lock.exists()
    aw._unlink_if_token_matches(lock, "new")
    assert not lock.exists()
    lock.write_text("invalid")
    assert aw._read_lock_owner(lock) is None


def test_enforcement_age_and_invalid_state_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enforcement_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(aw.time, "time", lambda: 100.0)
    assert aw._liveness_startup_in_backoff() is False
    Path(aw.LIVENESS_STARTUP_BACKOFF_FILE).write_text('{"last_check_ts":99}')
    assert aw._liveness_startup_in_backoff() is True
    Path(aw.LIVENESS_STARTUP_BACKOFF_FILE).write_text('{"last_check_ts":0}')
    assert aw._liveness_startup_in_backoff() is False
    Path(aw.LIVENESS_STARTUP_BACKOFF_FILE).write_text("invalid")
    assert aw._liveness_startup_in_backoff() is False

    Path(aw.MULTITASK_STATE_FILE).write_text("invalid")
    assert aw._read_multitask_state() == {}
    Path(aw.DISENGAGE_FILE).write_text("invalid")
    assert aw._is_disengage_active() is False
    Path(aw.FORCE_DISPATCH_FILE).write_text("{}")
    os.utime(Path(aw.FORCE_DISPATCH_FILE), (0, 0))
    monkeypatch.setattr(aw, "FORCE_DISPATCH_MAX_AGE", 1)
    assert aw._is_force_dispatch_active() is False

    Path(aw.DISENGAGE_FILE).unlink(missing_ok=True)
    Path(aw.BLOCK_COUNTER_FILE).unlink(missing_ok=True)
    aw._auto_reengage_enforcement(None)


def test_monitor_fail_open_and_boundary_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    logs: list[str] = []
    monkeypatch.setattr(aw, "_log", logs.append)
    aw.check_running_tasks()
    Path(aw.TASK_DEADLINES_FILE).write_text("{}")
    aw.check_running_tasks()

    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "_should_check_ci", lambda: True)
    monkeypatch.setattr(aw, "_get_local_head", lambda: None)
    aw._check_push_health()

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], 1)

    monkeypatch.setattr(aw, "_get_local_head", lambda: "head")
    monkeypatch.setattr(aw.subprocess, "run", timeout)
    aw._check_push_health()
    assert any("NETWORK STALL" in message for message in logs)
    aw._check_ci_stall()
    assert any("CI CHECK TIMEOUT" in message for message in logs)

    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "timing.json"))
    Path(aw.TIMING_DATA_FILE).write_text("invalid")
    assert aw._read_timing_data() == {}
    Path(aw.TIMING_DATA_FILE).write_text('{"bad":"value"}')
    assert aw._read_timing_data() == {}

    monkeypatch.setattr(aw.os, "getloadavg", lambda: (_ for _ in ()).throw(OSError()))
    aw._check_load_average()


def test_push_history_retention_and_ci_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    push_history = tmp_path / "push-history.json"
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(push_history))
    monkeypatch.setattr(aw.time, "time", lambda: 10_000.0)
    push_history.write_text(json.dumps([9_999.0] * 60 + [1.0]))
    aw._record_push_timestamp()
    stored = json.loads(push_history.read_text())
    assert len(stored) == 50
    assert all(value > 1.0 for value in stored)

    push_history.write_text("invalid")
    assert aw._detect_ci_loop() is False
    monkeypatch.setattr(aw, "_ci_pending_for_too_long_minutes", lambda: 100.0)
    push_history.unlink()
    assert aw._detect_ci_true_stall() is True
    push_history.write_text("invalid")
    assert aw._detect_ci_true_stall() is True


def test_remaining_monitor_decision_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _task_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        aw,
        "EXPECTED_DURATIONS",
        {**aw.EXPECTED_DURATIONS, "gate": 10, "lint": 10, "default": 10},
    )
    monkeypatch.setattr(aw, "ANOMALY_ESCALATE_THRESHOLD", 99)
    Path(aw.TASK_DEADLINES_FILE).write_text(
        json.dumps(
            [
                {"task_name": "gate", "start_ts": 100},
                {"task_name": "lint", "start_ts": 960},
                {"task_name": "normal", "start_ts": 999},
            ]
        )
    )
    aw.check_running_tasks()

    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    Path(aw.STREAK_FILE).write_text('{"count":1}')
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="clean", stderr=""),
    )
    aw.check_push_status()
    Path(aw.STREAK_FILE).unlink()
    aw.check_push_status()

    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate.pid")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    aw.GATE_PID_FILE.write_text("")
    aw._check_gate_background()
    aw.GATE_PID_FILE.write_text("invalid")
    aw._check_gate_background()
    assert not aw.GATE_PID_FILE.exists()
    aw.GATE_PID_FILE.write_text("321")
    os.utime(aw.GATE_PID_FILE, (999, 999))
    monkeypatch.setattr(aw.os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(aw, "GATE_MAX_RUNTIME_SECS", 100)
    aw._check_gate_background()
    aw.GATE_PID_FILE.unlink()
    aw._GATE_STATUS.write_text("green")
    os.utime(aw._GATE_STATUS, (999, 999))
    aw._check_gate_background()

    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "_get_local_head", lambda: "head")
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="not-a-number", stderr=""),
    )
    aw._check_push_health()
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="0", stderr=""),
    )
    aw._check_push_health()


def test_remaining_storage_operation_and_runtime_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    Path(aw.DURATIONS_FILE).write_text('{"junk":"bad","lint":{"avg_duration":2,"count":2}}')
    aw.track_task_duration("lint", 3.0)

    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "directory.output").mkdir()
    (tasks / "ignored.txt").write_text("x")
    stale = tasks / "stale.output"
    stale.write_text("x")
    os.utime(stale, (1, 1))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tasks))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(tmp_path / "missing-push"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "missing-gate")
    monkeypatch.setattr(aw.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        aw,
        "EXPECTED_DURATIONS",
        {**aw.EXPECTED_DURATIONS, "subagent-task": 10},
    )
    assert aw._detect_operations() == {}

    monkeypatch.setattr(aw, "_process_start_time", lambda _pid: None)
    monkeypatch.setattr(aw.os, "kill", lambda _pid, _sig: None)
    assert aw._owner_is_alive({"pid": 123, "pid_start_time": "recorded"}) is True

    _enforcement_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        aw.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="grep git push\nps -eo command git push\nordinary process\n"
        ),
    )
    assert aw._is_push_running() is False
    Path(aw.DISENGAGE_FILE).write_text('{"disengage_until":9999999999999}')
    aw._clear_disengage_signal()
    assert Path(aw.DISENGAGE_FILE).exists()


def test_lease_platform_fallback_and_shutdown_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ProcStat:
        def exists(self) -> bool:
            return True

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return "watchdog) " + " ".join(str(value) for value in range(20))

    monkeypatch.setattr(aw, "Path", lambda _value: ProcStat())
    assert aw._process_start_time(123) == "19"

    def os_error(_pid: int, _signal: int) -> None:
        raise OSError

    monkeypatch.setattr(aw.os, "kill", os_error)
    assert aw._owner_is_alive({"pid": 123}) is False
    aw.release_watchdog_lock(None)

    lock = tmp_path / "malformed.lock"
    lock.write_text("invalid")
    monkeypatch.undo()
    lease = aw.acquire_watchdog_lock(lock_path=lock, pid=os.getpid())
    assert lease is not None
    aw.release_watchdog_lock(lease)

    lock.write_text('{"pid":1,"token":"owner"}')
    monkeypatch.setattr(aw, "_read_lock_owner", lambda _path: {"pid": 1, "token": "owner"})
    monkeypatch.setattr(aw, "_owner_is_alive", lambda _owner: True)
    monkeypatch.setattr(aw, "_as_int", lambda _value, _default=-1: -1)
    assert aw.stop_watchdog(lock_path=lock) is False

    monkeypatch.setattr(aw, "_as_int", lambda _value, _default=-1: 1)
    monkeypatch.setattr(aw.os, "kill", lambda _pid, _sig: (_ for _ in ()).throw(PermissionError()))
    assert aw.stop_watchdog(lock_path=lock) is False
