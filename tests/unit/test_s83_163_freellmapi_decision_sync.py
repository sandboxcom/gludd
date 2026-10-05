"""Pin the S83.163 FreeLLMAPI HOLD decision across docs and deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
DESIGN = ROOT / "docs/design/FREELLMAPI_UPSTREAM_INTEGRATION.md"


def _decision_section() -> str:
    """Return only the dated S83.163 decision from the canonical design doc."""
    content = DESIGN.read_text(encoding="utf-8")
    return content.split(
        "### S83.163 frozen-delta promotion decision (2026-10-05)", 1
    )[1].split("\n#### Adversarial receipt hardening", 1)[0]


def _deck_slide() -> str:
    """Return the reveal.js slide that mirrors the frozen-delta decision."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="s83-163-frozen-delta-hold">', 1)[
        1
    ].split("</section>", 1)[0]


def test_frozen_delta_hold_and_preregistered_comparison_are_pinned() -> None:
    """The decision remains a hold until an exact three-arm replay clears every gate."""
    decision = _decision_section()

    for marker in (
        "**HOLD**",
        "synthetic",
        "`v0.9.9`",
        "`v0.11.1`",
        "`v0.12.0`",
        "32 route groups",
        "96 included observations",
        "8 preregistered exclusions",
        "paired Brier",
        "LCB",
        "`+0.02`",
        "McNemar",
        "`p < 0.05`",
        "No stratum",
        "`0.02`",
        "p95 added scoring latency",
        "`5 ms`",
        "peak RSS delta",
        "`32 MiB`",
        "incremental provider cost",
        "`$0`",
        "zero bridge faults",
        "no retargeting",
    ):
        assert marker in decision

    for implementation in (
        "evaluator",
        "pytest",
        "Hypothesis",
        "SciPy",
        "psutil",
        "Vitest",
        "Node",
        "no new framework",
    ):
        assert implementation in decision


def test_practitioner_threads_remain_tied_to_decision_effects() -> None:
    """Every retained upstream report names the guardrail it motivates."""
    decision = _decision_section()
    design = DESIGN.read_text(encoding="utf-8")

    for reference, source in (
        ("[issue-456]", "issues/456"),
        ("[discussion-533]", "discussions/533"),
        ("[issue-608]", "issues/608"),
        ("[issue-666]", "issues/666"),
        ("[issue-880]", "issues/880"),
        ("[issue-1210]", "issues/1210"),
        ("[issue-1262]", "issues/1262"),
    ):
        assert reference in decision
        assert source in design
    for effect in (
        "per-key quota",
        "immutable release identity",
        "authenticated readiness",
        "Gludd-owned deadline",
        "catalog row is not a usable route",
        "request-rate ceiling",
        "slow-but-alive",
    ):
        assert effect in decision


def test_reveal_deck_mirrors_the_hold_boundary() -> None:
    """The canonical deck cannot turn incomplete synthetic evidence into promotion."""
    slide = _deck_slide()

    for marker in (
        "S83.163",
        "HOLD",
        "v0.9.9 synthetic",
        "exact v0.11.1",
        "v0.12.0",
        "32 route groups",
        "96 observations",
        "8 exclusions",
        "Brier LCB &ge; +0.02",
        "McNemar <code>p &lt; 0.05</code>",
        "No stratum worse by &gt;0.02",
        "p95 latency &le; 5 ms",
        "RSS &le; 32 MiB",
        "cost = $0",
        "zero faults",
        "No retargeting",
        "docs/design/FREELLMAPI_UPSTREAM_INTEGRATION.md",
    ):
        assert marker in slide

    for thread in ("#456", "#533", "#608", "#666", "#880", "#1210", "#1262"):
        assert thread in slide
