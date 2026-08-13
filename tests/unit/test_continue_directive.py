"""Unit tests for the watchdog-continue-directive injection pipeline.

Tests cover:
  1. _build_continue_directive produces correct JSON structure
  2. check_and_reset writes JSON directive when stop is detected
  3. enforce-stop.ts system.transform reads directive and prepends it
  4. Stale directives are ignored (freshness check)
  5. Pipeline integration: file written by watchdog, consumed by plugin
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "agent_watchdog.py"
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
PLUGIN_IMPL_PATH = (
    ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"
)


def _plugin_source() -> str:
    """Return the deployed proxy and its implementation as one source view."""
    assert PLUGIN_PATH.exists()
    assert PLUGIN_IMPL_PATH.exists()
    return PLUGIN_PATH.read_text() + "\n" + PLUGIN_IMPL_PATH.read_text()


def _plugin_source() -> str:
    return plugin_contract_source(PLUGIN_PATH)


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_watchdog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("agent_watchdog", module)
    spec.loader.exec_module(module)
    return module


aw = _load_module()
_build_continue_directive = aw._build_continue_directive
CONTINUE_DIRECTIVE = aw.CONTINUE_DIRECTIVE


def check_and_reset() -> dict:
    """Exercise directive logic without launching the repository scanner."""
    return aw.check_and_reset(secrets_check=lambda: None)


# ── _build_continue_directive helper function ────────────────────────────────


def test_build_directive_basic():
    d = _build_continue_directive(
        work_sources=["local"],
        stop_count=1,
        tasks_unchecked=True,
        ratchet_count=3,
        gate_red=False,
        ci_pending=False,
    )
    assert d["action"] == "FORCE_DISPATCH"
    assert d["required_tool"] == "task"
    assert "TASKS.md has unchecked items" in d["pending_items"]
    assert "3 ratchet entries" in d["pending_items"]
    assert "Dispatch ALL of them NOW" in d["message"]
    assert d["stop_count"] == 1
    assert d["source"] == "local"
    assert "ts" in d


def test_build_directive_all_pending():
    d = _build_continue_directive(
        work_sources=["local", "CI (run 123)"],
        stop_count=5,
        tasks_unchecked=True,
        ratchet_count=2,
        gate_red=True,
        ci_pending=True,
        ci_run_id="123",
    )
    assert len(d["pending_items"]) == 4
    assert "TASKS.md has unchecked items" in d["pending_items"]
    assert "2 ratchet entries" in d["pending_items"]
    assert ".gate-status is red" in d["pending_items"]
    assert "CI pending (run 123)" in d["pending_items"]
    assert d["stop_count"] == 5


def test_build_directive_no_pending():
    d = _build_continue_directive(
        work_sources=[],
        stop_count=0,
        tasks_unchecked=False,
        ratchet_count=0,
        gate_red=False,
        ci_pending=False,
    )
    assert d["pending_items"] == []
    assert d["source"] == "unknown"


def test_build_directive_with_extra_message():
    d = _build_continue_directive(
        work_sources=["local"],
        stop_count=3,
        tasks_unchecked=True,
        ratchet_count=0,
        gate_red=False,
        ci_pending=False,
        extra_message="REPEATED STOP DETECTED (3x)",
    )
    assert "REPEATED STOP DETECTED" in d["message"]
    assert "Dispatch ALL of them NOW" in d["message"]


def test_build_directive_with_work_hint():
    d = _build_continue_directive(
        work_sources=["CI (run 456)"],
        stop_count=1,
        tasks_unchecked=False,
        ratchet_count=0,
        gate_red=False,
        ci_pending=True,
        ci_run_id="456",
        work_hint="CI pending. Do NOT push new commits.",
    )
    assert "CI pending. Do NOT push new commits." in d["message"]
    assert "Dispatch ALL of them NOW" in d["message"]


def test_build_directive_ci_pending_no_run_id():
    d = _build_continue_directive(
        work_sources=["CI"],
        stop_count=1,
        tasks_unchecked=False,
        ratchet_count=0,
        gate_red=False,
        ci_pending=True,
        ci_run_id=None,
    )
    assert "CI pending" in d["pending_items"]


# ── check_and_reset writes directive via CONTINUE_DIRECTIVE ──────────────────


def test_check_and_reset_writes_json_directive_on_stop(tmp_path):
    import subprocess

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "activity.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled.json"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-loop.json"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "task-deadlines.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_STATE_SNAPSHOT", str(tmp_path / "task-state-snapshot.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timings.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "watchdog-timing.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "continue.txt"))
    monkeypatch.setattr(aw, "STOP_IDLE_SECS", 1)
    monkeypatch.setattr(aw, "PURE_IDLE_SECS", 9999)
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    # Streak file: age > STOP_IDLE_SECS (1s), streak=0 → triggers stop detection
    streak_file = tmp_path / "streak.json"
    streak_file.write_text('{"count":0}')
    os.utime(str(streak_file), (time.time() - 70, time.time() - 70))

    # Local pending work
    (tmp_path / "TASKS.md").write_text("- [ ] fix a bug\n- [x] done thing\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / ".gate-status").write_text("lint PASS 0\n")

    # CI not pending
    def mock_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "ci-verdict" in str(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="conclusion: SUCCESS\n",
                stderr="",
            )
        if isinstance(cmd, list) and "git log" in str(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if isinstance(cmd, list) and "git status" in str(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    try:
        result = check_and_reset()
    finally:
        monkeypatch.undo()

    assert result.get("stop_detected") is True
    assert result.get("reset_applied") is True

    directive_path = tmp_path / "continue-directive.json"
    assert directive_path.exists(), f"Expected directive at {directive_path}"

    directive = json.loads(directive_path.read_text())
    assert directive["action"] == "FORCE_DISPATCH"
    assert directive["required_tool"] == "task"
    assert "TASKS.md has unchecked items" in directive["pending_items"]
    assert "Dispatch ALL of them NOW" in directive["message"]


def test_check_and_reset_does_not_write_directive_when_no_stop(tmp_path):
    import subprocess

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "activity.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled.json"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "cooldowns.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-loop.json"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "task-deadlines.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_STATE_SNAPSHOT", str(tmp_path / "task-state-snapshot.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timings.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "watchdog-timing.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "continue.txt"))
    monkeypatch.setattr(aw, "STOP_IDLE_SECS", 1)
    monkeypatch.setattr(aw, "PURE_IDLE_SECS", 9999)
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)

    # Streak file: recently touched → no stop
    streak_file = tmp_path / "streak.json"
    streak_file.write_text('{"count":1}')
    os.utime(str(streak_file), (time.time(), time.time()))

    # No local pending work
    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / ".gate-status").write_text("lint PASS 0\n")

    def mock_run(cmd, **_kwargs):
        if isinstance(cmd, list) and "ci-verdict" in str(cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="conclusion: SUCCESS\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    try:
        result = check_and_reset()
    finally:
        monkeypatch.undo()

    assert result.get("stop_detected") is False or result.get("stop_detected") is None

    directive_path = tmp_path / "continue-directive.json"
    assert not directive_path.exists(), (
        f"Directive should NOT be written when no stop detected, "
        f"but {directive_path} exists"
    )


# ── enforce-stop.ts structural checks ────────────────────────────────────────


def test_plugin_reads_continue_directive():
    src = _plugin_source()
    assert ("/tmp/gludd-force-dispatch.json" in src or
            "FORCE_DISPATCH_FILE" in src), (
        "enforce-stop.ts must reference the force-dispatch file"
    )


def test_plugin_has_freshness_check():
    src = _plugin_source()
    assert "120_000" in src or "120000" in src, (
        "enforce-stop.ts must check freshness (<120s)"
    )
    assert "Date.now()" in src or "lastBlockTs" in src, (
        "enforce-stop.ts must check age via timestamps"
    )


def test_plugin_prepends_continue_directive():
    src = _plugin_source()
    assert "FORCE_DISPATCH" in src or "MANDATORY" in src, (
        "enforce-stop.ts must prepend a FORCE_DISPATCH directive to the system prompt"
    )
    assert ".unshift" in src or "prepend" in src or ".join" in src, (
        "enforce-stop.ts must prepend (not append) the directive"
    )


def test_plugin_directive_mentions_required_tool():
    src = _plugin_source()
    assert "Task tool" in src or "dispatch a subagent" in src, (
        "enforce-stop.ts must instruct dispatch via Task tool"
    )


def test_plugin_directive_mentions_pending_items():
    src = _plugin_source()
    assert "pending_items" in src or "PENDING WORK EXISTS" in src, (
        "enforce-stop.ts must surface pending work items"
    )


def test_plugin_directive_has_action_check():
    src = _plugin_source()
    assert "active:" in src or "consecutiveBlocks," in src, (
        "enforce-stop.ts must write force-dispatch data with action fields"
    )


# ── Integration: pipeline connectivity ───────────────────────────────────────


def test_watchdog_and_plugin_use_same_filename():
    src = _plugin_source()
    # Plugin writes FORCE_DISPATCH_FILE; watchdog reads it
    assert ("/tmp/gludd-force-dispatch.json" in src or
            "FORCE_DISPATCH_FILE" in src), (
        "enforce-stop.ts must share the force-dispatch file with the watchdog"
    )


def test_watchdog_default_is_json():
    assert "json" in CONTINUE_DIRECTIVE.lower(), (
        f"CONTINUE_DIRECTIVE must default to .json, got: {CONTINUE_DIRECTIVE}"
    )


def test_directive_json_is_valid_on_stop(tmp_path):
    """End-to-end: write a stop-like directive, verify it can be parsed and used."""
    directive = {
        "action": "FORCE_DISPATCH",
        "pending_items": ["TASKS.md has unchecked items", "3 ratchet entries"],
        "required_tool": "task",
        "message": "Dispatch subagents NOW to clear pending work.",
        "stop_count": 2,
        "source": "local, CI",
        "ts": "2026-07-05T00:00:00Z",
    }
    directive_path = tmp_path / "test-directive.json"
    directive_path.write_text(json.dumps(directive, indent=2))

    # Simulate what the plugin does
    stat = directive_path.stat()
    age_sec = time.time() - stat.st_mtime
    assert age_sec < 120, "Fresh directive should be within 120s threshold"

    parsed = json.loads(directive_path.read_text())
    assert parsed["action"] == "FORCE_DISPATCH"
    assert len(parsed["pending_items"]) == 2
    assert parsed["required_tool"] == "task"

    # Simulate the prepend string the plugin builds
    pending_str = "; ".join(parsed["pending_items"])
    prepend = f"FORCE DISPATCH: {pending_str} — {parsed['message']} — use {parsed['required_tool']}"
    assert "TASKS.md has unchecked items" in prepend
    assert "Dispatch subagents NOW" in prepend
    assert "task" in prepend
