from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs" / "audit" / "AGENT_BEHAVIOR_FAILURE_AUDIT.md"


def _audit_text() -> str:
    return AUDIT.read_text()


def test_behavior_audit_names_exact_guard_for_dirty_main_checkout() -> None:
    text = _audit_text()

    assert "Main checkout dirty while isolated worktree commits exist" in text
    assert "main-worktree-guard" in text
    assert "all-worktree-state" in text
    assert "tests/unit/test_worktree_state_guard.py" in text
    assert "test_main_worktree_guard_fails_when_canonical_checkout_is_dirty" in text


def test_behavior_audit_blocks_broad_fixed_claims_without_specific_evidence() -> None:
    text = _audit_text()

    assert "A behavioral guard only counts when it names the exact failure path" in text
    assert "If no row exists, say it is not yet codified" in text
    assert "If a row status is partial, do not describe the behavior as fixed" in text


def test_behavior_audit_covers_local_vs_gha_dirty_state_mismatch() -> None:
    text = _audit_text()

    assert "Local CI-replica tests run dirty code that GHA cannot run" in text
    assert "_ci-replica-clean-tree" in text
    assert "scripts/worktree_state_guard.py --assert-clean --claim-token" in text
    assert "test_local_ci_replica_shards_refuse_dirty_tree_by_default" in text
