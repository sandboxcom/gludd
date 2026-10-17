"""Behavioral tests for scripts/watchdog_components/enforcement.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import scripts.watchdog_components.enforcement as enforcement


def test_check_under_floor_dispatch_emits_full_wave_directive_when_pipeline_dry() -> None:
    """When pending work exists, no dispatches were sent, and the pipeline is dry, a full-wave directive is written."""
    runtime = MagicMock()
    runtime._read_multitask_state.return_value = {
        "thisMessageDispatches": 0,
        "zeroStreak": 1,
        "estimatedInFlight": 0,
    }
    runtime._as_int.side_effect = lambda v: int(v) if v is not None else 0
    runtime._pending_work_exists.return_value = True
    runtime._now.return_value = "2026-01-01T00:00:00Z"

    enforcement._check_under_floor_dispatch(runtime)

    runtime._write_prioritized_plain_directive.assert_called_once()
    directive = runtime._write_prioritized_plain_directive.call_args[0][0]
    assert "UNDER-FLOOR DETECTED" in directive
    assert "DISPATCH A FULL WAVE OF 10 SUBAGENTS NOW" in directive


def test_check_under_floor_dispatch_noop_when_no_pending_work() -> None:
    """Without pending work the function should not emit any directive."""
    runtime = MagicMock()
    runtime._read_multitask_state.return_value = {
        "thisMessageDispatches": 0,
        "zeroStreak": 1,
        "estimatedInFlight": 0,
    }
    runtime._as_int.side_effect = lambda v: int(v) if v is not None else 0
    runtime._pending_work_exists.return_value = False

    enforcement._check_under_floor_dispatch(runtime)

    runtime._write_prioritized_plain_directive.assert_not_called()
