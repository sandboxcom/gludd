"""Keep S83.158's claim-before-provision contract synchronized."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE = ROOT / "docs/features/TODO_DRIVEN_COMPUTE_LIFECYCLE.md"
OWNERSHIP = ROOT / "docs/features/application-resource-ownership.md"
DECK = ROOT / "docs/presentation/deck/index.html"


def _claim_before_provision_contract(content: str) -> str:
    """Return the contract block while allowing proof metadata on its tag."""
    start = content.index('<div data-contract="s83-158-claim-before-provision"')
    return content[start:].split("</div>", 1)[0]


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
        "periodic producer discover and persist",
        "never injects a self-improvement todo row",
        "cancellation to manufacture zero demand",
        "Azure Container Apps discussion #725",
        "Azure Container Apps issue #1458",
    ):
        assert marker in content


def test_reveal_deck_mirrors_claim_before_provision_in_order() -> None:
    """The published lifecycle cannot drift back to provision-before-claim."""
    content = DECK.read_text(encoding="utf-8")
    contract = _claim_before_provision_contract(content)
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
    assert 'data-proof="s83-158-durable-chain-acceptance"' in contract
    assert "Hermetic closure:" in contract
    assert "no injected self-improve row or cancellation" in contract
    assert "docs/features/TODO_DRIVEN_COMPUTE_LIFECYCLE.md" in contract


def test_docs_pin_failed_commit_restart_and_non_runnable_outcomes() -> None:
    """Both canonical docs must retain the complete durable-claim fence."""
    feature = FEATURE.read_text(encoding="utf-8")
    ownership = OWNERSHIP.read_text(encoding="utf-8")

    for marker in (
        "Commit failure rolls back and clears the detached claimed batch.",
        "No provider or runner call follows that failed commit.",
        "Exactly one durable winner causes exactly one provisioning call",
        "Non-runnable work causes zero compute allocation.",
    ):
        assert marker in feature

    for marker in (
        "## Durable todo claim ownership",
        "claim commit and session close precede provisioning",
        "rollback clears the detached claimed batch",
        "one durable winner and one provisioning call",
        "Non-runnable work retains zero allocation",
    ):
        assert marker in ownership


def test_reveal_deck_pins_failed_commit_and_restart_outcomes() -> None:
    """The presentation must expose failure and restart acceptance outcomes."""
    content = DECK.read_text(encoding="utf-8")
    contract = _claim_before_provision_contract(content)

    for marker in (
        "Commit failure: rollback + clear detached claim; no provision or dispatch.",
        "Competing workers + restart: one durable winner, exactly one provision.",
        "Non-runnable work: zero allocation.",
    ):
        assert marker in contract
