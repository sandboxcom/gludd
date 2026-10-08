"""Pin the S83.169 capture, reuse, and observability story in the deck."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT = "s83-169-capture-reuse-observability"


def _slide(content: str) -> str:
    """Return the compact S83.169 feature-sync slide body."""
    opening = f'<section class="feature-sync-slide" data-contract="{CONTRACT}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_capture_reuse_observability_is_ordered_bounded_and_honest() -> None:
    """The slide must show the shipped path without claiming deployed proof."""
    deck = DECK.read_text(encoding="utf-8")
    slide = _slide(deck)
    ordered_markers = (
        "Capture",
        "Coordinate",
        "Prove reuse",
        "Observe",
        "Bound cardinality",
        "Attest",
    )

    assert [slide.index(marker) for marker in ordered_markers] == sorted(
        slide.index(marker) for marker in ordered_markers
    )
    for marker in (
        "S83.169",
        "IMPLEMENTED &middot; DEPLOYED PROOF PENDING",
        "DecisionOutcomeRecorder",
        "capture_identity",
        "no raw prompts, responses, rationale, or model parameters",
        "domain-separated HMAC",
        "signed decision + digest-linked outcome",
        "128 KiB",
        "60-second exact-owner lease",
        "32 signed bundles across four UTC days",
        "exact REVIEW reuse makes zero reviewer calls",
        "mismatch and expiry each fall back once",
        "exact-rule hits, typed abstentions, fallbacks, avoided agent/LLM calls",
        "fixed-cardinality rows",
        "BEGIN IMMEDIATE",
        "no per-request rows",
        "64 KiB",
        "SHA-256 digest + HMAC",
        "Opt-in and default-off",
        "capture/telemetry failure cannot change the selected decision",
        "deployed live-traffic proof remains pending",
        "agent/LLM fallback remains the rollback path",
    ):
        assert marker in slide

    assert "badge-green" not in slide
    assert slide.count("<li>") == 6
    assert len(slide) < 5_000
    assert deck.count("<section") == 62
    assert "Automatic signed replay capture pending" not in deck


def test_capture_reuse_observability_builds_exact_repository_source_ranges() -> None:
    """Every implementation claim must link to GitHub and the local code viewer."""
    authored = DECK.read_text(encoding="utf-8")
    sha = "d" * 40
    linked, citations = build_deck.link_source_citations(authored, sha)
    slide = _slide(linked)
    expected_sources = {
        "docs/features/DECISION_LOG_CODIFICATION.md": "95-158",
        "src/general_ludd/decision_codification/capture.py": "118-210",
        "src/general_ludd/decision_codification/observability.py": "69-213",
        "tests/integration/test_decision_codification_producer_reuse.py": "220-356",
    }

    for path, lines in expected_sources.items():
        start, end = lines.split("-", 1)
        assert path in citations
        assert f'data-source-path="{path}"' in slide
        assert f'data-source-lines="{lines}"' in slide
        assert (
            f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}"
            f"#L{start}-L{end}"
        ) in slide

    assert slide.count('class="source-link"') == 4


def test_capture_reuse_observability_retains_practitioner_findings() -> None:
    """Long-lived operator reports must remain attached to the mitigations."""
    slide = _slide(DECK.read_text(encoding="utf-8"))

    for source in (
        "https://github.com/prometheus/client_python/issues/568",
        "https://github.com/prometheus/client_python/issues/431",
        "https://github.com/kubernetes/kubernetes/issues/23731",
        "https://github.com/sqlalchemy/sqlalchemy/discussions/8554",
    ):
        assert source in slide
