"""Pin bounded hosted-runner acquisition recovery in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"


def _recovery_section() -> str:
    """Return the section mirroring runner-acquisition recovery."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<div data-contract="runner-acquisition-recovery">', 1)[
        1
    ].split("</div>", 1)[0]


def test_deck_pins_ledger_first_runner_recovery_in_order() -> None:
    """Recovery remains evidence-first, bounded, coherent, and zero-downtime."""
    deck = DECK.read_text(encoding="utf-8")
    section = _recovery_section()

    ordered_markers = (
        "Record first",
        "Prove exact acquisition",
        "Rerun coherently",
        "Stop at attempt 2",
        "Keep recovery control-plane only",
    )
    positions = [section.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert deck.count("<section") == 60
    assert deck.index('data-contract="hosted-runner-capacity"') < deck.index(
        'data-contract="runner-acquisition-recovery"'
    )

    for marker in (
        "ci-recover-runner-acquisition",
        "ci-view",
        "complete terminal attempt",
        "run ID/attempt + full SHA",
        "every non-successful job",
        "no executed failing step",
        "The job was not acquired by Runner of type hosted even after multiple attempts",
        "attempt 1 only",
        "full same-SHA rerun",
        "dependent jobs and artifacts remain one coherent candidate proof",
        "attempt 2 is a hard stop",
        "diagnosis instead of an automatic loop",
        "no live restart, traffic shift, tag move, artifact upload, or credential mutation",
        "docs/features/CI_FAILURE_LEDGER.md",
        "tests/unit/test_ci_failure_ledger.py",
    ):
        assert marker in section


def test_deck_retains_every_prior_contract_and_live_token() -> None:
    """The recovery delta cannot displace prior slides or provenance."""
    deck = DECK.read_text(encoding="utf-8")

    for contract in (
        "s83-157-next-live-proof",
        "s83-163-frozen-delta-hold",
        "service-reliability-boundaries",
        "release-resilience-boundaries",
        "hosted-runner-capacity",
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
