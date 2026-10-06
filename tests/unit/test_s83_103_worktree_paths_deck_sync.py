"""Pin the S83.103 worktree creation boundary in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
FEATURE = ROOT / "docs/features/GIT_WORKTREE_CREATION_PATHS.md"
CONTRACT_TOKEN = "s83-103-canonical-worktree-paths"


def test_reveal_deck_mirrors_canonical_worktree_creation_paths() -> None:
    """The deck must retain path, resource, rollout, and measured boundaries."""
    feature = " ".join(FEATURE.read_text(encoding="utf-8").split())
    content = DECK.read_text(encoding="utf-8")
    opening = f'<section data-contract="{CONTRACT_TOKEN}">'

    assert content.count(opening) == 1
    slide = content.split(opening, 1)[1].split("</section>", 1)[0]

    for marker in (
        "gludd-worktree-<uuid>",
        "resolved with `pathlib.Path.resolve()`",
        "sibling project's namespace is not authority",
        "Stack Overflow discussion",
        "starts no daemon, retry loop, network request",
        "Old and new processes can overlap",
        f"token `{CONTRACT_TOKEN}`",
        "single-sourced in commit `6f3a98ad3`",
    ):
        assert marker in feature

    for marker in (
        "S83.103",
        "before any Git mutation",
        "32 lowercase hexadecimal",
        "raw .. traversal",
        "symlink escape",
        "foreign project namespace",
        "35/35 warning-strict",
        "421/421 Git repository family",
        "86.00%",
        "zero network",
        "rolling replacement",
        "source-only rollback",
        "docs/features/GIT_WORKTREE_CREATION_PATHS.md",
    ):
        assert marker in slide
