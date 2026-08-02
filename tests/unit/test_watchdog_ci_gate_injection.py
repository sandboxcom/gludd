"""Tests for watchdog CI gate-status injection behavior.

Verifies that when CI is pending and .gate-status is otherwise clean,
the watchdog writes a CI-FAIL line so enforce-stop.ts sees hasLocalWork.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "agent_watchdog.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_watchdog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("agent_watchdog", module)
    spec.loader.exec_module(module)
    return module


aw = _load_module()
_ci_is_pending_or_red = aw._ci_is_pending_or_red
_gate_status_is_red = aw._gate_status_is_red
_pending_work_exists = aw._pending_work_exists
HEARTBEAT_FILE = aw.HEARTBEAT_FILE


def check_and_reset() -> dict:
    """Exercise CI injection without launching the repository scanner."""
    return aw.check_and_reset(secrets_check=lambda: None)


# ── Shared fixtures / helpers ─────────────────────────────────────────────────

def _setup_ci_gate_test(monkeypatch, tmp_path: Path, tasks_clean=True, ratchet_empty=True):
    """Configure all file paths to point into tmp_path, with clean local state."""
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "watchdog-activity.json"))
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(tmp_path / "todos.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done-blocks.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(tmp_path / "false-done-maxout.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.txt"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "LAST_FLAG_FILE", str(tmp_path / "last-flag.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "deadlines.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled-tasks.txt"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "ex-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timing.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_STATE_SNAPSHOT", str(tmp_path / "task-state-snapshot.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "timing-data.json"))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(tmp_path / "push-flag-nonexistent"))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tmp_path / "tasks-dir-nonexistent"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate-pid-nonexistent")
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")

    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "_CI_STATUS", tmp_path / ".ci-status", raising=False)
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "check-cooldowns.json"))
    monkeypatch.setattr(aw, "HEARTBEAT_FILE", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: False)
    monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
    monkeypatch.setattr(aw, "_check_push_health", lambda: None)
    monkeypatch.setattr(aw, "check_task_timings", lambda: None)
    monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
    monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
    monkeypatch.setattr(aw, "_detect_ci_loop", lambda: False)
    monkeypatch.setattr(aw, "_detect_ci_true_stall", lambda: False)
    monkeypatch.setattr(aw, "_check_under_floor_dispatch", lambda: None)
    monkeypatch.setattr(aw, "_check_ci_red_after_tag_push", lambda: None)
    monkeypatch.setattr(aw, "_check_release_completeness", lambda: None)
    monkeypatch.setattr(aw, "_check_secrets_committed", lambda: None)
    monkeypatch.setattr(aw, "_check_stale_release", lambda: None)


    # Write clean local state
    if tasks_clean:
        (tmp_path / "TASKS.md").write_text("- [x] all done" + chr(10))
    else:
        (tmp_path / "TASKS.md").write_text("- [ ] pending task" + chr(10))

    if ratchet_empty:
        (tmp_path / "ratchet.yml").write_text("# empty" + chr(10))
    else:
        (tmp_path / "ratchet.yml").write_text("entry: value" + chr(10))

    (tmp_path / "todos.json").write_text("[]")
    (tmp_path / "stop-count.json").write_text("{\"count\":0}")
    (tmp_path / "push-flag-nonexistent").write_text("")
    (tmp_path / "tasks-dir-nonexistent").mkdir(parents=True, exist_ok=True)

# ── Test 1: CI pending + gate clean → watchdog writes CI status ───────────────

def test_ci_pending_sets_ci_status_without_rewriting_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When CI is pending and .gate-status is clean, the watchdog writes
    .ci-status with the CI run ID and leaves local gate evidence intact."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text("{\"count\": 0, \"last_tool\": \"write\"}")
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    original_gate = chr(10).join([
        "lint PASS 0",
        "typecheck PASS 0",
        "collect PASS 0",
        "=== GATE: PASSED ===",
        "",
    ])
    gate_status.write_text(original_gate)

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "424242"))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    assert gate_status.read_text() == original_gate
    ci_content = (tmp_path / ".ci-status").read_text()
    assert "CI FAIL pending" in ci_content, f"Expected CI FAIL in ci-status, got: {ci_content!r}"
    assert "424242" in ci_content, f"Expected run ID 424242 in ci-status, got: {ci_content!r}"


# ── Test 2: CI green → no modification to .gate-status ────────────────────────

def test_ci_green_does_not_modify_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When CI is green, the watchdog does not touch .gate-status."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count": 0, "last_tool": "write"}')
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    original_content = "lint PASS 0\ntypecheck PASS 0\n=== GATE: PASSED ===\n"
    gate_status.write_text(original_content)

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    assert gate_status.exists()
    content = gate_status.read_text()
    assert content == original_content, (
        f"Gate-status should not be modified when CI is green.\n"
        f"Original: {original_content!r}\n"
        f"Got:      {content!r}"
    )


# ── Test 3: Gate already red with real FAIL → not overwritten ─────────────────

def test_gate_already_red_not_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When .gate-status already has a real FAIL (not CI-injected), the
    watchdog does not overwrite it with a CI-FAIL line."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count": 0, "last_tool": "write"}')
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    original_content = (
        "lint PASS 0\n"
        "typecheck FAIL 12\n"
        "collect FAIL 3\n"
        "=== GATE: FAILED ===\n"
    )
    gate_status.write_text(original_content)

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "99999"))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    assert gate_status.exists()
    content = gate_status.read_text()
    assert "typecheck FAIL 12" in content, f"Real FAIL should be preserved, got: {content!r}"
    assert "99999" not in content, (
        f"CI run ID should NOT appear when gate was already red, got: {content!r}"
    )



# ── Test 4: _has_pending_work() tracks local gate state ───────────────────────

def test_has_pending_work_includes_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """_pending_work_exists checks local state; CI is handled by check_and_reset."""
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")

    (tmp_path / "TASKS.md").write_text("- [x] all done" + chr(10))
    (tmp_path / "ratchet.yml").write_text("# empty" + chr(10))
    (tmp_path / ".gate-status").write_text(chr(10).join(["lint PASS 0", "typecheck PASS 0", ""]))

    assert _pending_work_exists() is False, (
        "_pending_work_exists() should be False when local state is clean"
    )
    assert _gate_status_is_red() is False, (
        "_gate_status_is_red() should be False when gate is clean"
    )

    (tmp_path / ".gate-status").write_text(chr(10).join(["lint PASS 0", "typecheck FAIL 2", ""]))
    assert _gate_status_is_red() is True, (
        "_gate_status_is_red() should be True when gate has FAIL line"
    )
    assert _pending_work_exists() is True, (
        "_pending_work_exists() should be True when gate has FAIL"
    )


# ── Test 5: Heartbeat reflects CI-status injection ───────────────────────────

def test_heartbeat_reflects_ci_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """After CI injection, heartbeat JSON records CI pending/red and keeps
    gate_status_red false because local gate evidence was not overwritten."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text("{\"count\": 0, \"last_tool\": \"write\"}")
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text(chr(10).join(["lint PASS 0", "typecheck PASS 0", "=== GATE: PASSED ===", ""]))

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "424242"))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    heartbeat_path = tmp_path / "heartbeat.json"
    assert heartbeat_path.exists(), "Heartbeat file should be written"
    heartbeat = json.loads(heartbeat_path.read_text())

    assert heartbeat["gate_status_red"] is False, (
        f"gate_status_red should stay False when only CI is pending, got: {heartbeat}"
    )
    assert heartbeat["ci_pending_or_red"] is True, (
        f"ci_pending_or_red should be True, got: {heartbeat}"
    )

    actual_ci_run_id = heartbeat["ci_run_id"]
    assert actual_ci_run_id == "424242", (
        f"ci_run_id should be 424242, got: {actual_ci_run_id!r}"
    )
    assert heartbeat["has_pending_work"] is True, (
        f"has_pending_work should be True after CI injection, got: {heartbeat}"
    )

# ── Test 6: CI "pending" with NO run ID (unpushed commit) → do NOT inject ─────

def test_ci_pending_no_run_id_does_not_inject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When _ci_is_pending_or_red returns (True, None) — meaning ci-verdict
    found NO actual CI run for the local HEAD (e.g., the commit hasn't been
    pushed yet) — the watchdog must NOT overwrite .gate-status.

    Injecting a CI-FAIL when no CI run exists creates a chicken-and-egg: the
    commit can never land (gate red) and CI can never start (no push). Only
    inject when a concrete run_id exists, which indicates a real CI run is
    pending or red for an already-pushed commit.
    """
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count": 0, "last_tool": "write"}')
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    original_content = (
        "lint PASS 0\ntypecheck PASS 0\ncollect PASS 0\n=== GATE: PASSED ===\n"
    )
    gate_status.write_text(original_content)

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, None))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    content = gate_status.read_text()
    assert content == original_content, (
        f"Gate-status must NOT be overwritten when no CI run exists "
        f"(run_id=None — unpushed commit).\n"
        f"Original: {original_content!r}\n"
        f"Got:      {content!r}"
    )
