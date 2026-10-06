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
    normalized = " ".join(feature.split())

    for marker in (
        "CLI and opt-in live REVIEW integration implemented",
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
        "durable multiworker configuration, recorder/outcome feedback, and deployed live-traffic proof remain pending",
    ):
        assert marker in normalized

    assert "automatic live-flow integration pending" not in feature


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


def test_design_spec_records_implemented_runtime_and_pending_evidence() -> None:
    """The design status must not present shipped core as an active daemon path."""
    spec = SPEC.read_text(encoding="utf-8")
    normalized = " ".join(spec.split())

    assert (
        "**Status: CORE, ANALYSIS API, CLI, AND OPT-IN LIVE REVIEW IMPLEMENTED; "
        "DURABLE INTEGRATION PENDING**"
    ) in normalized
    assert "## 0. Implementation status (2026-10-06)" in spec
    assert "DecisionLogAnalyzer" in spec
    assert "DecisionResolver" in spec
    assert "DecisionCodificationAdapter" in spec
    assert "explicit injection" in spec
    assert (
        "durable multiworker configuration, recorder/outcome feedback, and deployed "
        "live-traffic proof remain pending"
    ) in normalized


def test_docs_pin_the_opt_in_live_review_contract() -> None:
    """The guide, spec, and deck must agree on the live REVIEW fallback contract."""
    feature = FEATURE.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    slide = _decision_log_slide()

    for document in (feature, spec):
        normalized = " ".join(document.replace("`", "").split())
        for marker in (
            "DecisionKind.REVIEW",
            "exact REVIEW hit skips the reviewer",
            "abstention or adapter error invokes the reviewer exactly once off-loop",
            "approve -> complete",
            "request_changes -> needs_more_work",
            "reject -> failed",
            "stable idempotency",
            "stable correlation and side-effect IDs",
            "managed self-improvement refuses codified resolution",
            "content-free attribution",
            "opt-in and disabled by default",
        ):
            assert marker in normalized

    for marker in (
        "Live REVIEW",
        "exact hit: zero reviewer calls",
        "abstention/error: one off-loop reviewer call",
        "approve -> complete",
        "request_changes -> needs_more_work",
        "reject -> failed",
        "Stable idempotency",
        "stable correlation + side-effect IDs",
        "managed self-improvement refuses",
        "content-free attribution",
        "opt-in; disabled by default",
    ):
        assert marker in slide


def test_docs_pin_the_bounded_authenticated_analysis_api_and_cli() -> None:
    """The guide, spec, and deck must agree on the analysis-only API and CLI."""
    feature = FEATURE.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")
    slide = _decision_log_slide()

    for document in (feature, spec):
        normalized = " ".join(document.replace("`", "").split())
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
            "gludd decision-codification analyze",
            "--project",
            "1-256 repeated --run-id values",
            "SHA-256 training-recipe and dependency-lock digests",
            "timezone-aware --created-at and --expires-at",
            "positive lifetime of at most 366 days",
            "--maximum-use-count from 1-1,000,000",
            "--estimated-tokens-per-call from 0-10,000,000",
            "GLUDD_AUTH_PSK",
            "128 KiB response cap",
            "safe summaries only",
            "no lifecycle, key, artifact, or evidence surface",
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
        "gludd decision-codification analyze",
        "explicit project/run/digest/time/use bounds",
        "existing auth",
        "128 KiB response cap",
        "safe summaries only",
        "no lifecycle/key/artifact/evidence surface",
    ):
        assert marker in slide

    for stale_marker in (
        "CLI, automatic live-flow invocation, and durable configuration remain pending",
        "automatic live-flow integration pending",
    ):
        assert stale_marker not in feature
        assert stale_marker not in spec
    assert "CLI, live-flow, durable config pending" not in slide
    assert "Bounded analysis API ready; live flow pending" not in slide


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


def test_reveal_deck_preserves_release_panel_slides_and_tokens() -> None:
    """This slide edit must not disturb the established release deck structure."""
    deck = DECK.read_text(encoding="utf-8")

    assert deck.count("<section") == 52
    assert deck.count('data-contract="decision-log-codification-v1"') == 1
    assert "v0.1.2 completed backlog" in deck
    assert "5 formally closed" in deck
    assert "S83.128 &mdash; invoking-worktree-safe virtual-environment reclamation" in deck
    assert "<li><strong>S83.169" not in deck
    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
