"""Tests for scripts/agent_watchdog.py — pure-function classify_tail core."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the scripts/ directory importable without installing.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from agent_watchdog import State, classify_tail, has_overload_marker  # noqa: I001


# ── has_overload_marker ───────────────────────────────────────────────────────


def test_has_overload_marker_detects_each_marker():
    cases = [
        "overloaded",
        "rate limit",
        "429",
        "529",
        "temporarily limiting requests",
        "quota exceeded",
    ]
    for marker in cases:
        assert has_overload_marker(f"Error: {marker} please retry"), f"missed marker {marker!r}"


def test_has_overload_marker_case_insensitive():
    assert has_overload_marker("OVERLOADED system")
    assert has_overload_marker("Rate Limit exceeded")


def test_has_overload_marker_false_for_normal_text():
    assert not has_overload_marker("Working on the task now...")
    assert not has_overload_marker("Result: all tests passed")


# ── classify_tail — ACTIVE branch ─────────────────────────────────────────────


def test_classify_tail_active_within_window():
    state, reason = classify_tail("some output", age_seconds=30, window_seconds=90)
    assert state == State.ACTIVE
    assert "30s" in reason


def test_classify_tail_overload_marker_skips_kill():
    """Overload marker in stale output → ACTIVE (backoff, no kill)."""
    for marker_text in [
        "overloaded",
        "rate limit",
        "429",
        "529",
        "temporarily limiting requests",
        "quota exceeded",
    ]:
        tail = f"Working on task...\nError: {marker_text} please retry"
        state, reason = classify_tail(tail, age_seconds=300, window_seconds=90)
        assert state == State.ACTIVE, f"Expected ACTIVE for marker {marker_text!r}, got {state}"
        assert "overload" in reason.lower() or "backoff" in reason.lower(), (
            f"Unexpected reason: {reason}"
        )


# ── classify_tail — DONE branch ───────────────────────────────────────────────


def test_classify_tail_done_via_marker():
    tail = "Running tests...\nAll tests passed.\nResult: success"
    state, reason = classify_tail(tail, age_seconds=300, window_seconds=90)
    assert state == State.DONE
    assert "result:" in reason


# ── classify_tail — LIKELY_STALLED_INCOMPLETE branch ─────────────────────────


def test_classify_tail_normal_stall_kills():
    """Normal stale output without overload markers → LIKELY_STALLED_INCOMPLETE (kill)."""
    tail = "I'll continue working on this:"
    state, _reason = classify_tail(tail, age_seconds=300, window_seconds=90)
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_classify_tail_empty_output_stalled():
    state, reason = classify_tail("", age_seconds=300, window_seconds=90)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "empty" in reason


def test_classify_tail_continuation_prefix_stalled():
    tail = "Let me proceed with the next steps"
    state, _reason = classify_tail(tail, age_seconds=200, window_seconds=90)
    assert state == State.LIKELY_STALLED_INCOMPLETE


def test_classify_tail_ends_with_colon_stalled():
    tail = "Continuing with the remaining files:"
    state, reason = classify_tail(tail, age_seconds=200, window_seconds=90)
    assert state == State.LIKELY_STALLED_INCOMPLETE
    assert "':'" in reason
