"""Structural contracts for navigable documentation shards."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ML_SPEC = ROOT / "docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md"
ML_CONTINUAL = ML_SPEC.parent / "ml-ai-expert" / "continual-evolution.md"


def test_ml_ai_spec_routes_continual_evolution_below_line_limit() -> None:
    """Keep the authoritative index concise and the full contract discoverable."""
    spec = ML_SPEC.read_text(encoding="utf-8")
    continual = ML_CONTINUAL.read_text(encoding="utf-8")

    assert len(spec.splitlines()) < 2_500
    assert "[continual-evolution contract](ml-ai-expert/continual-evolution.md)" in spec
    for marker in ("### MLCONT.1 ", "### MLCONT.31 "):
        assert marker not in spec
        assert marker in continual
    assert len(continual.splitlines()) < 2_500
