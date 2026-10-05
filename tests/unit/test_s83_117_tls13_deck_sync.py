"""Pin the S83.117 authenticated TLS 1.3 contract in the reveal deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT_TOKEN = "s83-117-authenticated-tls13-state"


def _tls13_slide() -> str:
    """Return the one reveal.js slide for authenticated TLS 1.3 state."""
    content = DECK.read_text(encoding="utf-8")
    opening = f'<section data-contract="{CONTRACT_TOKEN}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_reveal_deck_retains_authenticated_tls13_state_contract() -> None:
    """The deck must retain authentication, resource, and rollout boundaries."""
    slide = _tls13_slide()

    for marker in (
        "S83.117",
        "exact handshake frame",
        "CertificateVerify",
        "server Finished",
        "poison",
        "independent read/write protectors",
        "79/79 warning-strict",
        "89% aggregate",
        "92.2% line / 76.3% branch",
        "constant state",
        "zero network",
        "rolling replacement",
        "drain in-flight handshakes",
        "source-only rollback",
        "docs/features/TLS13_AUTHENTICATED_STATE.md",
        "CPython #91826",
    ):
        assert marker in slide
