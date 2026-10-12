"""Pin visible replay evidence and slide-scoped spacing capacity."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck"
CONTRACT = "release-resilience-boundaries"
SCOPE = f'.reveal section[data-contract="{CONTRACT}"]'


def test_replay_evidence_slide_preserves_content_while_reclaiming_spacing() -> None:
    """The failing WebKit slide must fit through spacing, never hidden evidence."""
    deck = (DECK / "index.html").read_text(encoding="utf-8")
    opening = f'<section data-contract="{CONTRACT}">'
    assert deck.count(opening) == 1
    assert deck.count("<section") == 63
    slide = deck.split(opening, 1)[1].split("</section>", 1)[0]

    contracts = (
        "hosted-runner-capacity",
        "runner-acquisition-recovery",
        "s83-166-published-rollback-receipt",
        "G10-RR1-R0-SCHEMA-LEGACY",
    )
    positions = [slide.index(f'<div data-contract="{contract}">') for contract in contracts]
    assert positions == sorted(positions)
    lists = re.findall(r"<ol\b[^>]*>(.*?)</ol>", slide, re.DOTALL)
    assert [len(re.findall(r"<li\b", group)) for group in lists] == [3, 4, 5, 9, 5]
    assert len(re.findall(r"<h[23]\b", slide)) == 5
    assert slide.count('class="honest-note"') == 5
    assert slide.count('class="cite"') == 5

    visible_text = " ".join(unescape(re.sub(r"<[^>]*>", "", slide)).split())
    for evidence in (
        "Frozen v1 surface",
        "Fail closed",
        "canonical integrity serialization makes equivalent accepted evidence hash identically",
        "schema=legacy-v0",
        "integrity=unverified",
        "without writes",
        "recorder, router, CLI, signing, store, simulation, and re-execution remain pending",
        "Legacy evidence stays visibly unverified",
        "no migration, replay execution, or runtime service changes occur",
        "src/general_ludd/replay/schema.py",
        "src/general_ludd/replay/legacy.py",
        "tests/unit/test_replay_schema.py",
        "tests/unit/test_replay_legacy.py",
    ):
        assert evidence in visible_text
    assert not re.search(r"<[^>]*\b(?:hidden|aria-hidden)\b", slide)
    assert not re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0)", slide)

    styles = (DECK / "presentation.css").read_text(encoding="utf-8")
    assert "@media (min-width: 641px) and (min-height: 641px)" in styles
    expected_margins = {
        " > h2": "0 0 0.35em",
        " h3": "0.3em 0 0.1em",
        " .bullets-tight": "0.12em 0 0.12em 1em",
        " .honest-note": "0.45em 0 0.2em",
        " .cite": "0.3em 0",
    }
    assert styles.count(SCOPE) == len(expected_margins)
    for descendant, margin in expected_margins.items():
        rule = re.search(re.escape(SCOPE + descendant) + r"\s*\{([^{}]*)\}", styles)
        assert rule is not None, f"Missing slide-scoped capacity rule: {descendant}"
        declarations = {}
        for declaration in rule.group(1).split(";"):
            if declaration.strip():
                property_name, value = declaration.strip().split(":", 1)
                declarations[property_name.strip()] = value.strip()
        assert declarations == {"margin": margin}
