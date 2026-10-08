"""Pin release, recovery, and worktree resilience in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"


def _resilience_slide() -> str:
    """Return the slide composing the three merged resilience contracts."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="release-resilience-boundaries">', 1)[
        1
    ].split("</section>", 1)[0]


def test_deck_composes_release_recovery_and_worktree_boundaries_in_order() -> None:
    """The deck retains one ordered evidence-to-confinement sequence."""
    deck = DECK.read_text(encoding="utf-8")
    slide = _resilience_slide()

    ordered_markers = (
        "Prove the release",
        "Replay bounded recovery",
        "Confine worktree identity",
    )
    positions = [slide.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert deck.count("<section") == 61

    for marker in (
        "exact tagged SHA",
        "46 artifacts",
        "32 completeness checks",
        "zero zero-byte assets",
        "unsigned tag",
        "no SLSA attestation",
        "fix forward",
        "read-only thread projection",
        "thread + inclusive ordinals",
        "temporary tree",
        "publish only after the whole batch validates",
        "canonical filesystem identity",
        "aliases and escapes fail closed",
        "main-checkout writes",
        "reads remain allowed",
        "docs/RELEASE_RUNBOOK.md",
        "docs/releases/audit-0.1.1.json",
        "docs/features/CODEX_FILE_CHANGE_RECOVERY.md",
        "docs/features/WORKTREE_AUDIT_IDENTITY_CONTRACT.md",
    ):
        assert marker in slide


def test_deck_retains_existing_contract_slides_and_live_tokens() -> None:
    """The insertion cannot displace earlier decisions or live provenance."""
    deck = DECK.read_text(encoding="utf-8")

    for contract in (
        "s83-157-next-live-proof",
        "s83-163-frozen-delta-hold",
        "service-reliability-boundaries",
    ):
        assert deck.count(f'data-contract="{contract}"') == 1
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
