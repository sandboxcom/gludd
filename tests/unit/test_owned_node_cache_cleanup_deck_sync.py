"""Pin bounded owned npm-cache reclamation in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT = ROOT / "docs/features/GATE_RESOURCE_LIFECYCLE.md"
CONTRACT_TOKEN = "owned-node-cache-reclamation"


def _cleanup_token() -> str:
    """Return the deck fragment that mirrors owned npm-cache reclamation."""
    content = DECK.read_text(encoding="utf-8")
    opening = f'<div data-contract="{CONTRACT_TOKEN}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</div>", 1)[0]


def test_deck_mirrors_measured_owned_node_cache_reclamation() -> None:
    """The deck retains measured pressure, refusals, and deletion rechecks."""
    deck = DECK.read_text(encoding="utf-8")
    token = _cleanup_token()

    assert deck.count("<section") == 50
    for marker in (
        "142.2 MiB",
        "52.6 MiB",
        "exact stale, inactive, owned npm caches",
        "regular <code>.sock</code> lookalikes",
        "ambiguous or active caches",
        "tree identity",
        "immediate <code>lstat</code>",
        "docs/features/GATE_RESOURCE_LIFECYCLE.md",
    ):
        assert marker in token


def test_deck_retains_live_tokens_after_cleanup_sync() -> None:
    """The cleanup token cannot displace generated build provenance."""
    deck = DECK.read_text(encoding="utf-8")

    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
    assert "Owned Node download-cache reclamation" in CONTRACT.read_text(
        encoding="utf-8"
    )
