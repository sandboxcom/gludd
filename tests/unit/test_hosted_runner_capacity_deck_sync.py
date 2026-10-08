"""Pin the hosted-runner capacity contract in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"


def _capacity_slide() -> str:
    """Return the deck section that mirrors hosted-runner capacity."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<div data-contract="hosted-runner-capacity">', 1)[
        1
    ].split("</div>", 1)[0]


def test_deck_pins_bounded_hosted_runner_capacity_in_order() -> None:
    """The deck preserves acquisition, classification, and rollout ordering."""
    deck = DECK.read_text(encoding="utf-8")
    slide = _capacity_slide()

    ordered_markers = (
        "Bound acquisition",
        "Keep coverage parallel",
        "Classify before retry",
        "Roll forward without downtime",
    )
    positions = [slide.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert deck.count("<section") == 63

    for marker in (
        "S83.166",
        "ubuntu-24.04",
        "four Linux runners instead of eight",
        "Build gate max-parallel=1",
        "standalone Molecule max-parallel=3",
        "later fan-out 1 / 3 / 2",
        "six instead of fourteen",
        "fail-fast=false",
        "every version and shard still reports",
        "not acquired by Runner",
        "before any step ran",
        "one annotation-proven retry",
        "no automatic attempt 3",
        "newer exact SHA",
        "inverse YAML change",
        "no release artifact, application state, credential, or production resource mutation",
        "docs/features/HOSTED_RUNNER_CAPACITY.md",
        ".github/workflows/build.yml",
        ".github/workflows/molecule.yml",
        "TASKS.md",
    ):
        assert marker in slide


def test_deck_retains_prior_contracts_and_live_tokens() -> None:
    """The capacity insertion cannot displace prior slides or provenance."""
    deck = DECK.read_text(encoding="utf-8")

    for contract in (
        "s83-157-next-live-proof",
        "s83-163-frozen-delta-hold",
        "service-reliability-boundaries",
        "release-resilience-boundaries",
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
