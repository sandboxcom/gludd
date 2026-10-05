"""Keep S83.158's claim-before-provision contract synchronized."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE = ROOT / "docs/features/TODO_DRIVEN_COMPUTE_LIFECYCLE.md"
DECK = ROOT / "docs/presentation/deck/index.html"


def test_feature_doc_records_claim_before_provision_and_practitioner_evidence() -> None:
    """The canonical feature doc must retain the ordering and external evidence."""
    content = FEATURE.read_text(encoding="utf-8")

    for marker in (
        "## Claim-before-provision boundary",
        "claim and lease transaction is committed",
        "Celery discussion #9460",
        "Sidekiq FAQ",
        "Celery issue #3765",
        "zero pre-claim provisioning",
        "foreign claim",
    ):
        assert marker in content


def test_reveal_deck_mirrors_claim_before_provision_in_order() -> None:
    """The published lifecycle cannot drift back to provision-before-claim."""
    content = DECK.read_text(encoding="utf-8")
    contract = content.split(
        '<div data-contract="s83-158-claim-before-provision">', 1
    )[1].split("</div>", 1)[0]
    markers = (
        "Produce durable todo",
        "Approval boundary",
        "Claim + lease",
        "Commit + close",
        "Provision",
        "Execute + verify",
        "Terminal commit",
        "Exact release",
    )
    positions = [contract.index(marker) for marker in markers]

    assert positions == sorted(positions)
    assert "zero pre-claim compute" in contract
    assert "losing or restarted worker preserves foreign ownership" in contract
    assert "docs/features/TODO_DRIVEN_COMPUTE_LIFECYCLE.md" in contract
