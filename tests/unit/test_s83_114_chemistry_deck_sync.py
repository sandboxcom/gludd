"""Pin the S83.114 fail-closed chemistry contract across docs, deck, and ledger."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
FEATURE = ROOT / "docs/features/CHEMISTRY_ENTITY_FAIL_CLOSED_RESOLUTION.md"
TASKS = ROOT / "TASKS.md"


def _chemistry_slide() -> str:
    """Return only the reveal.js slide that mirrors the chemistry contract."""
    content = DECK.read_text(encoding="utf-8")
    return content.split(
        '<section data-contract="s83-114-chemistry-entity-resolution">',
        1,
    )[1].split("</section>", 1)[0]


def test_reveal_deck_mirrors_fail_closed_chemistry_resolution() -> None:
    """The presentation must retain chemistry evidence and rollout boundaries."""
    feature = FEATURE.read_text(encoding="utf-8")
    slide = _chemistry_slide()

    for marker in (
        "complete empty failure sentinel",
        "only one empty text field is rejected",
        "Pydantic model validators",
        "preserving undefined stereochemistry from 2020",
        "isotope-handling discussion from 2011",
        "fixed, non-recursive scans",
        "same worker",
        "source-only deployment rollback",
    ):
        assert marker in feature

    for marker in (
        "S83.114",
        "Fail closed",
        "complete empty sentinel",
        "one-sided empty states",
        "specified / partial / unknown",
        "specified / natural / unknown",
        "RDKit adapter seam",
        "116/116 warning-strict",
        "94% aggregate",
        "92% / 99%",
        "fixed regex scans",
        "zero network access",
        "same worker",
        "canary status counts",
        "drain or pin sentinel-bearing requests",
        "source-only rollback",
        "docs/features/CHEMISTRY_ENTITY_FAIL_CLOSED_RESOLUTION.md",
    ):
        assert marker in slide


def test_task_closeout_names_measured_chemistry_evidence() -> None:
    """The ledger must close S83.114 only with reproducible evidence."""
    task = next(
        line
        for line in TASKS.read_text(encoding="utf-8").splitlines()
        if "S83.114" in line
    )

    for marker in (
        "- [x] S83.114",
        "`95b2887d`",
        "failing-first presentation drift regression reproduced 2/2 RED",
        "116/116",
        "1,262 passed",
        "94% aggregate",
        "`core.py` at 92%",
        "`schemas.py` at 99%",
        "117658/117659",
        "s83-114-chemistry-entity-resolution",
        "status: completed",
    ):
        assert marker in task
