"""Keep completed post-v0.1.1 backlog work assigned to v0.1.2."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
TASKS = ROOT / "TASKS.md"
DECK = ROOT / "docs/presentation/deck/index.html"

RELEASE_HEADING = "## Next release (v0.1.2) — Unreleased"
RELEASE_TOKEN = "v0.1.2-completed-backlog"
COMPLETED_ITEMS = (
    ("S83.114", "Fail-closed chemistry entity resolution"),
    ("S83.115", "Standards-consistent X.509 chain validation"),
    ("S83.116", "Monotonic debounce, throttle, and watchdog state"),
    ("S83.117", "Authenticated TLS 1.3 state and directional records"),
)


def _release_section(changelog: str) -> str:
    """Return only the unreleased v0.1.2 section."""
    return changelog.split(RELEASE_HEADING, 1)[1].split("## [0.1.1]", 1)[0]


def _release_contract(deck: str) -> str:
    """Return the consolidated v0.1.2 backlog contract."""
    opening = f'<div data-contract="{RELEASE_TOKEN}">'
    return deck.split(opening, 1)[1].split("</div>", 1)[0]


def test_changelog_assigns_every_formally_completed_backlog_item_to_v012() -> None:
    """The next release notes must enumerate the complete closed backlog set."""
    changelog = CHANGELOG.read_text(encoding="utf-8")
    section = _release_section(changelog)

    assert "Completed backlog items" in section
    for task_id, title in COMPLETED_ITEMS:
        assert task_id in section
        assert title in section

    assert "- **S83.158" not in section
    assert "- **S83.166" not in section
    assert "implemented but" in section.lower()
    assert "still open" in section.lower()


def test_task_ledger_declares_the_exact_v012_completed_backlog_scope() -> None:
    """The evidence ledger must name the release assignment without changing status."""
    tasks = TASKS.read_text(encoding="utf-8")
    opening = f'<!-- {RELEASE_TOKEN} -->'
    contract = tasks.split(opening, 1)[1].split("<!-- /v0.1.2-completed-backlog -->", 1)[0]

    assert "S83 items 114-117" in contract
    positions = [
        contract.index(f"| {task_id.rsplit('.', 1)[1]} |")
        for task_id, _ in COMPLETED_ITEMS
    ]
    assert positions == sorted(positions)
    assert "four formally completed" in contract
    assert "S83.158 and S83.166" in contract
    assert "remain open" in contract


def test_reveal_deck_maps_the_same_four_completed_items_to_v012() -> None:
    """The presentation must expose the same release scope without losing slides."""
    deck = DECK.read_text(encoding="utf-8")
    contract = _release_contract(deck)

    positions = [contract.index(task_id) for task_id, _ in COMPLETED_ITEMS]
    assert positions == sorted(positions)
    for task_id, title in COMPLETED_ITEMS:
        assert title in contract
        contract_prefix = task_id.lower().replace(".", "-")
        assert deck.count(f'data-contract="{contract_prefix}') == 1

    assert "implemented &ne; closed" in contract.lower()
    assert deck.count("<section") == 51
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
