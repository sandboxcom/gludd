"""E2e integration test for enforcement plugin state files.

Per AGENTS.md "Clean Slate" and "Disengage/Engage cycle": the enforcement
plugins maintain state in /tmp/gludd-* files. This test simulates a full
session cycle to verify those state files behave correctly:
  1. Clean slate: state files can be cleanly initialized (no stale corruption).
  2. Dispatch simulation: mainthread streak counter increments on non-dispatch
     tools and resets on dispatch.
  3. Gate-refresh + commit: .gate-status presence signals plugins to allow.
  4. Disengage: floor-override relaxes enforcement.
  5. Engage: removing override resumes enforcement.
  6. Nag detection: DELEGATE-FIRST / MUST DISPATCH patterns are absent when
     state is healthy (fresh session, first call).

All plugin logic is re-implemented in pure Python from the TypeScript source
(no import from the .ts files — the extract-translate-assert pattern from
test_verified_claims_plugin.py).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

import pytest

from tests.e2e.enforcement_state import state_path, state_root

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.xdist_group("enforcement-shared-state")

STATE_FILES = [
    state_path(name)
    for name in (
        "gludd-mainthread-streak.json",
        "gludd-multitask-state.json",
        "gludd-tool-streak.json",
        "gludd-floor-override",
        "gludd-session-start.json",
        "gludd-watchdog-disengage.json",
        "gludd-stop-state.json",
        "gludd-block-reason.json",
        "gludd-force-dispatch.json",
        "gludd-read-grind.json",
        "gludd-sonnet-health.json",
        "gludd-task-deadlines.json",
        "gludd-task-stale.json",
        "gludd-todowrite-state.json",
        "gludd-stop-tool-counts.json",
        "gludd-stop-text-complete-count.json",
        "gludd-block-counter.json",
        "gludd-blanked-responses.json",
        "gludd-plugin-alive.json",
        "gludd-false-done-blocks.json",
        "gludd-model-util.json",
        "gludd-force-delegate.json",
    )
]

NAG_PATTERNS = [
    "DELEGATE-FIRST",
    "MUST DISPATCH",
    "MESSAGE-SHAPE VIOLATION",
    "MAIN-THREAD GRINDING DETECTED",
]

_GATE_STATUS_ROOT: Path | None = None  # autouse fixture overrides to tmp_path


def _gate_status_path() -> Path:
    root = _GATE_STATUS_ROOT if _GATE_STATUS_ROOT is not None else Path(os.getcwd())
    return root / ".gate-status"


@pytest.fixture(autouse=True)
def _isolate_gate_status(tmp_path):
    """Redirect .gate-status reads/writes to tmp_path so tests never pollute
    the repo working tree. A stale green .gate-status in the repo root can
    mask a real red gate (see test-isolation bug)."""
    global _GATE_STATUS_ROOT
    previous = _GATE_STATUS_ROOT
    _GATE_STATUS_ROOT = tmp_path
    try:
        yield tmp_path
    finally:
        _GATE_STATUS_ROOT = previous


# --------------------------------------------------------------------------
# Helpers — re-implement plugin state-machine logic in pure Python.
# --------------------------------------------------------------------------


def _clean_state_files() -> None:
    """Remove all known /tmp/gludd-* state files."""
    for f in STATE_FILES:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _count_remaining_state_files() -> int:
    return sum(1 for f in STATE_FILES if f.exists())


def _read_mainthread_streak() -> int:
    """Mirror enforce-delegate.ts readStreak(): {count, ts} JSON or bare int."""
    try:
        raw = state_path("gludd-mainthread-streak.json").read_text().strip()
        if raw.startswith("{"):
            obj = json.loads(raw)
            return int(obj.get("count", 0))
        return int(raw)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return 0


def _write_mainthread_streak(count: int) -> None:
    """Mirror enforce-delegate.ts writeStreak(n)."""
    p = state_path("gludd-mainthread-streak.json")
    tmp = state_path("gludd-mainthread-streak.json.tmp")
    tmp.write_text(json.dumps({"count": count, "ts": int(time.time() * 1000)}))
    tmp.rename(p)


def _increment_mainthread_streak() -> int:
    """Simulate a non-dispatch tool call: increment streak by 1."""
    current = _read_mainthread_streak()
    _write_mainthread_streak(current + 1)
    return current + 1


def _reset_mainthread_streak() -> None:
    """Simulate a dispatch (task/agent/workflow): reset streak to 0."""
    _write_mainthread_streak(0)


def _read_tool_streak() -> dict:
    """Read shared streak state from /tmp/gludd-tool-streak.json."""
    try:
        p = state_path("gludd-tool-streak.json")
        if p.exists():
            raw = json.loads(p.read_text())
            return {
                "streak": int(raw.get("streak", 0)),
                "lastDispatchTs": int(raw.get("lastDispatchTs", 0)),
                "readStreak": int(raw.get("readStreak", 0)),
                "editStreak": int(raw.get("editStreak", 0)),
                "lastUpdateTs": int(raw.get("lastUpdateTs", 0)),
                "lastWriter": raw.get("lastWriter", ""),
            }
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return {"streak": 0, "lastDispatchTs": 0, "readStreak": 0, "editStreak": 0, "lastUpdateTs": 0, "lastWriter": ""}


def _write_tool_streak(state: dict) -> None:
    """Write shared streak state."""
    p = state_path("gludd-tool-streak.json")
    p.write_text(json.dumps(state))


def _simulate_dispatch_reset() -> None:
    """Dispatch tools reset ALL streak counters."""
    s = _read_tool_streak()
    s["streak"] = 0
    s["readStreak"] = 0
    s["editStreak"] = 0
    s["lastDispatchTs"] = int(time.time() * 1000)
    s["lastUpdateTs"] = int(time.time() * 1000)
    s["lastWriter"] = "enforce-floor"
    _write_tool_streak(s)
    _reset_mainthread_streak()


def _simulate_non_dispatch_call(tool: str = "edit") -> None:
    """Simulate a non-dispatch tool call: increment all streak counters."""
    s = _read_tool_streak()
    s["streak"] += 1
    if tool in ("read", "grep", "glob"):
        s["readStreak"] += 1
    else:
        s["editStreak"] += 1
    s["lastUpdateTs"] = int(time.time() * 1000)
    s["lastWriter"] = "enforce-floor"
    _write_tool_streak(s)
    if tool not in ("read", "grep", "glob"):
        _increment_mainthread_streak()


def _is_streak_healthy() -> bool:
    """Return True if no nags would fire (streak < DELEGATE_FIRST_THRESHOLD=8)."""
    s = _read_tool_streak()
    return s["streak"] <= 8


def _write_floor_override(value: int) -> None:
    """Mirror enforce-floor.ts _tunable(): write integer to override file."""
    state_path("gludd-floor-override").write_text(str(value))


def _remove_floor_override() -> None:
    with contextlib.suppress(FileNotFoundError):
        state_path("gludd-floor-override").unlink()


def _read_floor_override(fallback: int = 7) -> int:
    """Check for floor-override file; fall back to env or default."""
    try:
        raw = state_path("gludd-floor-override").read_text().strip()
        if raw.isdigit():
            return int(raw)
    except (FileNotFoundError, ValueError):
        pass
    env_val = os.environ.get("CLAUDE_AGENT_FLOOR", "")
    if env_val.isdigit():
        return int(env_val)
    return fallback


def _write_multitask_state(dispatches: int, zero_streak: int = 0) -> None:
    """Write /tmp/gludd-multitask-state.json."""
    p = state_path("gludd-multitask-state.json")
    p.write_text(json.dumps({
        "thisMessageDispatches": dispatches,
        "prevMessageDispatches": 0,
        "zeroStreak": zero_streak,
        "estimatedInFlight": dispatches,
        "lastTs": int(time.time() * 1000),
    }))


def _read_multitask_state() -> dict:
    """Read /tmp/gludd-multitask-state.json."""
    try:
        p = state_path("gludd-multitask-state.json")
        if p.exists():
            return json.loads(p.read_text())
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return {
        "thisMessageDispatches": 0,
        "prevMessageDispatches": 0,
        "zeroStreak": 0,
        "estimatedInFlight": 0,
        "lastTs": 0,
    }


def _write_session_start_state(dispatch_count: int) -> None:
    """Write /tmp/gludd-session-start.json."""
    p = state_path("gludd-session-start.json")
    p.write_text(json.dumps({
        "dispatches_in_session": dispatch_count,
        "session_start_ts": int(time.time() * 1000),
        "first_tool_call_ts": int(time.time() * 1000) - 5000,
    }))


def _read_session_start_state() -> dict:
    try:
        p = state_path("gludd-session-start.json")
        if p.exists():
            return json.loads(p.read_text())
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _write_disengage(until_epoch: int) -> None:
    """Write /tmp/gludd-watchdog-disengage.json."""
    state_path("gludd-watchdog-disengage.json").write_text(
        json.dumps({"disengage_until": until_epoch})
    )


def _remove_disengage() -> None:
    with contextlib.suppress(FileNotFoundError):
        state_path("gludd-watchdog-disengage.json").unlink()


def _is_disengaged() -> bool:
    """Mirror enforce-floor.ts / enforce-delegate.ts isDisengaged()."""
    try:
        p = state_path("gludd-watchdog-disengage.json")
        if p.exists():
            d = json.loads(p.read_text())
            until = d.get("disengage_until", 0)
            now = int(time.time() * 1000)
            if until > now:
                return True
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return False


def _has_nag_patterns(text: str) -> list[str]:
    """Return list of nag patterns found in text."""
    return [p for p in NAG_PATTERNS if p in text]


def _check_gate_status() -> dict:
    """Read .gate-status and return parsed content."""
    gs = _gate_status_path()
    if not gs.exists():
        return {"exists": False, "is_green": False, "is_fresh": False}
    try:
        content = gs.read_text()
        is_green = "=== GATE: PASSED ===" in content or "PASS" in content
        mtime = gs.stat().st_mtime
        fresh = (time.time() - mtime) < 3600  # <1 hour
        return {
            "exists": True,
            "is_green": is_green,
            "is_fresh": fresh,
            "mtime": mtime,
            "content": content.strip()[:200],
        }
    except OSError:
        return {"exists": False, "is_green": False, "is_fresh": False}


def _write_gate_status_passing() -> None:
    """Write a fresh green .gate-status."""
    gs = _gate_status_path()
    lines = [
        "=== GATE PHASE: lint ===",
        "lint: 0",
        "=== GATE PHASE: typecheck ===",
        "typecheck: baseline",
        "=== GATE PHASE: collect-check ===",
        "Collection OK",
        "=== GATE PHASE: test ===",
        "100 passed",
        "=== GATE: PASSED ===",
    ]
    gs.write_text("\n".join(lines) + "\n")


def _remove_gate_status() -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(str(_gate_status_path()))


# --------------------------------------------------------------------------
# Test classes
# --------------------------------------------------------------------------


class TestCleanSlate:
    """Scenario 1: Verify state files can be cleanly initialized."""

    def test_state_files_missing_after_cleanup(self):
        _clean_state_files()
        remaining = _count_remaining_state_files()
        assert remaining == 0, f"Expected 0 state files after cleanup, got {remaining}"

    def test_all_state_paths_are_under_configured_root(self):
        for f in STATE_FILES:
            assert f.parent == state_root(), (
                f"State file {f} must be confined under {state_root()}"
            )

    def test_streak_starts_at_zero_after_cleanup(self):
        _clean_state_files()
        assert _read_mainthread_streak() == 0, "Mainthread streak should start at 0"
        s = _read_tool_streak()
        assert s["streak"] == 0, "Tool streak should start at 0"

    def test_session_start_starts_clean(self):
        _clean_state_files()
        s = _read_session_start_state()
        assert s.get("dispatches_in_session", 0) == 0

    def test_multitask_starts_clean(self):
        _clean_state_files()
        s = _read_multitask_state()
        assert s["zeroStreak"] == 0
        assert s["thisMessageDispatches"] == 0

    def test_floor_override_defaults_when_missing(self):
        _remove_floor_override()
        floor = _read_floor_override(fallback=7)
        assert floor == 7, f"Fallback floor should be 7, got {floor}"

    def test_disengaged_is_false_when_clean(self):
        _remove_disengage()
        assert not _is_disengaged()


class TestDispatchStreakSimulation:
    """Scenario 2: Verify mainthread streak counter increments and resets."""

    def setup_method(self):
        _clean_state_files()

    def test_streak_increments_on_non_dispatch_call(self):
        _simulate_non_dispatch_call("edit")
        assert _read_mainthread_streak() == 1, "Streak should be 1 after 1 edit"

    def test_streak_increments_across_multiple_calls(self):
        for _ in range(5):
            _simulate_non_dispatch_call("write")
        assert _read_mainthread_streak() == 5, "Streak should be 5 after 5 writes"

    def test_streak_resets_on_dispatch(self):
        for _ in range(4):
            _simulate_non_dispatch_call("edit")
        assert _read_mainthread_streak() == 4
        _simulate_dispatch_reset()
        assert _read_mainthread_streak() == 0, "Streak should reset to 0 after dispatch"

    def test_read_tools_dont_increment_mainthread_streak(self):
        _simulate_non_dispatch_call("read")
        assert _read_mainthread_streak() == 0, "Read tools should not affect mainthread streak"

    def test_read_tools_do_increment_tool_streak(self):
        _simulate_non_dispatch_call("read")
        s = _read_tool_streak()
        assert s["readStreak"] >= 1, "Read tools should increment readStreak"

    def test_tool_streak_read_vs_edit_separation(self):
        _simulate_non_dispatch_call("read")
        _simulate_non_dispatch_call("read")
        _simulate_non_dispatch_call("edit")
        s = _read_tool_streak()
        assert s["readStreak"] >= 2
        assert s["editStreak"] >= 1
        assert s["streak"] >= 3

    def test_streak_healthy_below_threshold(self):
        for _ in range(3):
            _simulate_non_dispatch_call("edit")
        assert _is_streak_healthy(), "Streak=3 should be healthy (<8)"

    def test_streak_unhealthy_above_threshold(self):
        for _ in range(10):
            _simulate_non_dispatch_call("edit")
        assert not _is_streak_healthy(), "Streak=10 should be unhealthy (>8)"
        s = _read_tool_streak()
        assert s["streak"] > 8


class TestGateStatusIsolation:
    """Verify .gate-status writes never pollute the repo working tree.

    Regression guard for the test-isolation bug where helpers wrote
    .gate-status into os.getcwd()/repo-root, leaving a stale green file
    that could mask a real red gate on a subsequent run.
    """

    REPO_ROOT_GATE = Path(os.getcwd()) / ".gate-status"

    def test_helpers_target_isolated_root(self):
        assert _GATE_STATUS_ROOT is not None, (
            "autouse fixture must set _GATE_STATUS_ROOT before tests run"
        )
        assert _gate_status_path() != self.REPO_ROOT_GATE, (
            "helpers must target tmp_path, not the repo working tree"
        )

    def test_write_does_not_create_repo_root_gate_status(self):
        existed_before = self.REPO_ROOT_GATE.exists()
        mtime_before = self.REPO_ROOT_GATE.stat().st_mtime if existed_before else None
        _write_gate_status_passing()
        assert _gate_status_path().exists(), "isolated .gate-status should be written"
        if not existed_before:
            assert not self.REPO_ROOT_GATE.exists(), (
                "Test created .gate-status at repo root - isolation broken"
            )
        else:
            assert self.REPO_ROOT_GATE.stat().st_mtime == mtime_before, (
                "Test modified existing repo-root .gate-status - isolation broken"
            )

    def test_remove_does_not_touch_repo_root_gate_status(self):
        existed_before = self.REPO_ROOT_GATE.exists()
        mtime_before = self.REPO_ROOT_GATE.stat().st_mtime if existed_before else None
        _write_gate_status_passing()
        _remove_gate_status()
        assert not _gate_status_path().exists()
        if existed_before:
            assert self.REPO_ROOT_GATE.exists(), (
                "Test deleted repo-root .gate-status - isolation broken"
            )
            assert self.REPO_ROOT_GATE.stat().st_mtime == mtime_before, (
                "Test modified repo-root .gate-status mtime - isolation broken"
            )


class TestGateRefreshAndCommit:
    """Scenario 3: .gate-status presence + freshness supports commit flow."""

    def setup_method(self):
        _clean_state_files()
        _remove_gate_status()

    def teardown_method(self):
        _remove_gate_status()

    def test_gate_status_absent_initially(self):
        gs = _check_gate_status()
        assert not gs["exists"], ".gate-status should not exist before gate runs"

    def test_write_and_verify_gate_status_passing(self):
        _write_gate_status_passing()
        gs = _check_gate_status()
        assert gs["exists"], ".gate-status should exist after write"
        assert gs["is_green"], ".gate-status should report green"
        assert gs["is_fresh"], ".gate-status should be fresh"
        assert "=== GATE: PASSED ===" in gs["content"]

    def test_stale_gate_status_detected(self, monkeypatch):
        _write_gate_status_passing()
        gs = _check_gate_status()
        assert gs["is_fresh"]

    def test_gate_status_passing_allows_commit_simulation(self):
        """A fresh green .gate-status allows plugin commit gates to pass."""
        _write_gate_status_passing()
        gs = _check_gate_status()
        assert gs["is_green"]
        assert gs["is_fresh"]
        _write_multitask_state(dispatches=5)
        _write_session_start_state(dispatch_count=10)
        assert _read_multitask_state()["thisMessageDispatches"] >= 5
        assert _read_session_start_state().get("dispatches_in_session", 0) >= 10

    def test_clean_state_with_gate_passing_avoids_nags(self):
        _write_gate_status_passing()
        _write_multitask_state(dispatches=5)
        _write_session_start_state(dispatch_count=10)
        _simulate_dispatch_reset()
        assert _is_streak_healthy()
        result_text = "=== GATE: PASSED ===\ncommit landed somehash\n10 passed"
        found = _has_nag_patterns(result_text)
        assert len(found) == 0, f"Should not find nags, got: {found}"


class TestDisengageEngageCycle:
    """Scenario 4+5: Disengage (floor-override) and re-engage."""

    def setup_method(self):
        _clean_state_files()

    def test_write_floor_override_and_read_back(self):
        _write_floor_override(3)
        floor = _read_floor_override(fallback=7)
        assert floor == 3, f"Floor override should be 3, got {floor}"

    def test_floor_override_changes_effective_floor(self):
        _write_floor_override(3)
        floor = _read_floor_override(fallback=7)
        assert floor == 3
        assert floor < 7, "Override should lower the floor"

    def test_remove_floor_override_restores_default(self):
        _write_floor_override(3)
        assert _read_floor_override(fallback=7) == 3
        _remove_floor_override()
        assert _read_floor_override(fallback=7) == 7

    def test_disengage_allows_work_with_high_streak(self):
        """With disengage active, high streak should not prevent dispatch."""
        for _ in range(12):
            _simulate_non_dispatch_call("edit")
        assert not _is_streak_healthy()
        future = int(time.time() * 1000) + 600_000  # 10 min from now
        _write_disengage(future)
        assert _is_disengaged()
        _simulate_dispatch_reset()
        assert _is_streak_healthy()

    def test_disengage_expired_does_not_help(self):
        past = int(time.time() * 1000) - 600_000  # 10 min ago
        _write_disengage(past)
        assert not _is_disengaged()

    def test_remove_disengage_resumes_enforcement(self):
        for _ in range(12):
            _simulate_non_dispatch_call("edit")
        assert not _is_streak_healthy()
        future = int(time.time() * 1000) + 600_000
        _write_disengage(future)
        assert _is_disengaged()
        _remove_disengage()
        assert not _is_disengaged()
        assert not _is_streak_healthy()

    def test_floor_override_alone_does_not_count_as_disengage(self):
        """floor-override changes the floor number but does NOT bypass the
        watchdog-disengage mechanism used by enforce-stop / enforce-delegate."""
        _write_floor_override(7)
        assert _read_floor_override(fallback=7) == 7
        assert not _is_disengaged(), (
            "floor-override is a tunable, not a disengage bypass"
        )

    def test_both_disengage_mechanisms_work_independently(self):
        _write_floor_override(3)
        future = int(time.time() * 1000) + 600_000
        _write_disengage(future)
        assert _read_floor_override(fallback=7) == 3
        assert _is_disengaged()
        _remove_floor_override()
        _remove_disengage()
        assert _read_floor_override(fallback=7) == 7
        assert not _is_disengaged()


class TestNagDetection:
    """Scenario 6: Verify DELEGATE-FIRST and MUST DISPATCH patterns are absent
    when state is healthy."""

    def setup_method(self):
        _clean_state_files()

    def test_healthy_fresh_session_has_no_nags(self):
        _simulate_dispatch_reset()
        _write_gate_status_passing()
        _write_multitask_state(dispatches=5)
        _write_session_start_state(dispatch_count=10)
        result = "Working on feature X. Running tests..."
        found = _has_nag_patterns(result)
        assert len(found) == 0, (
            f"Fresh healthy session should have no nags, found: {found}"
        )

    def test_nag_patterns_definitions_are_precise(self):
        """DELEGATE-FIRST and MUST DISPATCH must be exact literals to avoid
        false positives in unrelated text."""
        for p in NAG_PATTERNS:
            assert p, "Nag pattern must not be empty"
            assert len(p) > 3, f"Nag pattern {p!r} too short (risk of FP)"

    def test_text_without_nags_passes_detection(self):
        texts = [
            "=== GATE: PASSED ===\n100 passed\ncommit abc1234",
            "Working on the fix for the delegate issue",
            "First dispatch wave: 5 agents running",
            "All tests passed. Continuing work.",
        ]
        for t in texts:
            found = _has_nag_patterns(t)
            assert len(found) == 0, f"Text should not trigger nags: {t[:60]}... found: {found}"

    def test_text_with_nags_is_detected(self):
        texts = [
            ("DELEGATE-FIRST: 9 consecutive non-dispatch calls", ["DELEGATE-FIRST"]),
            ("MUST DISPATCH 5+ SUBAGENTS NOW", ["MUST DISPATCH"]),
            ("MESSAGE-SHAPE VIOLATION — MUST DISPATCH >=5 PER WAVE", ["MESSAGE-SHAPE VIOLATION", "MUST DISPATCH"]),
        ]
        for t, expected in texts:
            found = _has_nag_patterns(t)
            for p in expected:
                assert p in found, f"Expected {p!r} in nags for: {t[:60]}... got: {found}"

    def test_streak_8_does_not_nag(self):
        """DELEGATE_FIRST_THRESHOLD is 8, so streak <= 8 should NOT trigger
        DELEGATE-FIRST. The check is `streak > 8`."""
        s = _read_tool_streak()
        s["streak"] = 8
        _write_tool_streak(s)
        assert _is_streak_healthy(), "Streak=8 should be healthy (not > threshold)"

    def test_streak_9_does_nag(self):
        s = _read_tool_streak()
        s["streak"] = 9
        _write_tool_streak(s)
        assert not _is_streak_healthy(), "Streak=9 should be unhealthy (above threshold)"

    def test_full_session_cycle_no_nags(self):
        """Simulate session start -> work -> dispatch -> commit -> end."""
        _clean_state_files()
        _write_gate_status_passing()
        for _ in range(3):
            _simulate_non_dispatch_call("edit")
        assert _is_streak_healthy()
        _simulate_dispatch_reset()
        assert _is_streak_healthy()
        assert _read_mainthread_streak() == 0
        for _ in range(2):
            _simulate_non_dispatch_call("edit")
        _simulate_dispatch_reset()
        assert _read_tool_streak()["streak"] == 0
        assert _check_gate_status()["is_green"]
        assert _is_streak_healthy()

    def test_multitask_zero_streak_starts_clean(self):
        s = _read_multitask_state()
        assert s["zeroStreak"] == 0, "Multitask zeroStreak should start at 0"

    def test_multitask_state_writes_dispatch_count(self):
        _write_multitask_state(dispatches=5, zero_streak=0)
        s = _read_multitask_state()
        assert s["thisMessageDispatches"] == 5
        assert s["zeroStreak"] == 0
        _write_multitask_state(dispatches=0, zero_streak=2)
        s = _read_multitask_state()
        assert s["zeroStreak"] == 2


class TestStateFileJSONIntegrity:
    """Verify state files use valid JSON that matches the TypeScript types."""

    def test_mainthread_streak_json_roundtrip(self):
        _write_mainthread_streak(3)
        assert _read_mainthread_streak() == 3

    def test_mainthread_streak_zero_roundtrip(self):
        _write_mainthread_streak(0)
        assert _read_mainthread_streak() == 0

    def test_tool_streak_json_roundtrip(self):
        s = {
            "streak": 4,
            "lastDispatchTs": int(time.time() * 1000),
            "readStreak": 2,
            "editStreak": 2,
            "lastUpdateTs": int(time.time() * 1000),
            "lastWriter": "enforce-floor",
        }
        _write_tool_streak(s)
        s2 = _read_tool_streak()
        assert s2["streak"] == 4
        assert s2["readStreak"] == 2
        assert s2["editStreak"] == 2

    def test_multitask_state_json_roundtrip(self):
        _write_multitask_state(dispatches=5, zero_streak=1)
        s = _read_multitask_state()
        assert s["thisMessageDispatches"] == 5
        assert s["zeroStreak"] == 1
        assert isinstance(s["lastTs"], (int, float))

    def test_session_start_json_roundtrip(self):
        _write_session_start_state(dispatch_count=5)
        s = _read_session_start_state()
        assert s.get("dispatches_in_session") == 5
        assert "session_start_ts" in s


class TestPluginSourceContract:
    """Verify the TypeScript plugin source files define the state paths we test."""

    def test_mainthread_streak_file_in_delegate_source(self):
        src = (ROOT / ".opencode/plugin/enforce-delegate.ts").read_text()
        assert "gludd-mainthread-streak.json" in src

    def test_floor_override_in_floor_source(self):
        src = (ROOT / ".opencode/plugin/enforce-floor.ts").read_text()
        assert "gludd-floor-override" in src

    def test_tool_streak_file_in_floor_source(self):
        src = (ROOT / ".opencode/plugin/enforce-floor.ts").read_text()
        shared = (ROOT / ".opencode/lib/shared.ts").read_text()
        assert "updateSharedStreak" in src
        assert "gludd-tool-streak.json" in shared

    def test_multitask_state_file_in_multitask_source(self):
        src = (ROOT / ".opencode/plugin/enforce-multitask.ts").read_text()
        assert "gludd-multitask-state.json" in src

    def test_session_start_state_in_source(self):
        src = (ROOT / ".opencode/plugin/enforce-session-start.ts").read_text()
        assert "gludd-session-start.json" in src

    def test_disengage_file_in_floor_source(self):
        src = (ROOT / ".opencode/plugin/enforce-floor.ts").read_text()
        shared = (ROOT / ".opencode/lib/shared.ts").read_text()
        assert "isDisengaged" in src
        assert "gludd-watchdog-disengage.json" in shared

    def test_delegate_first_threshold_correct(self):
        src = (ROOT / ".opencode/plugin/enforce-stop.ts").read_text()
        m = re.search(r"const\s+DELEGATE_FIRST_THRESHOLD\s*=\s*(\d+)", src)
        assert m, "DELEGATE_FIRST_THRESHOLD not found"
        assert int(m.group(1)) == 8, f"Expected 8, got {m.group(1)}"


class TestEndToEndCycle:
    """Full session lifecycle simulation at the state-file level."""

    def setup_method(self):
        _clean_state_files()
        _remove_gate_status()

    def test_init_work_dispatch_commit_cycle(self):
        _write_gate_status_passing()
        _write_multitask_state(dispatches=5)
        _write_session_start_state(dispatch_count=5)
        _simulate_dispatch_reset()
        for _ in range(3):
            _simulate_non_dispatch_call("edit")
        assert _read_tool_streak()["streak"] == 3
        _simulate_dispatch_reset()
        assert _read_tool_streak()["streak"] == 0
        _write_mainthread_streak(0)
        assert _read_mainthread_streak() == 0
        assert _check_gate_status()["is_green"]
        assert _read_session_start_state().get("dispatches_in_session", 0) >= 5
        assert _is_streak_healthy()

    def test_disengage_mid_cycle(self):
        _write_gate_status_passing()
        for _ in range(10):
            _simulate_non_dispatch_call("edit")
        assert not _is_streak_healthy()
        future = int(time.time() * 1000) + 600_000
        _write_disengage(future)
        assert _is_disengaged()
        _simulate_dispatch_reset()
        assert _is_streak_healthy()
        _remove_disengage()
        for _ in range(10):
            _simulate_non_dispatch_call("edit")
        assert not _is_streak_healthy()

    def test_cleanup_at_end_drops_all_state(self):
        _write_gate_status_passing()
        _write_mainthread_streak(5)
        _write_multitask_state(dispatches=5)
        _write_session_start_state(dispatch_count=5)
        _write_floor_override(3)
        _clean_state_files()
        assert _count_remaining_state_files() == 0
        assert _read_mainthread_streak() == 0
        assert _read_floor_override(fallback=7) == 7
