"""Contracts for bounded teardown of the namespaced E2E supervisor."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_e2e_tree_cleanup_escalates_when_supervisor_traps_sigterm() -> None:
    makefile = (ROOT / "Makefile").read_text()
    start = makefile.index("kill-worktree-e2e:")
    recipe = makefile[start : makefile.index("\n.PHONY:", start)]

    assert "/bin/kill -TERM" in recipe
    assert "/bin/kill -KILL" in recipe
    assert "KILL_WORKTREE_E2E_VALIDATE_ONLY" in recipe


def test_e2e_tree_cleanup_has_safe_make_contract() -> None:
    contract = (ROOT / "config/make_target_contract.json").read_text()

    assert '"name": "kill-worktree-e2e"' in contract
    assert "KILL_WORKTREE_E2E_VALIDATE_ONLY=1" in contract
