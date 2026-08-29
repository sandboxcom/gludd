"""Behavior pins for ten-wide dispatch waves and their preflight audit."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOOR_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
WAVE_REFERENCE = ROOT / "docs" / "WAVE_ENFORCEMENT.md"


def _source() -> str:
    return FLOOR_PLUGIN.read_text(encoding="utf-8")


def test_wave_width_defaults_to_ten_and_is_env_tunable() -> None:
    source = _source()
    assert 'GLUDD_DISPATCH_WAVE_WIDTH' in source
    assert '"10"' in source
    assert "WAVE_WIDTH" in source


def test_previous_undersized_wave_is_blocked_before_inline_work() -> None:
    source = _source()
    assert "WAVE WIDTH VIOLATION" in source
    assert "_prevMessageDispatchCount < eff.waveWidth" in source
    assert 'permissionDecision: "deny" as const' in source


def test_dispatch_preflight_is_recorded_before_first_wave_member() -> None:
    source = _source()
    assert "recordDispatchPreflight" in source
    assert "gludd-dispatch-preflight.json" in source
    assert "required_width" in source


def test_reference_documentation_defines_pre_dispatch_audit() -> None:
    reference = WAVE_REFERENCE.read_text(encoding="utf-8")
    assert "Pre-dispatch audit" in reference
    assert "10" in reference
    assert "deduplicate" in reference.lower()
    assert "enhancement" in reference.lower()
