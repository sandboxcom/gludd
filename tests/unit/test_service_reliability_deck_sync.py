"""Pin the service-discovery and lifecycle reliability story in the deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"


def _reliability_slide() -> str:
    """Return the one slide that composes the four reliability contracts."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="service-reliability-boundaries">', 1)[
        1
    ].split("</section>", 1)[0]


def test_reveal_deck_composes_the_four_reliability_boundaries_in_order() -> None:
    """The deck retains the fail-closed path from input through shutdown."""
    slide = _reliability_slide()

    ordered_markers = (
        "Validate before I/O",
        "Isolate every test",
        "Choose lifecycle explicitly",
        "Keep shutdown observable",
    )
    positions = [slide.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)

    for marker in (
        "(identifier, query)",
        "plain strings stay compatible",
        "empty results preserve the catalog",
        "monkeypatch",
        "PID + counter",
        "pre-startup",
        "/readyz",
        "context-managed startup",
        "per-app in-memory database",
        "closed log stream",
        "stderr file descriptor 2",
        "restore handlers + propagation",
        "docs/features/SERVICE_DISCOVERY_SEARCH_TERM_SCHEMA.md",
        "docs/features/FLOOR_TEST_ENV_ISOLATION.md",
        "docs/features/READINESS_LIFESPAN_TESTING.md",
        "docs/features/RESOURCE_LIFECYCLE_SHUTDOWN_LOGGING.md",
    ):
        assert marker in slide


def test_reveal_deck_retains_prior_decisions_and_live_tokens() -> None:
    """The reliability insertion cannot displace decisions or build provenance."""
    deck = DECK.read_text(encoding="utf-8")

    assert deck.count('data-contract="s83-157-next-live-proof"') == 1
    assert deck.count('data-contract="s83-163-frozen-delta-hold"') == 1
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
