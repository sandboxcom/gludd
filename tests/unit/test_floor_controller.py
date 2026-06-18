"""Unit tests for scripts/floor_controller.py.

Covers the five scenarios required by the task spec:
  1. below floor          -> dispatch toward target
  2. at / above ceiling   -> dispatch 0
  3. STALLED inflight     -> appears in repoke_ids
  4. DONE (resting) agent -> appears in kill_ids, NOT repoke_ids
  5. in band              -> dispatch 0, reason says "in band"

Plus structural / composition checks.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Dynamic import (same pattern as test_floor_planner.py)
# ---------------------------------------------------------------------------

_MODPATH = Path(__file__).resolve().parents[2] / "scripts" / "floor_controller.py"
_spec = importlib.util.spec_from_file_location("floor_controller", _MODPATH)
assert _spec and _spec.loader
floor_controller = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(floor_controller)

decide = floor_controller.decide


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_agent(agent_id: str, age: float = 10.0) -> dict:
    """An agent that was modified recently — will classify as ACTIVE."""
    return {
        "id": agent_id,
        "age_seconds": age,
        # 'result:' marker in tail would make it DONE; plain text with recent
        # age keeps it ACTIVE regardless of tail content.
        "tail": "Running tests...",
    }


def _stalled_agent(agent_id: str, age: float = 200.0) -> dict:
    """An agent whose tail ends with a continuation phrase — LIKELY_STALLED_INCOMPLETE."""
    return {
        "id": agent_id,
        "age_seconds": age,
        "tail": "Let me continue working on the remaining files:",
    }


def _done_agent(agent_id: str, age: float = 200.0) -> dict:
    """An agent whose tail contains a completion marker — DONE."""
    return {
        "id": agent_id,
        "age_seconds": age,
        "tail": "result: all tasks completed successfully",
    }


# ---------------------------------------------------------------------------
# 1. Below floor -> dispatch toward target
# ---------------------------------------------------------------------------

class TestBelowFloor:
    def test_below_floor_dispatches_nonzero(self) -> None:
        """With live=2 and floor=6 we must dispatch agents."""
        result = decide(live_agents=2, inflight=[], floor=6, target=10, ceiling=12)
        assert result["dispatch_n"] > 0
        assert "below floor" in result["reason"]

    def test_below_floor_dispatch_fills_to_target(self) -> None:
        """With no inflight, dispatch should reach the aim (≥ target when target ≤ ceiling)."""
        result = decide(live_agents=0, inflight=[], floor=6, target=10, ceiling=12)
        # aim = max(floor+buffer, target) = max(8, 10) = 10; live=0 -> dispatch 10
        assert result["dispatch_n"] == 10

    def test_dispatch_n_type(self) -> None:
        result = decide(live_agents=1, inflight=[], floor=6, target=10, ceiling=12)
        assert isinstance(result["dispatch_n"], int)


# ---------------------------------------------------------------------------
# 2. At / above ceiling -> dispatch 0
# ---------------------------------------------------------------------------

class TestAtOrAboveCeiling:
    def test_at_ceiling_dispatch_zero(self) -> None:
        result = decide(live_agents=12, inflight=[], floor=6, target=10, ceiling=12)
        assert result["dispatch_n"] == 0
        assert "ceiling" in result["reason"]

    def test_above_ceiling_dispatch_zero(self) -> None:
        result = decide(live_agents=15, inflight=[], floor=6, target=10, ceiling=12)
        assert result["dispatch_n"] == 0
        assert "ceiling" in result["reason"]

    def test_ceiling_does_not_override_repoke(self) -> None:
        """Even when at ceiling, stalled agents are still repoke-flagged."""
        stalled = _stalled_agent("s1", age=200.0)
        result = decide(
            live_agents=12,
            inflight=[stalled],
            floor=6,
            target=10,
            ceiling=12,
            watchdog_window=90,
        )
        assert result["dispatch_n"] == 0
        assert "s1" in result["repoke_ids"]


# ---------------------------------------------------------------------------
# 3. Stalled inflight agent -> appears in repoke_ids
# ---------------------------------------------------------------------------

class TestStalledAgentRepoke:
    def test_stalled_agent_in_repoke_ids(self) -> None:
        stalled = _stalled_agent("stalled-123", age=200.0)
        result = decide(
            live_agents=5,
            inflight=[stalled],
            floor=6,
            target=10,
            ceiling=12,
            watchdog_window=90,
        )
        assert "stalled-123" in result["repoke_ids"]
        assert "stalled-123" not in result["kill_ids"]

    def test_multiple_stalled_agents_all_repoked(self) -> None:
        inflight = [
            _stalled_agent("s1", age=200.0),
            _stalled_agent("s2", age=500.0),
        ]
        result = decide(live_agents=4, inflight=inflight, floor=6, target=10, ceiling=12, watchdog_window=90)
        assert "s1" in result["repoke_ids"]
        assert "s2" in result["repoke_ids"]

    def test_active_agent_not_repoked(self) -> None:
        """An ACTIVE (recently modified) agent must not end up in repoke_ids."""
        active = _active_agent("active-1", age=10.0)
        result = decide(
            live_agents=8,
            inflight=[active],
            floor=6,
            target=10,
            ceiling=12,
            watchdog_window=90,
        )
        assert "active-1" not in result["repoke_ids"]
        assert "active-1" not in result["kill_ids"]

    def test_stalled_tail_ends_with_colon(self) -> None:
        """Last line ends with ':' is a stall signal."""
        agent = {
            "id": "colon-stall",
            "age_seconds": 200.0,
            "tail": "Continuing with the remaining files:",
        }
        result = decide(live_agents=5, inflight=[agent], floor=6, target=10, ceiling=12, watchdog_window=90)
        assert "colon-stall" in result["repoke_ids"]


# ---------------------------------------------------------------------------
# 4. DONE (resting) agent -> kill_ids, NOT repoke_ids
# ---------------------------------------------------------------------------

class TestDoneAgentKill:
    def test_done_agent_in_kill_ids(self) -> None:
        done = _done_agent("done-abc", age=200.0)
        result = decide(
            live_agents=8,
            inflight=[done],
            floor=6,
            target=10,
            ceiling=12,
            watchdog_window=90,
        )
        assert "done-abc" in result["kill_ids"]
        assert "done-abc" not in result["repoke_ids"]

    def test_done_agent_not_repoked(self) -> None:
        done = _done_agent("done-xyz", age=300.0)
        result = decide(live_agents=8, inflight=[done], floor=6, target=10, ceiling=12, watchdog_window=90)
        assert "done-xyz" not in result["repoke_ids"]

    def test_multiple_done_agents_all_killed(self) -> None:
        inflight = [
            _done_agent("d1", age=200.0),
            _done_agent("d2", age=400.0),
        ]
        result = decide(live_agents=8, inflight=inflight, floor=6, target=10, ceiling=12, watchdog_window=90)
        assert "d1" in result["kill_ids"]
        assert "d2" in result["kill_ids"]

    def test_done_agent_with_finished_marker(self) -> None:
        agent = {
            "id": "fin-agent",
            "age_seconds": 200.0,
            "tail": "All tests passed. finished.",
        }
        result = decide(live_agents=8, inflight=[agent], floor=6, target=10, ceiling=12, watchdog_window=90)
        assert "fin-agent" in result["kill_ids"]


# ---------------------------------------------------------------------------
# 5. In band -> dispatch 0 + reason says "in band"
# ---------------------------------------------------------------------------

class TestInBand:
    def test_in_band_dispatch_zero(self) -> None:
        """live == target, no drain -> in band, dispatch 0."""
        result = decide(live_agents=10, inflight=[], floor=6, target=10, ceiling=12)
        assert result["dispatch_n"] == 0
        assert "in band" in result["reason"]

    def test_above_floor_below_ceiling_dispatch_zero(self) -> None:
        result = decide(live_agents=9, inflight=[], floor=6, target=9, ceiling=12)
        assert result["dispatch_n"] == 0

    def test_in_band_reason_string(self) -> None:
        result = decide(live_agents=10, inflight=[], floor=6, target=10, ceiling=12)
        assert isinstance(result["reason"], str)
        assert len(result["reason"]) > 0


# ---------------------------------------------------------------------------
# 6. Mixed inflight (ACTIVE + STALLED + DONE)
# ---------------------------------------------------------------------------

class TestMixedInflight:
    def test_mixed_inflight_routes_correctly(self) -> None:
        inflight = [
            _active_agent("active-1", age=5.0),
            _stalled_agent("stalled-1", age=300.0),
            _done_agent("done-1", age=300.0),
        ]
        result = decide(
            live_agents=4,
            inflight=inflight,
            floor=6,
            target=10,
            ceiling=12,
            watchdog_window=90,
        )
        assert "active-1" not in result["repoke_ids"]
        assert "active-1" not in result["kill_ids"]
        assert "stalled-1" in result["repoke_ids"]
        assert "done-1" in result["kill_ids"]
        # dispatch > 0 because live_agents=4 < floor=6
        assert result["dispatch_n"] > 0


# ---------------------------------------------------------------------------
# 7. Result shape
# ---------------------------------------------------------------------------

class TestResultShape:
    def test_result_has_required_keys(self) -> None:
        result = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12)
        assert set(result) >= {"dispatch_n", "repoke_ids", "kill_ids", "reason", "planner"}

    def test_repoke_and_kill_are_lists(self) -> None:
        result = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12)
        assert isinstance(result["repoke_ids"], list)
        assert isinstance(result["kill_ids"], list)

    def test_dispatch_n_never_negative(self) -> None:
        result = decide(live_agents=100, inflight=[], floor=6, target=10, ceiling=12)
        assert result["dispatch_n"] >= 0

    def test_planner_subkey_present(self) -> None:
        result = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12)
        planner = result["planner"]
        assert "dispatch_now" in planner
        assert "reason" in planner

    def test_json_serializable(self) -> None:
        inflight = [_stalled_agent("s", age=200), _done_agent("d", age=200)]
        result = decide(live_agents=5, inflight=inflight, floor=6, target=10, ceiling=12, watchdog_window=90)
        # Must not raise
        raw = json.dumps(result)
        decoded = json.loads(raw)
        assert decoded["dispatch_n"] == result["dispatch_n"]


# ---------------------------------------------------------------------------
# 9. Gate-safe floor rule (gate_running=True)
# ---------------------------------------------------------------------------
#
# THE BUG BEING GUARDED: orchestrator sat at 5 live agents "to protect the
# gate" instead of dispatching read-only agents to reach the floor of 6.
# A running gate must NOT suppress read-only floor dispatch.

class TestGateSafeFloor:
    def test_gate_running_below_floor_still_dispatches(self) -> None:
        """live=5, floor=6, gate_running=True -> dispatch_n >= 1 (the core regression)."""
        result = decide(
            live_agents=5,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
        )
        assert result["dispatch_n"] >= 1, (
            "A running gate must NOT suppress read-only floor dispatch. "
            f"Got dispatch_n={result['dispatch_n']}"
        )

    def test_gate_running_below_floor_marks_read_only(self) -> None:
        """live=5, floor=6, gate_running=True -> plan is marked read_only_only=True."""
        result = decide(
            live_agents=5,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
        )
        assert result["read_only_only"] is True
        assert "gate" in result["reason"].lower()

    def test_gate_running_false_unchanged_behavior(self) -> None:
        """gate_running=False (default) -> same result as not passing the arg."""
        result_default = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12)
        result_explicit = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12, gate_running=False)
        assert result_default["dispatch_n"] == result_explicit["dispatch_n"]
        assert result_default["reason"] == result_explicit["reason"]
        assert result_explicit["read_only_only"] is False

    def test_gate_running_at_ceiling_still_zero(self) -> None:
        """Ceiling always wins: gate_running=True with live>=ceiling -> dispatch_n=0."""
        result = decide(
            live_agents=12,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
        )
        assert result["dispatch_n"] == 0
        assert "ceiling" in result["reason"]

    def test_gate_running_above_ceiling_still_zero(self) -> None:
        """Ceiling wins even when well above it: gate_running=True, live=15 -> dispatch_n=0."""
        result = decide(
            live_agents=15,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
        )
        assert result["dispatch_n"] == 0

    def test_gate_running_caps_read_only_only_flag(self) -> None:
        """gate_running=True with dispatch recommended -> read_only_only is True (writer suppressed)."""
        result = decide(
            live_agents=3,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
            writer_cap_during_gate=0,
        )
        assert result["read_only_only"] is True
        # dispatch_n still > 0 (floor not yet reached)
        assert result["dispatch_n"] > 0

    def test_gate_running_echoed_in_result(self) -> None:
        """The returned dict echoes gate_running for logging purposes."""
        result_on = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12, gate_running=True)
        result_off = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12, gate_running=False)
        assert result_on["gate_running"] is True
        assert result_off["gate_running"] is False

    def test_gate_running_in_band_read_only_only_false(self) -> None:
        """When live is in band (no dispatch needed), read_only_only stays False even with gate."""
        result = decide(
            live_agents=8,
            inflight=[],
            floor=6,
            target=8,
            ceiling=12,
            gate_running=True,
        )
        # No dispatch needed -> read_only_only stays False
        assert result["dispatch_n"] == 0
        assert result["read_only_only"] is False


# ---------------------------------------------------------------------------
# 8. CLI (main) wrapper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 10. Predictive floor control (expected_completions_soon + buffer)
# ---------------------------------------------------------------------------
#
# THE PROBLEM BEING GUARDED: sawtooth oscillation — the orchestrator falls below
# floor because agents complete in bursts.  Predictive mode pre-empts the dip:
# when ``expected_completions_soon > 0`` completions are imminent, dispatch NOW
# to maintain ``floor + buffer`` headroom, not just to recover after the dip.

class TestPredictiveFloor:
    # --- test 1: backward compat — expected_completions_soon=0 means no change ---
    def test_backward_compat_zero_expected_completions(self) -> None:
        """expected_completions_soon=0 (default) must give same result as the gate-safe tests."""
        result_implicit = decide(live_agents=5, inflight=[], floor=6, target=10, ceiling=12)
        result_explicit = decide(
            live_agents=5, inflight=[], floor=6, target=10, ceiling=12,
            expected_completions_soon=0,
        )
        assert result_implicit["dispatch_n"] == result_explicit["dispatch_n"]
        assert result_implicit["reason"] == result_explicit["reason"]

    # --- test 2: predictive pre-emption when anticipated drops below floor ---
    def test_predictive_preemption_below_floor(self) -> None:
        """live=7, floor=6, expected_completions_soon=2 → anticipated=5 < floor → dispatch >= 1."""
        result = decide(
            live_agents=7,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=2,
        )
        assert result["dispatch_n"] >= 1, (
            "Predictive: anticipated live 5 < floor 6, must dispatch. "
            f"Got dispatch_n={result['dispatch_n']}"
        )

    # --- test 3: predictive with explicit buffer ---
    def test_predictive_with_buffer(self) -> None:
        """live=8, floor=6, expected_completions_soon=3, buffer=2 → anticipated=5 < floor → dispatch."""
        result = decide(
            live_agents=8,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=3,
            buffer=2,
        )
        assert result["dispatch_n"] >= 1, (
            "Predictive: anticipated live 5 < floor 6, must dispatch with buffer=2. "
            f"Got dispatch_n={result['dispatch_n']}"
        )

    # --- test 4: dispatch still happens when anticipated < aim (floor+buffer) ---
    def test_predictive_below_aim_dispatches(self) -> None:
        """live=9, floor=6, expected_completions_soon=2, buffer=2 → anticipated=7 < aim=8 → dispatch >= 1."""
        result = decide(
            live_agents=9,
            inflight=[],
            floor=6,
            target=6,   # keep target=floor so aim=floor+buffer=8 drives the test
            ceiling=12,
            expected_completions_soon=2,
            buffer=2,
        )
        # anticipated=7 < aim=max(6+2, 6)=8 → planner dispatches; capped to ceiling-live=3
        assert result["dispatch_n"] >= 1, (
            "Predictive: anticipated 7 < aim 8, must dispatch. "
            f"Got dispatch_n={result['dispatch_n']}"
        )

    # --- test 5: ceiling cap applies to predictive dispatch ---
    def test_predictive_ceiling_cap(self) -> None:
        """live=11, ceiling=12, expected_completions_soon=5 → dispatch at most 1 (headroom=1)."""
        result = decide(
            live_agents=11,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=5,
            buffer=2,
        )
        # anticipated=6=floor; aim=max(8,10)=10; effective=6 < 10 → need=4
        # but ceiling-live=1 → dispatch capped to 1
        assert result["dispatch_n"] <= 1, (
            f"Predictive ceiling cap: dispatch must be <= 1. Got {result['dispatch_n']}"
        )
        assert result["dispatch_n"] >= 0

    # --- test 6: buffer fills to aim when expected_completions_soon=0 ---
    def test_buffer_fills_to_aim_no_predictive(self) -> None:
        """live=6=floor, expected_completions_soon=0, buffer=2 → aim=floor+buffer=8, dispatch 2."""
        result = decide(
            live_agents=6,
            inflight=[],
            floor=6,
            target=6,   # target=floor so aim=max(8,6)=8 driven by buffer
            ceiling=12,
            expected_completions_soon=0,
            buffer=2,
        )
        # aim=max(6+2, 6)=8; live=6 < aim=8 → dispatch=2
        assert result["dispatch_n"] == 2, (
            f"Buffer fill: live=6, aim=8, must dispatch 2. Got {result['dispatch_n']}"
        )

    # --- test 7: no over-dispatch when in band at target ---
    def test_no_overdispatch_in_band(self) -> None:
        """live=10=target, expected_completions_soon=0 → in band, dispatch 0."""
        result = decide(
            live_agents=10,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=0,
        )
        assert result["dispatch_n"] == 0
        assert "in band" in result["reason"]

    # --- test 8: expected_completions_soon > live → clamp gracefully ---
    def test_expected_completions_more_than_live(self) -> None:
        """expected_completions_soon > live → clamp anticipated to 0, dispatch toward floor."""
        result = decide(
            live_agents=3,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=10,  # more than live=3
            buffer=2,
        )
        # anticipated=max(0, 3-10)=0 < floor=6 → dispatch toward aim
        assert result["dispatch_n"] >= 1
        assert result["dispatch_n"] <= 12  # never exceeds ceiling

    # --- test 9: gate_running=True + expected_completions_soon still marks read_only_only ---
    def test_predictive_with_gate_running_marks_read_only(self) -> None:
        """gate_running=True + expected_completions_soon=3 → read_only_only=True when dispatch > 0."""
        result = decide(
            live_agents=7,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            gate_running=True,
            expected_completions_soon=3,
        )
        # anticipated=4 < floor=6 → dispatch > 0 → gate forces read_only_only=True
        assert result["dispatch_n"] > 0
        assert result["read_only_only"] is True

    # --- test 10: predictive result still has all required keys ---
    def test_predictive_result_shape(self) -> None:
        """Predictive path must return the full result dict with new keys echoed."""
        result = decide(
            live_agents=8,
            inflight=[],
            floor=6,
            target=10,
            ceiling=12,
            expected_completions_soon=3,
            buffer=2,
        )
        required_keys = {
            "dispatch_n", "repoke_ids", "kill_ids", "reason", "planner",
            "read_only_only", "gate_running",
            "expected_completions_soon", "buffer",
        }
        missing = required_keys - set(result)
        assert not missing, f"Missing keys in predictive result: {missing}"
        assert result["expected_completions_soon"] == 3
        assert result["buffer"] == 2


class TestCLI:
    def test_main_valid_stdin(self, capsys, monkeypatch) -> None:
        state = {
            "live": 2,
            "inflight": [],
            "floor": 6,
            "target": 10,
            "ceiling": 12,
        }
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(state)))
        rc = floor_controller.main([])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # live=2, floor=6, target=10, buffer=2 -> aim=max(8,10)=10; dispatch 10-2=8
        assert out["dispatch_n"] == 8
        assert out["dispatch_n"] > 0

    def test_main_invalid_json_returns_nonzero(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not-json{{{"))
        rc = floor_controller.main([])
        assert rc != 0

    def test_main_file_arg(self, tmp_path, capsys) -> None:
        state_file = tmp_path / "state.json"
        state = {
            "live": 12,
            "inflight": [],
            "floor": 6,
            "target": 10,
            "ceiling": 12,
        }
        state_file.write_text(json.dumps(state))
        rc = floor_controller.main([str(state_file)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["dispatch_n"] == 0  # at ceiling
        assert "ceiling" in out["reason"]
