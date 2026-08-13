"""Behavior pin for opt-in under-floor escalation in enforce-multitask.ts.

Tracks consecutive waves below the operator-configured minimum. After three
such waves, the text boundary injects an escalation warning. Without an
explicit minimum, ordinary inline work remains allowed.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _src() -> str:
    return PLUGIN_PATH.read_text()


class TestUnderFloorCounterExists:
    def test_under_floor_count_in_interface(self):
        src = _src()
        assert "underFloorCount" in src, "underFloorCount missing from plugin source"

    def test_under_floor_count_in_multitask_state(self):
        src = _src()
        m = re.search(r"interface\s+MultitaskState\s*\{([^}]+)\}", src, re.DOTALL)
        assert m, "MultitaskState interface not found"
        body = m.group(1)
        assert "underFloorCount" in body, (
            "underFloorCount must be a field on MultitaskState interface"
        )
        assert "number" in body.split("underFloorCount")[1].split("\n")[0], (
            "underFloorCount must be typed as number"
        )

    def test_under_floor_count_in_fresh_state(self):
        src = _src()
        m = re.search(
            r"function\s+freshState\(\).*?\{([^}]+)\}",
            src,
            re.DOTALL,
        )
        assert m, "freshState() not found"
        body = m.group(1)
        assert "underFloorCount:" in body or "underFloorCount :" in body, (
            "underFloorCount must be initialized in freshState()"
        )
        assert re.search(r"underFloorCount\s*:\s*0", body), (
            "underFloorCount must default to 0"
        )


class TestUnderFloorCounterIncrement:
    def test_increment_in_handle_message_boundary(self):
        src = _src()
        m = re.search(
            r"function\s+handleMessageBoundary\(.*?\).*?\{(.*?)"
            r"\n\}\nlet\s+_state",
            src,
            re.DOTALL,
        )
        assert m, "handleMessageBoundary() not found"
        body = m.group(1)
        assert "underFloorCount" in body, (
            "underFloorCount must be referenced in handleMessageBoundary"
        )

    def test_increment_condition_uses_configured_minimum(self):
        src = _src()
        assert re.search(
            r"REQUIRED_DISPATCHES\s*>\s*0\s*&&\s*"
            r"s\.prevMessageDispatches\s*<\s*REQUIRED_DISPATCHES",
            src,
        ), (
            "underFloorCount must increment only below an explicit minimum"
        )

    def test_reset_on_full_wave(self):
        src = _src()
        # The else branch (full wave) must reset counter to 0
        assert re.search(
            r"underFloorCount\s*=\s*0",
            src,
        ), "underFloorCount must be reset to 0 when a full wave is dispatched"

    def test_increment_not_reset_to_zero_on_sub_floor(self):
        """The increment path must increment, not set to 0."""
        src = _src()
        assert "underFloorCount++" in src, (
            "underFloorCount must increment via ++ on sub-floor waves"
        )


class TestUnderFloorEscalationWarning:
    def test_escalation_check_exists_in_text_complete(self):
        src = _src()
        assert re.search(
            r"underFloorCount\s*>=\s*3",
            src,
        ), "escalation threshold check (underFloorCount >= 3) missing"

    def test_escalation_warning_text_exists(self):
        src = _src()
        assert "DISPATCH FLOOR VIOLATION" in src, (
            "escalation warning text 'DISPATCH FLOOR VIOLATION' missing"
        )
        assert "consecutive waves with fewer than" in src, (
            "escalation text must mention consecutive waves"
        )

    def test_escalation_in_thin_wave_blocked_path(self):
        """The session-aware thin-wave response includes repeated-breach escalation."""
        src = _src()
        start = src.index("const _tef = REQUIRED_DISPATCHES > 0")
        end = src.index("const hasWork", start)
        block_region = src[start:end]
        assert "THIN WAVE BLOCKED" in block_region
        assert "underFloorCount" in block_region, (
            "Escalation must be checked in THIN WAVE BLOCKED path"
        )
        assert "ESCALATION" in block_region, (
            "Escalation warning text must appear in THIN WAVE BLOCKED response"
        )

    def test_escalation_in_normal_return_path(self):
        """The normal text.complete return must inject escalation warning when counter >= 3."""
        src = _src()
        assert "DISPATCH FLOOR VIOLATION" in src, (
            "escalation warning must be injectable into normal output"
        )


class TestInitBlockResetsCounter:
    def test_init_block_resets_under_floor_count(self):
        src = _src()
        # The _state init block must reset underFloorCount to 0
        m = re.search(
            r"let\s+_state.*?=\s*\(\(\).*?writeState\(s\).*?return\s+s",
            src,
            re.DOTALL,
        )
        assert m, "_state init block not found"
        init_block = m.group(0)
        assert "underFloorCount" in init_block, (
            "underFloorCount must be reset in _state init block"
        )
        assert re.search(r"underFloorCount\s*=\s*0", init_block, re.MULTILINE), (
            "underFloorCount must be set to 0 in _state init block"
        )


class TestUnderFloorCountInStateFile:
    def test_state_file_path_unchanged(self):
        src = _src()
        assert "MULTITASK_STATE_FILE" in src, (
            "MULTITASK_STATE_FILE export must still exist"
        )
        # The state file persists the full MultitaskState including underFloorCount
