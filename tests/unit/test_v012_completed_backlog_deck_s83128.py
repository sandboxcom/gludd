"""Pin the five-item v0.1.2 completed-backlog Reveal panel."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
RELEASE_TOKEN = "v0.1.2-completed-backlog"
COMPLETED_ITEMS = (
    ("S83.114", "Fail-closed chemistry entity resolution"),
    ("S83.115", "Standards-consistent X.509 chain validation"),
    ("S83.116", "Monotonic debounce, throttle, and watchdog state"),
    ("S83.117", "Authenticated TLS 1.3 state and directional records"),
    ("S83.128", "invoking-worktree-safe virtual-environment reclamation"),
)


def _completed_backlog_contract(deck: str) -> str:
    """Return only the consolidated completed-backlog panel."""
    opening = f'<div data-contract="{RELEASE_TOKEN}">'
    return deck.split(opening, 1)[1].split("</div>", 1)[0]


def test_reveal_panel_names_the_exact_five_completed_items() -> None:
    """The v0.1.2 panel must add S83.128 without closing later work."""
    deck = DECK.read_text(encoding="utf-8")
    contract = _completed_backlog_contract(deck)

    assert "5 formally closed" in contract
    expected_entries = tuple(
        f"<li><strong>{task_id} &mdash; {title}</strong></li>"
        for task_id, title in COMPLETED_ITEMS
    )
    positions = [contract.index(entry) for entry in expected_entries]
    assert positions == sorted(positions)
    assert contract.count("<li><strong>S83.") == len(COMPLETED_ITEMS)
    assert "S83.169" not in contract


def test_reveal_update_preserves_slides_tokens_and_decision_api_content() -> None:
    """The narrow panel edit must not disturb the surrounding deck contract."""
    deck = DECK.read_text(encoding="utf-8")

    assert deck.count(f'data-contract="{RELEASE_TOKEN}"') == 1
    assert deck.count('data-contract="decision-log-codification-v1"') == 1
    assert "/api/v1/decision-codification/analyze" in deck
    assert deck.count("<section") == 62
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
