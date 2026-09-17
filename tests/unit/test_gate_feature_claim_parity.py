"""Pin hosted feature-claim verification into the full local gate."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _full_gate_recipe() -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    return makefile.split("\ngate:", maxsplit=1)[1].split("\n# gate-lite:", maxsplit=1)[0]


def test_full_gate_runs_hosted_feature_claim_check_fail_closed() -> None:
    recipe = _full_gate_recipe()

    assert "=== GATE PHASE: verify-feature-claims ===" in recipe
    assert 'printf "verify-feature-claims " >> .gate-status.next' in recipe
    assert "$(MAKE) --no-print-directory verify-feature-claims" in recipe
    assert "touch .gate-failed" in recipe
