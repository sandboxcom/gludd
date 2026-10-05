"""Pin the S83.163 FreeLLMAPI HOLD decision across docs and deck."""

from __future__ import annotations

import json
from pathlib import Path

from general_ludd.models.freellmapi.three_arm_contracts import canonical_digest

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
DESIGN = ROOT / "docs/design/FREELLMAPI_UPSTREAM_INTEGRATION.md"
RECEIPT = ROOT / "config/freellmapi/three_arm_replay_receipt.json"


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


def test_exact_three_arm_hold_and_measured_comparison_are_pinned() -> None:
    """The exact replay remains a hold because it ties the admitted artifact."""
    decision = _decision_section()

    for marker in (
        "**HOLD**",
        "`v0.9.9`",
        "`v0.11.1`",
        "`v0.12.0`",
        "32 route groups",
        "96 included observations",
        "8 preregistered exclusions",
        "`e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845`",
        "`+0.5165816326530612`",
        "`4.656612873077393e-10`",
        "`0.0`",
        "`1.0`",
        "`quality_lcb:freellmapi_v0_9_9`",
        "`mcnemar:freellmapi_v0_9_9`",
        "Node cross-check matched QuickJS",
        "zero network calls",
        "zero incremental cost",
        "zero bridge faults",
        "RSS delta was `0.015625 MiB`",
        "maximum candidate call was `0.244458 ms`",
        "`runtime_admitted: false`",
        "no retargeting",
    ):
        assert marker in decision

    for implementation in (
        "evaluator",
        "pytest",
        "Hypothesis",
        "NumPy",
        "standard-library exact binomial",
        "psutil",
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


def test_reveal_deck_mirrors_the_measured_hold_boundary() -> None:
    """The canonical deck cannot turn the admitted-arm tie into promotion."""
    slide = _deck_slide()

    for marker in (
        "S83.163",
        "HOLD",
        "exact v0.11.1 executed",
        "signed 4191d8e7",
        "exact v0.11.1",
        "v0.12.0",
        "32 route groups",
        "96 observations",
        "8 exclusions",
        "+0.5166",
        "4.66e-10",
        "LCB 0.0",
        "p = 1.0",
        "0.244458 ms max",
        "0.015625 MiB RSS delta",
        "Node = QuickJS",
        "network/cost/faults = 0",
        "v0.9.9 stays serving",
        "No retargeting",
        "docs/design/FREELLMAPI_UPSTREAM_INTEGRATION.md",
    ):
        assert marker in slide

    for thread in ("#456", "#533", "#608", "#666", "#880", "#1210", "#1262"):
        assert thread in slide


def test_tracked_replay_receipt_is_content_addressed_and_non_promoting() -> None:
    """The measured receipt binds the exact HOLD evidence and serving artifact."""
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    unsigned = dict(receipt)
    evidence_id = unsigned.pop("evidence_id")

    assert evidence_id == f"sha256:{canonical_digest(unsigned)}"
    assert receipt["decision"] == "HOLD"
    assert receipt["all_gates_passed"] is False
    assert receipt["promotion_permitted"] is False
    assert receipt["runtime_admitted"] is False
    assert receipt["serving_bundle_sha256"] == (
        "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"
    )
    assert receipt["failed_gates"] == [
        "quality_lcb:freellmapi_v0_9_9",
        "mcnemar:freellmapi_v0_9_9",
    ]
    assert receipt["candidate_max_latency_ms"] <= 25.0
    assert receipt["rss_delta_mib"] <= 32.0
    assert receipt["network_calls"] == 0
    assert receipt["cost_usd"] == 0.0
    assert receipt["bridge_fault_count"] == 0
    assert receipt["node_crosscheck_passed"] is True
