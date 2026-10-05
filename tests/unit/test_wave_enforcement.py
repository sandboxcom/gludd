"""Behavior pins for bounded opt-in dispatch waves and their preflight audit."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOOR_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
WAVE_REFERENCE = ROOT / "docs" / "WAVE_ENFORCEMENT.md"


def _source() -> str:
    return FLOOR_PLUGIN.read_text(encoding="utf-8")


def test_wave_width_uses_canonical_ceiling_and_is_env_tunable() -> None:
    source = _source()
    assert "GLUDD_DISPATCH_WAVE_WIDTH" in source
    assert "String(HARD_MAX_DISPATCHES)" in source
    assert "Math.min(" in source
    assert "WAVE_WIDTH" in source


def test_explicitly_configured_undersized_wave_is_blocked_before_inline_work() -> None:
    source = _source()
    assert "WAVE WIDTH VIOLATION" in source
    assert "_prevMessageDispatchCount < eff.floor" in source
    assert "eff.floor > 1" in source
    assert 'permissionDecision: "deny" as const' in source


def test_dispatch_preflight_is_recorded_before_first_wave_member() -> None:
    source = _source()
    assert "recordDispatchPreflight" in source
    assert "gludd-dispatch-preflight.json" in source
    assert "required_width" in source


def test_reference_documentation_defines_pre_dispatch_audit() -> None:
    reference = WAVE_REFERENCE.read_text(encoding="utf-8")
    assert "Pre-dispatch audit" in reference
    assert "zero subagents" in reference.lower()
    assert "three" in reference.lower()
    assert "deduplicate" in reference.lower()
    assert "enhancement" in reference.lower()
