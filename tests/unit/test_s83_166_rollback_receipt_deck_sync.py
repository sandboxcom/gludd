"""Pin the published rollback receipt contract in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"

CONTRACT_TOKEN = "s83-166-published-rollback-receipt"
PRESERVED_CONTRACT = "s83-158-claim-before-provision"
REPLAY_SCHEMA_TOKEN = "G10-RR1-R0-SCHEMA-LEGACY"


def _rollback_receipt_contract(content: str) -> str:
    """Return the receipt block without coupling to surrounding slide text."""
    opening = f'<div data-contract="{CONTRACT_TOKEN}">'
    return content.split(opening, 1)[1].split("</div>", 1)[0]


def _replay_schema_contract(content: str) -> str:
    """Return the frozen-schema block independently of its host slide."""
    opening = f'<div data-contract="{REPLAY_SCHEMA_TOKEN}">'
    return content.split(opening, 1)[1].split("</div>", 1)[0]


def test_deck_publishes_checksum_bound_rollback_receipt_in_order() -> None:
    """The deck must retain the complete activation-to-attestation proof chain."""
    deck = DECK.read_text(encoding="utf-8")
    contract = _rollback_receipt_contract(deck)

    ordered_markers = (
        "Fan-in",
        "Candidate activation",
        "Restoration",
        "Active work",
        "Publication binding",
        "Published-byte acquisition",
        "Local replay gate",
        "Bounded cleanup",
        "Mutation boundary",
    )
    positions = [contract.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)

    for marker in (
        "S83.166",
        "15/15 platform-category smoke fan-in",
        "candidate SHA + version + health",
        "byte-identical prior route restored",
        "active-work SHA unchanged",
        "inventoried, checksummed, and attested",
        "After publication",
        "rollback receipt, checksum index, release manifest, candidate archive, and smoke attestations",
        "verify-published-rollback",
        "before remote completeness",
        "run/attempt-namespaced",
        "cleanup failure blocks the job",
        "zero new network mutation",
        "docs/RELEASE_RUNBOOK.md",
        "Argo Rollouts #501",
        "Kubernetes #50021",
        "GitHub Community #161656",
    ):
        assert marker in contract


def test_deck_pins_frozen_replay_schema_and_read_only_legacy_boundary() -> None:
    """The deck must distinguish strict v1 evidence from unverified legacy reads."""
    deck = DECK.read_text(encoding="utf-8")
    contract = _replay_schema_contract(deck)

    ordered_markers = (
        "Frozen v1 surface",
        "Fail closed",
        "Canonical integrity",
        "Legacy reads",
        "Explicit nonclaim",
    )
    positions = [contract.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)

    for marker in (
        "strict frozen v1 manifest/event schemas",
        "unsafe, traversal, confusable, or device IDs",
        "extras or unknown schemas",
        "duplicate keys or non-finite JSON",
        "canonical integrity serialization",
        "schema=legacy-v0",
        "integrity=unverified",
        "without writes",
        "recorder, router, CLI, signing, store, simulation, and re-execution remain pending",
    ):
        assert marker in contract


def test_deck_preserves_prior_contract_and_slide_inventory() -> None:
    """The receipt sync cannot replace the durable S83.158 proof or a slide."""
    deck = DECK.read_text(encoding="utf-8")

    assert deck.count(f'data-contract="{CONTRACT_TOKEN}"') == 1
    assert deck.count(f'data-contract="{PRESERVED_CONTRACT}"') == 1
    assert deck.count(f'data-contract="{REPLAY_SCHEMA_TOKEN}"') == 1
    assert deck.count("<section") == 62
    assert deck.count('data-contract="decision-log-codification-v1"') == 1
    assert "/api/v1/decision-codification/analyze" in deck
    assert deck.count('data-contract="v0.1.2-completed-backlog"') == 1
    completed_panel = deck.split(
        '<div data-contract="v0.1.2-completed-backlog">', 1
    )[1].split("</div>", 1)[0]
    assert "5 formally closed" in completed_panel
    assert "S83.128" in completed_panel
    assert "<li><strong>S83.158" not in completed_panel
    assert "<li><strong>S83.166" not in completed_panel
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
