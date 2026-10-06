"""Structural contracts for navigable documentation shards."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ML_SPEC = ROOT / "docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md"
ML_CONTINUAL = ML_SPEC.parent / "ml-ai-expert" / "continual-evolution.md"
BETA4_CI = ROOT / "docs/features/BETA4_DUAL_TRACK_CI.md"
BETA4_PROMOTION = BETA4_CI.parent / "beta4-dual-track-ci" / "exact-sha-promotion.md"


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


def test_beta4_ci_routes_exact_sha_operations_below_line_limit() -> None:
    """Keep the dual-track overview concise without discarding release evidence."""
    overview = BETA4_CI.read_text(encoding="utf-8")
    promotion = BETA4_PROMOTION.read_text(encoding="utf-8")

    assert len(overview.splitlines()) < 2_500
    assert "[exact-SHA promotion and release operations](beta4-dual-track-ci/exact-sha-promotion.md)" in overview
    for marker in (
        "## Exact-SHA promotion contract (2026-08-31)",
        "### Durable plugin state is explicit test input (2026-09-29)",
    ):
        assert marker not in overview
        assert marker in promotion
    assert len(promotion.splitlines()) < 2_500
