"""Pin the S83.116 monotonic timing contract across docs, deck, and ledger."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
FEATURE = ROOT / "docs/features/DEBOUNCE_MONOTONIC_STATE.md"
TASKS = ROOT / "TASKS.md"


def _debounce_slide() -> str:
    """Return only the reveal.js slide that mirrors the timing contract."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="s83-116-monotonic-debounce">', 1)[
        1
    ].split("</section>", 1)[0]


def test_reveal_deck_mirrors_the_monotonic_timing_contract() -> None:
    """The presentation must retain timing, resource, and ZDD boundaries."""
    feature = FEATURE.read_text(encoding="utf-8")
    slide = _debounce_slide()

    for marker in (
        "Each trailing debounce call restarts",
        "A debounce max-wait may be shorter",
        "separate ownership",
        "Re-registering a live watchdog operation is a no-op",
        "No retry loop, thread, or hidden polling process is introduced",
        "Rollback sends new traffic",
    ):
        assert marker in feature

    for marker in (
        "S83.116",
        "Monotonic state",
        "zero clock epoch",
        "latest call",
        "max-wait",
        "separate task ownership",
        "live registration is idempotent",
        "160/160 warning-strict",
        "95% aggregate",
        "90% / 93% / 98%",
        "one trailing timer",
        "zero network",
        "rolling replacement",
        "drain old workers",
        "source-only rollback",
        "docs/features/DEBOUNCE_MONOTONIC_STATE.md",
    ):
        assert marker in slide


def test_task_closeout_names_the_measured_timing_evidence() -> None:
    """The ledger must close S83.116 only with reproducible evidence."""
    task = next(
        line
        for line in TASKS.read_text(encoding="utf-8").splitlines()
        if "S83.116" in line
    )

    for marker in (
        "- [x] S83.116",
        "`13b933128`",
        "`dc5082aac`",
        "160/160",
        "95% aggregate",
        "`debounce.py` at 90%",
        "`debounce_v2.py` at 93%",
        "`timing.py` at 98%",
        "117656/117657",
        "s83-116-monotonic-debounce",
        "status: completed",
    ):
        assert marker in task
