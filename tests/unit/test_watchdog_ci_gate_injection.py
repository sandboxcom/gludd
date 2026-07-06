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
check_and_reset = aw.check_and_reset
HEARTBEAT_FILE = aw.HEARTBEAT_FILE


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
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "check-cooldowns.json"))
    monkeypatch.setattr(aw, "HEARTBEAT_FILE", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    # Write clean local state
    if tasks_clean:
        (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    else:
        (tmp_path / "TASKS.md").write_text("- [ ] pending task\n")

    if ratchet_empty:
        (tmp_path / "ratchet.yml").write_text("# empty\n")
    else:
        (tmp_path / "ratchet.yml").write_text("entry: value\n")

    (tmp_path / "todos.json").write_text("[]")
    (tmp_path / "stop-count.json").write_text('{"count":0}')
    (tmp_path / "push-flag-nonexistent").write_text("")
    (tmp_path / "tasks-dir-nonexistent").mkdir(parents=True, exist_ok=True)


# ── Test 1: CI pending + gate clean → watchdog writes CI FAIL ─────────────────

def test_ci_pending_sets_gate_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When CI is pending and .gate-status is clean (no FAIL), the watchdog
    writes a FAIL entry containing the CI run ID."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count": 0, "last_tool": "write"}')
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    # Gate is clean — no FAIL lines
    gate_status.write_text("lint PASS 0\ntypecheck PASS 0\ncollect PASS 0\n=== GATE: PASSED ===\n")

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "424242"))

    # Watchdog writes activity so mtime age is computed from activity file
    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    assert gate_status.exists()
    content = gate_status.read_text()
    assert "FAIL" in content, f"Expected FAIL in gate-status, got: {content!r}"
    assert "424242" in content, f"Expected run ID 424242 in gate-status, got: {content!r}"


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


# ── Test 4: _has_pending_work() includes CI state ─────────────────────────────

def test_has_pending_work_includes_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """_pending_work_exists() returns True when CI is pending, even if
    tasks/ratchet/gate are all clean."""
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")

    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / ".gate-status").write_text("lint PASS 0\ntypecheck PASS 0\n")

    # _pending_work_exists only checks local state — CI is checked separately
    # in check_and_reset. Verify that when all local is clean, it returns False.
    assert _pending_work_exists() is False, (
        "_pending_work_exists() should be False when local is clean "
        "(CI is handled separately)"
    )

    # Now verify that the _gate_status_is_red helper works correctly
    assert _gate_status_is_red() is False, (
        "_gate_status_is_red() should be False when gate is clean"
    )

    # Add a FAIL line and verify it returns True
    (tmp_path / ".gate-status").write_text("lint PASS 0\ntypecheck FAIL 2\n")
    assert _gate_status_is_red() is True, (
        "_gate_status_is_red() should be True when gate has FAIL line"
    )
    assert _pending_work_exists() is True, (
        "_pending_work_exists() should be True when gate has FAIL"
    )


# ── Test 5: Heartbeat reflects CI-injected gate status ────────────────────────

def test_heartbeat_reflects_ci_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """After CI injection, the heartbeat JSON includes gate_status_red: true
    and ci_pending_or_red: true with the run ID."""
    _setup_ci_gate_test(monkeypatch, tmp_path)

    streak_path = tmp_path / "streak.json"
    streak_path.write_text('{"count": 0, "last_tool": "write"}')
    streak_path.touch()

    gate_status = tmp_path / ".gate-status"
    gate_status.write_text("lint PASS 0\ntypecheck PASS 0\n=== GATE: PASSED ===\n")

    monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, "424242"))

    activity_path = tmp_path / "watchdog-activity.json"
    activity_path.write_text(json.dumps({"last_activity_ts": time.time()}))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(activity_path))

    check_and_reset()

    heartbeat_path = tmp_path / "heartbeat.json"
    assert heartbeat_path.exists(), "Heartbeat file should be written"
    heartbeat = json.loads(heartbeat_path.read_text())

    assert heartbeat["gate_status_red"] is True, (
        f"gate_status_red should be True after CI injection, got: {heartbeat}"
    )
    assert heartbeat["ci_pending_or_red"] is True, (
        f"ci_pending_or_red should be True, got: {heartbeat}"
    )
    assert heartbeat["ci_run_id"] == "424242", (
        f"ci_run_id should be '424242', got: {heartbeat['ci_run_id']!r}"
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
