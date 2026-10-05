"""Pin the S83.113 QEMU import-isolation contract in the reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT_TOKEN = "s83-113-qemu-import-isolation"


def test_reveal_deck_retains_qemu_import_isolation_contract() -> None:
    """The deck must retain isolation, resource, and ZDD rollback boundaries."""
    content = DECK.read_text(encoding="utf-8")
    opening = f'<section data-contract="{CONTRACT_TOKEN}">'
    assert content.count(opening) == 1
    slide = content.split(opening, 1)[1].split("</section>", 1)[0]

    for marker in (
        "S83.113",
        "prefix-scoped",
        "sys.modules",
        "parent-to-child bindings",
        "third-party dependencies stay cached",
        "QEMU detection semantics remain unchanged",
        "75/75 warning-strict",
        "100% line and branch",
        "process-local",
        "zero network",
        "no runtime artifact, API, schema, process, or deployment change",
        "source-only test-harness rollback",
        "docs/features/QEMU_IMPORT_ISOLATION.md",
        "CPython #27515",
        "Python Help",
    ):
        assert marker in slide
