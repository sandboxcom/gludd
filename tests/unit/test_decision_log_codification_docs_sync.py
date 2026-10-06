"""Keep the decision-log codification guide, design status, and deck aligned."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURE = ROOT / "docs/features/DECISION_LOG_CODIFICATION.md"
SPEC = ROOT / "docs/design/specs/SPEC_DECISION_LOG_CODIFICATION.md"
DECK = ROOT / "docs/presentation/deck/index.html"


def _decision_log_slide() -> str:
    """Return the reveal.js slide for the stable codification contract."""
    content = DECK.read_text(encoding="utf-8")
    return content.split('<section data-contract="decision-log-codification-v1">', 1)[
        1
    ].split("</section>", 1)[0]


def test_feature_guide_pins_safe_runtime_and_rollout_boundaries() -> None:
    """The operator guide must retain every safety-critical product boundary."""
    feature = FEATURE.read_text(encoding="utf-8")

    for marker in (
        "Core and opt-in application adapter implemented",
        "automatic live-flow integration pending",
        "RunBundleStore.read_verified()",
        "VerifiedDecisionSourceV1",
        "Exact-context abstention",
        "DecisionAbstentionV1",
        "DecisionLogAnalyzer",
        "DecisionResolver",
        "DecisionCodificationAdapter",
        "disabled by default",
        "zero-LLM hit",
        "shadow -> canary -> canary_10 -> canary_50 -> active",
        "Atomic rollback",
        "explicit human approval",
    ):
        assert marker in feature


def test_feature_guide_retains_long_lived_practitioner_findings() -> None:
    """Upstream user reports must stay connected to their Gludd guardrails."""
    feature = FEATURE.read_text(encoding="utf-8")

    for marker in (
        "RapidFuzz #432",
        "scikit-learn #15629",
        "scikit-learn discussion #25411",
        "OPA #2379",
        "OPA #1514",
    ):
        assert marker in feature


def test_design_spec_records_implemented_core_and_pending_integration() -> None:
    """The design status must not present shipped core as an active daemon path."""
    spec = SPEC.read_text(encoding="utf-8")

    assert "**Status: CORE AND ANALYSIS API IMPLEMENTED; LIVE INTEGRATION PENDING**" in spec
    assert "## 0. Implementation status (2026-10-06)" in spec
    assert "DecisionLogAnalyzer" in spec
    assert "DecisionResolver" in spec
    assert "DecisionCodificationAdapter" in spec
    assert "explicit injection" in spec
    assert "single-writer R4 integration remains" in spec


def test_docs_pin_the_bounded_authenticated_analysis_api() -> None:
    """The guide, spec, and deck must agree on the analysis-only HTTP boundary."""
    feature = FEATURE.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    slide = _decision_log_slide()

    for document in (feature, spec):
        normalized = " ".join(document.split())
        for marker in (
            "POST /api/v1/decision-codification/analyze",
            "DecisionAnalysisRequest",
            "DecisionAnalysisResponse",
            "64 KiB",
            "256 unique safe run IDs",
            "128 candidate summaries",
            "authentication and authorization run before analysis",
            "exact project and policy scope",
            "digests, counts, and closed rejection enums",
            "does not approve or activate",
            "CLI, automatic live-flow invocation, and durable configuration remain pending",
        ):
            assert marker in normalized

    for marker in (
        "/api/v1/decision-codification/analyze",
        "Auth before analysis",
        "1-256 unique safe run IDs",
        "64 KiB",
        "128 digest/count summaries",
        "Analysis only",
        "no approval or activation",
        "CLI, live-flow, durable config pending",
    ):
        assert marker in slide


def test_reveal_deck_mirrors_the_decision_codification_contract() -> None:
    """The deck must retain the same trust, abstention, and ZDD story."""
    slide = _decision_log_slide()

    for marker in (
        "Verified signed bundles",
        "Exact context or abstain",
        "Human approval",
        "shadow",
        "1%",
        "10%",
        "50%",
        "active",
        "Atomic rollback",
        "zero-LLM hit",
        "DecisionLogAnalyzer",
        "DecisionResolver",
        "DecisionCodificationAdapter",
        "disabled by default",
        "docs/features/DECISION_LOG_CODIFICATION.md",
    ):
        assert marker in slide
