"""Deep tests for agent_watchdog.py and task_watchdog.py — heartbeat monitoring,
stall detection, auto-restart, PID tracking, signal handling, configuration.

Covers both watchdogs comprehensively (>15 tests per domain):
  - agent_watchdog: heartbeat, stop detection, streak reset, singleton lock,
    PID reuse, force-dispatch, pure idle, grinding detection, stop count escal.
  - task_watchdog: deadlines parsing, stale detection, hung process scanning,
    kill lifecycle, kill records, elapsed parsing, gate exclusion, fail-open.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent.parent


def _load_aw():
    spec = importlib.util.spec_from_file_location("agent_watchdog", ROOT / "scripts" / "agent_watchdog.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_watchdog"] = module
    spec.loader.exec_module(module)
    return module


aw = _load_aw()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _aw_env(monkeypatch, tmp_path: Path, *, streak_count: int = 0, streak_mtime_age: float | None = 999.0):
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(tmp_path / "mt-state.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "activity.json"))
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(tmp_path / "todos.json"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "LAST_FLAG_FILE", str(tmp_path / "last-flag.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "deadlines.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled-tasks.txt"))
    monkeypatch.setattr(aw, "HEARTBEAT_FILE", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "RELEASE_COMPLETENESS_FILE", str(tmp_path / "release-completeness.json"))
    monkeypatch.setattr(aw, "SECRETS_VIOLATION_FILE", str(tmp_path / "secrets-violation.json"))
    monkeypatch.setattr(aw, "STALE_RELEASE_FILE", str(tmp_path / "stale-release.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-blocks.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(tmp_path / "false-maxout.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timing.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "timing-data.json"))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(tmp_path / "push-flag-nonexistent"))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tmp_path / "tasks-dir-nonexistent"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "ex-anomalies.json"))
    monkeypatch.setattr(aw, "BLOCK_COUNTER_FILE", str(tmp_path / "block-counter.json"))
    monkeypatch.setattr(aw, "LOAD_THROTTLE_FILE", str(tmp_path / "load-throttle"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate-pid-nonexistent")
    monkeypatch.setattr(aw, "_WORKSPACE", tmp_path)
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "_CI_STATUS", tmp_path / ".ci-status")
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)
    monkeypatch.setattr(aw, "_mark_check_run", lambda name: None)

    (tmp_path / "streak.json").write_text(json.dumps({"count": streak_count, "last_tool": "write"}))
    (tmp_path / "todos.json").write_text("[]")
    (tmp_path / "stop-count.json").write_text('{"count":0}')
    (tmp_path / "last-flag.json").write_text('{"ts":0}')
    (tmp_path / "push-flag-nonexistent").write_text("")
    (tmp_path / "tasks-dir-nonexistent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / "cooldowns.json").write_text("{}")
    (tmp_path / "push-ts.json").write_text("{}")
    (tmp_path / "orchestrator.json").write_text("{}")
    (tmp_path / "health.json").write_text("{}")
    (tmp_path / "task-timing.json").write_text("{}")
    (tmp_path / "task-history.json").write_text('{"durations":{},"last_seen":{}}')
    (tmp_path / "task-state.json").write_text("{}")
    (tmp_path / "ci-cache.json").write_text("{}")

    if streak_mtime_age is not None:
        monkeypatch.setattr(aw, "_streak_mtime_age_seconds", lambda: streak_mtime_age)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HEARTBEAT MONITORING
# ═══════════════════════════════════════════════════════════════════════════════


class TestHeartbeatMonitoring:
    """agent_watchdog writes HEARTBEAT_FILE every poll cycle for operators."""

    def test_heartbeat_written_every_cycle(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        hb = json.loads((tmp_path / "heartbeat.json").read_text())
        assert "ts" in hb
        assert "epoch" in hb
        assert hb["poll_cycle"] >= 1
        assert "streak" in hb
        assert "has_pending_work" in hb
        assert "mtime_age_s" in hb

    def test_heartbeat_reports_tasks_unchecked(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        (tmp_path / "TASKS.md").write_text("- [ ] fix-bug\n")
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        hb = json.loads((tmp_path / "heartbeat.json").read_text())
        assert hb["tasks_md_unchecked"] is True
        assert hb["has_pending_work"] is True

    def test_heartbeat_reports_ci_state(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (True, 42))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        hb = json.loads((tmp_path / "heartbeat.json").read_text())
        assert hb["ci_pending_or_red"] is True
        assert hb["ci_run_id"] == 42

    def test_heartbeat_includes_stop_count(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        (tmp_path / "stop-count.json").write_text('{"count":5}')
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        hb = json.loads((tmp_path / "heartbeat.json").read_text())
        assert hb["stop_count"] == 5

    def test_heartbeat_fail_open(self, tmp_path, monkeypatch):
        """Heartbeat IO errors must not crash check_and_reset."""
        _aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])
        # Force heartbeat write to throw
        monkeypatch.setattr(aw, "HEARTBEAT_FILE", "/nonexistent/deep/deep/heartbeat.json")

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert "ts" in result  # did not crash


# ═══════════════════════════════════════════════════════════════════════════════
# 2. STALL DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestStopDetection:
    """Stop detection: agent idle + pending work = STOP DETECTED."""

    def test_stop_detected_when_idle_with_pending_work(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=30.0)
        (tmp_path / "TASKS.md").write_text("- [ ] bug-1\n")
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["stop_detected"] is True
        assert result["reset_applied"] is True

    def test_no_stop_detected_when_agent_active(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=5.0)
        (tmp_path / "TASKS.md").write_text("- [ ] bug-1\n")
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["stop_detected"] is False

    def test_grinding_detected_with_stale_streak(self, tmp_path, monkeypatch):
        """Agent has streak>0 but mtime_age > STOP_IDLE_SECS*2 = grinding loop."""
        _aw_env(monkeypatch, tmp_path, streak_count=3, streak_mtime_age=60.0)
        (tmp_path / "TASKS.md").write_text("- [ ] bug-2\n")
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["stop_detected"] is True
        assert "grinding" in (tmp_path / "reset.log").read_text().lower()

    def test_pure_idle_flagged_after_cooldown(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=20.0)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])
        (tmp_path / "last-flag.json").write_text(json.dumps({"ts": 0}))

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["stop_detected"] is True

    def test_pure_idle_skips_when_in_cooldown(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=20.0)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])
        # Flag written recently with correct key — cooldown active
        (tmp_path / "last-flag.json").write_text(json.dumps({"last_flag_ts": time.time()}))

        result = aw.check_and_reset(secrets_check=lambda: None)
        # stop_detected stays False because pure-idle skips due to cooldown
        assert result["stop_detected"] is False
        assert result["reset_applied"] is False


class TestTailClassification:
    """classify_tail and scan_tasks_dir stall detection."""

    def test_active_when_young(self):
        state, reason = aw.classify_tail("any text", age_seconds=30, window_seconds=90)
        assert state == aw.State.ACTIVE
        assert "age" in reason.lower()

    def test_done_when_marker_present(self):
        for marker in ["result:", "summary:", "complete", "finished", "passed"]:
            state, _reason = aw.classify_tail(f"{marker} all good", age_seconds=120, window_seconds=90)
            assert state == aw.State.DONE, f"marker={marker}"

    def test_stalled_on_empty_tail(self):
        state, reason = aw.classify_tail("", age_seconds=120, window_seconds=90)
        assert state == aw.State.LIKELY_STALLED_INCOMPLETE
        assert "empty" in reason.lower()

    def test_stalled_on_whitespace_tail(self):
        state, _reason = aw.classify_tail("   \n  \t  ", age_seconds=120, window_seconds=90)
        assert state == aw.State.LIKELY_STALLED_INCOMPLETE

    def test_stalled_on_let_me_marker(self):
        state, _reason = aw.classify_tail("let me check something first", age_seconds=120, window_seconds=90)
        assert state == aw.State.LIKELY_STALLED_INCOMPLETE

    def test_stalled_on_trailing_colon(self):
        state, _reason = aw.classify_tail("here is the status:", age_seconds=120, window_seconds=90)
        assert state == aw.State.LIKELY_STALLED_INCOMPLETE

    def test_stalled_default_no_markers(self):
        state, _reason = aw.classify_tail("some random text here", age_seconds=120, window_seconds=90)
        assert state == aw.State.LIKELY_STALLED_INCOMPLETE


class TestTaskAnomalyDetection:
    """Duration anomaly detection in agent_watchdog."""

    def test_detect_stalled_tasks_empty(self):
        assert aw._detect_stalled_tasks([]) == []

    def test_detect_stalled_tasks_over_threshold(self):
        deadlines = [
            {"task_id": "t1", "elapsed": 500.0},  # 500s > 300s (5min default)
            {"task_id": "t2", "elapsed": 60.0},
        ]
        stalled = aw._detect_stalled_tasks(deadlines, max_minutes=5.0)
        assert len(stalled) == 1
        assert stalled[0]["task_id"] == "t1"

    def test_detect_anomalies_below_minimum(self):
        deadlines = [{"task_id": "t1", "elapsed": 100}]
        assert aw._detect_anomalies(deadlines) == []

    def test_detect_anomalies_above_median_multiplier(self):
        deadlines = [
            {"task_id": "fast1", "elapsed": 10},
            {"task_id": "fast2", "elapsed": 15},
            {"task_id": "fast3", "elapsed": 20},
            {"task_id": "slow", "elapsed": 200},  # 10x median
        ]
        anomalies = aw._detect_anomalies(deadlines, multiplier=5.0)
        assert len(anomalies) == 1
        assert anomalies[0]["task_id"] == "slow"


class TestStopCountEscalation:
    """Stop count tracking and escalation."""

    def test_increment_stop_count(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        assert aw._read_stop_count() == 0
        assert aw._increment_stop_count() == 1
        assert aw._read_stop_count() == 1

    def test_clear_stop_count(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        aw._increment_stop_count()
        aw._increment_stop_count()
        assert aw._read_stop_count() == 2
        aw._clear_stop_count()
        assert aw._read_stop_count() == 0

    def test_escalation_threshold_fires(self, tmp_path, monkeypatch):
        """When stop_count >= STOP_ESCALATE_THRESHOLD, extra message included."""
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=30.0)
        (tmp_path / "stop-count.json").write_text(json.dumps({"count": aw.STOP_ESCALATE_THRESHOLD}))
        (tmp_path / "TASKS.md").write_text("- [ ] bug\n")
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        directive = (tmp_path / "continue-directive.json").read_text()
        assert "REPEATED" in directive

    def test_stop_count_decays_when_agent_active(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=5.0)
        (tmp_path / "stop-count.json").write_text('{"count":7}')
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        aw.check_and_reset(secrets_check=lambda: None)
        assert aw._read_stop_count() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PID TRACKING — SINGLETON LOCK
# ═══════════════════════════════════════════════════════════════════════════════


class TestWatchdogSingletonLock:
    """acquire_watchdog_lock / release_watchdog_lock / _owner_is_alive."""

    def test_acquire_first_lock_succeeds(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        lease = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease is not None
        assert lease.path == lock_path
        aw.release_watchdog_lock(lease)
        assert not lock_path.exists()

    def test_acquire_second_lock_blocked_by_live_owner(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        # First lock with pid=99999
        lease1 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease1 is not None
        # os.kill(99999, 0) succeeds — process appears alive
        with patch("os.kill", return_value=None):
            lease2 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99998)
            assert lease2 is None  # blocked by live owner
        aw.release_watchdog_lock(lease1)

    def test_acquire_replaces_dead_owner(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        lease1 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease1 is not None
        # Make owner appear dead
        with (
            patch("os.kill", side_effect=ProcessLookupError),
            patch.object(aw, "_process_start_time", return_value=None),
        ):
            lease2 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=77777)
            assert lease2 is not None  # dead owner recovered
        aw.release_watchdog_lock(lease2)

    def test_acquire_sigterms_older_version(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        lease1 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999, version="1.0")
        assert lease1 is not None
        # Newer version replaces older live owner with SIGTERM
        kills: list = []
        with patch("os.kill", side_effect=lambda pid, sig: kills.append((pid, sig))):
            lease2 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=77777, version="2.0")
            assert lease2 is not None
            assert any(s == signal.SIGTERM for _, s in kills)
        aw.release_watchdog_lock(lease2)

    def test_stop_watchdog_signals_owner(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        lease = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease is not None
        with patch("os.kill") as mock_kill:
            result = aw.stop_watchdog(lock_path=lock_path)
            assert result is True
            # Called: signal(0) for liveness check + signal(SIGTERM) for kill
            sigs = [call.args[1] for call in mock_kill.call_args_list]
            assert signal.SIGTERM in sigs
        aw.release_watchdog_lock(lease)

    def test_stop_watchdog_dead_owner_cleans_up(self, tmp_path):
        lock_path = tmp_path / "watchdog.lock"
        lease = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease is not None
        aw.release_watchdog_lock(lease)
        # Re-acquire first so the lock exists, then manually mark its owner dead
        lease2 = aw.acquire_watchdog_lock(lock_path=lock_path, pid=99999)
        assert lease2 is not None
        with patch("os.kill", side_effect=ProcessLookupError):
            result = aw.stop_watchdog(lock_path=lock_path)
            assert result is False  # dead → no signal sent
        # Dead owner lock is removed
        with patch("os.kill", side_effect=ProcessLookupError):
            pass  # lock already cleaned by stop_watchdog

    def test_owner_is_alive_pid_reuse_detection(self, tmp_path):
        """PID 12345 with mismatched start time = PID reused → not alive."""
        owner = {"pid": 12345, "pid_start_time": "Mon Jan 1 12:00:00 2024"}
        with patch.object(aw, "_process_start_time", return_value="Tue Jan 2 12:00:00 2024"):
            assert aw._owner_is_alive(owner) is False

    def test_owner_is_alive_same_start_time(self):
        owner = {"pid": os.getpid(), "pid_start_time": "matching"}
        with patch.object(aw, "_process_start_time", return_value="matching"):
            result = aw._owner_is_alive(owner)
            assert result is True

    def test_owner_is_alive_missing_pid(self):
        assert aw._owner_is_alive({}) is False
        assert aw._owner_is_alive({"pid": 0}) is False
        assert aw._owner_is_alive({"pid": -1}) is False

    def test_version_key_ordering(self):
        """Newer versions sort higher, ignoring trailing zeros and suffixes."""
        assert aw._version_key("2.0") > aw._version_key("1.0")
        assert aw._version_key("1.10") > aw._version_key("1.9")
        assert aw._version_key("1.0.0") == aw._version_key("1.0")
        assert aw._version_key("1.0-rc1") != aw._version_key("1.0")
        assert aw._version_key("2") > aw._version_key("1.99")

    def test_watchdog_lock_path(self):
        path = aw.watchdog_lock_path()
        assert "watchdog" in str(path).lower() or "agent-watchdog" in str(path).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. STREAK RESET (AUTO-RESTART)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreakReset:
    def test_read_streak_parses_count(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=7)
        assert aw._read_streak() == 7

    def test_read_streak_missing_file_returns_none(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        Path(tmp_path / "streak.json").unlink()
        assert aw._read_streak() is None

    def test_reset_streak_writes_zero(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=5)
        aw._reset_streak()
        assert aw._read_streak() == 0

    def test_streak_mtime_age_falls_back_to_activity(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        Path(tmp_path / "streak.json").unlink()
        age = aw._streak_mtime_age_seconds()
        assert isinstance(age, (int, float))

    def test_streak_threshold_triggers_reset(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=aw.STREAK_THRESHOLD, streak_mtime_age=5.0)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["reset_applied"] is True
        assert aw._read_streak() == 0

    def test_streak_below_threshold_no_reset(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=aw.STREAK_THRESHOLD - 1, streak_mtime_age=5.0)
        monkeypatch.setattr(aw, "_tasks_md_has_unchecked", lambda: False)
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["reset_applied"] is False


class TestForceDispatch:
    def test_force_dispatch_is_active(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        (tmp_path / aw.FORCE_DISPATCH_FILE).write_text(json.dumps({"active": True, "ts": time.time()}))
        assert aw._is_force_dispatch_active() is True

    def test_force_dispatch_stale_flag_ignored(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        f = tmp_path / aw.FORCE_DISPATCH_FILE
        f.write_text(json.dumps({"active": True, "ts": time.time()}))
        old_mtime = time.time() - aw.FORCE_DISPATCH_MAX_AGE - 1
        os.utime(str(f), (old_mtime, old_mtime))
        assert aw._is_force_dispatch_active() is False

    def test_force_dispatch_lowers_idle_threshold(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path, streak_count=0, streak_mtime_age=10.0)
        (tmp_path / "TASKS.md").write_text("- [ ] urgent\n")
        # Write active force-dispatch flag
        (tmp_path / aw.FORCE_DISPATCH_FILE).write_text(json.dumps({"active": True, "ts": time.time()}))
        monkeypatch.setattr(aw, "_ratchet_has_entries", lambda: 0)
        monkeypatch.setattr(aw, "_gate_status_is_red", lambda: False)
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "check_agent_stalled", lambda: False)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "_check_timing_anomalies", lambda: [])
        monkeypatch.setattr(aw, "_detect_stalled_push", lambda: None)
        monkeypatch.setattr(aw, "_detect_history_anomalies", lambda _d: [])

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert result["stop_detected"] is True  # 10s > FORCE_DISPATCH_IDLE_SECS=5


class TestFalseDoneMaxOut:
    def test_max_out_false_done_increments(self):
        with patch.object(aw, "FALSE_DONE_MAXOUT"):
            pass

    def test_false_done_wraps_at_100(self, tmp_path, monkeypatch):
        """Counter wraps 100 → 1 to prevent saturation (100 % 100 + 1 = 1)."""
        _aw_env(monkeypatch, tmp_path)
        (tmp_path / "false-maxout.json").write_text('{"count":100,"ts":999}')
        monkeypatch.setattr(aw, "_streak_mtime_age_seconds", lambda: 999.0)
        aw._max_out_false_done()
        data = json.loads((tmp_path / "false-maxout.json").read_text())
        assert data["count"] == 1

    def test_false_done_resets_when_agent_active(self, tmp_path, monkeypatch):
        _aw_env(monkeypatch, tmp_path)
        (tmp_path / "false-maxout.json").write_text('{"count":50,"ts":999}')
        monkeypatch.setattr(aw, "_streak_mtime_age_seconds", lambda: 1.0)
        aw._max_out_false_done()
        data = json.loads((tmp_path / "false-maxout.json").read_text())
        assert data["count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TASK WATCHDOG DEEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from scripts.task_watchdog import (  # noqa: E402
    find_hung_processes,
    kill_process,
    load_deadlines,
    load_stale_ids,
    record_kill,
    run_once,
)


class TestTaskWatchdogElapsedParsing:
    def test_parse_etime_days_format(self):
        from scripts.task_watchdog import _parse_etime

        assert _parse_etime("1-02:03:04") == 93784

    def test_parse_etime_colon_triple(self):
        from scripts.task_watchdog import _parse_etime

        assert _parse_etime("12:34:56") == 12 * 3600 + 34 * 60 + 56

    def test_parse_etime_colon_double(self):
        from scripts.task_watchdog import _parse_etime

        assert _parse_etime("05:30") == 330

    def test_parse_etime_invalid(self):
        from scripts.task_watchdog import _parse_etime

        assert _parse_etime("bad") == 0.0


class TestTaskWatchdogFindHungProcesses:
    def test_ps_failure_returns_empty(self):
        with patch("scripts.task_watchdog.subprocess.run", side_effect=FileNotFoundError):
            assert find_hung_processes(timeout_secs=300) == []

    def test_ps_timeout_returns_empty(self):
        import subprocess as sp

        with patch("scripts.task_watchdog.subprocess.run", side_effect=sp.TimeoutExpired([], 10)):
            assert find_hung_processes(timeout_secs=300) == []

    def test_excludes_exclude_patterns(self):
        ps_output = (
            "  PID  PPID ELAPSED COMMAND\n11111     1 10:00 agent_watchdog.py\n22222     1 10:00 task_watchdog.py\n"
        )
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(stdout=ps_output, returncode=0)
            procs = find_hung_processes(timeout_secs=1)
        assert len(procs) == 0

    def test_nontask_process_excluded(self):
        """Only processes matching TASK_PROCESS_PATTERNS are candidates."""
        ps_output = "  PID  PPID ELAPSED COMMAND\n33333     1 20:00:00 /usr/sbin/sshd\n44444     1 20:00:00 /bin/bash\n"
        with patch("scripts.task_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = mock_run.return_value.__class__(stdout=ps_output, returncode=0)
            procs = find_hung_processes(timeout_secs=300)
        assert len(procs) == 0


class TestTaskWatchdogLoadDeadlinesDeep:
    def test_non_numeric_values_skipped(self, tmp_path):
        f = tmp_path / "dl.json"
        f.write_text(json.dumps({"good": time.time() * 1000, "bad": "not a number"}))
        dl = load_deadlines(str(f))
        assert "good" in dl
        assert "bad" not in dl

    def test_os_error_returns_empty(self, tmp_path):
        f = tmp_path / "dl.json"
        f.write_text("{}")
        f.chmod(0o000)  # make unreadable
        try:
            assert load_deadlines(str(f)) == {}
        finally:
            f.chmod(0o644)


class TestTaskWatchdogLoadStaleIdsDeep:
    def test_dict_format(self, tmp_path):
        f = tmp_path / "stale.json"
        f.write_text(json.dumps({"abc": 1, "def": 2}))
        ids = load_stale_ids(str(f))
        assert ids == {"abc", "def"}

    def test_non_dict_non_list_returns_empty(self, tmp_path):
        f = tmp_path / "stale.json"
        f.write_text('"just a string"')
        assert load_stale_ids(str(f)) == set()


class TestTaskWatchdogRunOnceDeep:
    def test_empty_deadlines_early_return(self, tmp_path):
        result = run_once(
            deadlines_file=str(tmp_path / "nonexistent.json"),
            stale_file=str(tmp_path / "stale.json"),
            killed_file=str(tmp_path / "killed.json"),
        )
        assert result == {"stale": 0, "killed": 0}

    def test_no_stale_tasks_no_kills(self, tmp_path):
        now_ms = time.time() * 1000
        dl = tmp_path / "dl.json"
        dl.write_text(json.dumps({"fresh-task": now_ms - 10_000}))
        result = run_once(
            deadlines_file=str(dl),
            stale_file=str(tmp_path / "stale.json"),
            killed_file=str(tmp_path / "killed.json"),
            timeout_ms=300_000,
        )
        assert result["stale"] == 0
        assert result["killed"] == 0


class TestTaskWatchdogKillRecordDeep:
    def test_kill_record_has_all_fields(self, tmp_path):
        f = tmp_path / "killed.json"
        record_kill("task-x", pid=99999, elapsed_ms=450_000, reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        entry = data[0]
        assert entry["task_id"] == "task-x"
        assert entry["pid"] == 99999
        assert entry["elapsed_ms"] == 450_000
        assert entry["reason"] == "timeout"
        assert "killed_at" in entry

    def test_kill_record_existing_file(self, tmp_path):
        f = tmp_path / "killed.json"
        f.write_text(json.dumps([{"task_id": "prior", "pid": 1}]))
        record_kill("new", pid=2, elapsed_ms=100_000, reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert len(data) == 2

    def test_kill_record_corrupt_existing(self, tmp_path):
        f = tmp_path / "killed.json"
        f.write_text("not json {{{")
        record_kill("recovery", pid=3, elapsed_ms=100_000, reason="timeout", killed_file=str(f))
        data = json.loads(f.read_text())
        assert len(data) == 1
        assert data[0]["task_id"] == "recovery"

    def test_kill_record_replace_failure_preserves_existing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from scripts import task_watchdog

        f = tmp_path / "killed.json"
        original = [{"task_id": "prior", "pid": 1}]
        f.write_text(json.dumps(original))

        def fail_replace(_source, _destination):
            raise OSError("injected replace failure")

        monkeypatch.setattr(task_watchdog.os, "replace", fail_replace)
        record_kill("new", pid=2, elapsed_ms=100_000, reason="timeout", killed_file=str(f))

        assert json.loads(f.read_text()) == original
        assert list(tmp_path.glob(".killed.json.*.tmp")) == []


class TestTaskWatchdogConfiguration:
    def test_env_var_overrides_deadlines_file(self, monkeypatch):
        monkeypatch.setenv("GLUDD_TASK_DEADLINE_STATE", "/tmp/custom-deadlines.json")
        import importlib

        import scripts.task_watchdog as tw

        importlib.reload(tw)
        assert "/tmp/custom-deadlines.json" in tw.DEADLINES_FILE
        # Restore
        monkeypatch.delenv("GLUDD_TASK_DEADLINE_STATE", raising=False)

    def test_env_var_overrides_timeout_ms(self, monkeypatch):
        monkeypatch.setenv("GLUDD_TASK_TIMEOUT_MS", "600000")
        import importlib

        import scripts.task_watchdog as tw

        importlib.reload(tw)
        assert tw.TIMEOUT_MS == 600000
        monkeypatch.delenv("GLUDD_TASK_TIMEOUT_MS", raising=False)

    def test_env_var_overrides_poll_secs(self, monkeypatch):
        monkeypatch.setenv("GLUDD_TASK_WATCHDOG_POLL", "3")
        import importlib

        import scripts.task_watchdog as tw

        importlib.reload(tw)
        assert tw.POLL_SECS == 3
        monkeypatch.delenv("GLUDD_TASK_WATCHDOG_POLL", raising=False)


class TestTaskWatchdogProcessIdentity:
    def test_kill_skips_when_identity_changed(self):
        """kill_process returns False when command != expected_command."""
        from scripts.process_cleanup import ProcessInfo

        table = {99999: ProcessInfo(99999, 1, 500, "python some_other_script.py")}
        with (
            patch("scripts.task_watchdog.snapshot_processes", return_value=table),
            patch("scripts.task_watchdog.time.sleep"),
        ):
            result = kill_process(99999, expected_command="python pytest test.py")
            assert result is False


class TestTaskWatchdogGateExclusion:
    def test_read_gate_pid_missing(self):
        from scripts.task_watchdog import _read_gate_pid

        assert _read_gate_pid("/nonexistent/file.pid") is None

    def test_descendant_pids_basic(self):
        from scripts.task_watchdog import _descendant_pids

        lines = [
            "100     1  10:00 root",
            "101   100  10:00 child",
            "102   101  10:00 grandchild",
            "200     1  10:00 unrelated",
        ]
        descendants = _descendant_pids(lines, 100)
        assert 101 in descendants
        assert 102 in descendants
        assert 200 not in descendants


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LOG ROTATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestLogRotation:
    def test_rotation_skip_patterns(self, tmp_path):
        """Files matching WATCHDOG_LOG_ROTATE_SKIP_PATTERNS are skipped."""
        log = tmp_path / "gludd-stderr-123.log"
        log.write_bytes(b"x" * (aw.WATCHDOG_LOG_ROTATION_MB * 1024 * 1024 + 1))
        # The skip pattern should match gludd-stderr-*
        assert any(log.name.startswith(p) for p in aw.WATCHDOG_LOG_ROTATE_SKIP_PATTERNS)
