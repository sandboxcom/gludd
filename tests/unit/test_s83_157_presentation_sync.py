"""Pin the S83.157 live-proof decision across its docs and reveal.js deck."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
FEATURE = ROOT / "docs/features/SELF_IMPROVEMENT_MIXED_MODELS.md"
RESEARCH = (
    ROOT / "docs/research/AZURE_TERRAFORM_EVENT_ELASTICITY_E2E_EVIDENCE.md"
)


def _decision_section() -> str:
    """Return only the dated S83.157 decision from the canonical feature doc."""
    content = FEATURE.read_text()
    return content.split(
        "#### Next bounded alternate-region T4 proof (decision, 2026-10-05)", 1
    )[1].split("\nBootstrap now records", 1)[0]


def _deck_slide() -> str:
    """Return the one canonical slide that mirrors the dated decision."""
    content = DECK.read_text()
    return content.split('<section data-contract="s83-157-next-live-proof">', 1)[
        1
    ].split("</section>", 1)[0]


def test_live_proof_decision_is_pinned_in_source_docs() -> None:
    """The feature and research docs retain the bounded decision evidence."""
    decision = _decision_section()
    research = RESEARCH.read_text()

    for marker in (
        "`westus`",
        "`westus2`",
        "`canadacentral`",
        "`minReplicas=1`",
        "`maxReplicas=1`",
        "$5",
        "60-minute",
        "GpuUtilizationPercentage",
        "destroy-plus-absence",
        "model quality",
    ):
        assert marker in decision
    for source in ("issues/1511", "issues/1705", "issues/1682", "issues/1763"):
        assert source in decision
    assert "5572527" in decision
    assert "[Azure Container Apps quotas][azure-container-quotas]" in research
    assert "[Azure Container Apps revisions][azure-container-revisions]" in research
    assert "[Azure Container Apps health probes][azure-container-health-probes]" in research


def test_reveal_deck_mirrors_ordered_s83_157_safety_boundary() -> None:
    """The published deck cannot silently omit or reorder the live-proof plan."""
    slide = _deck_slide()

    for marker in (
        "S83.157",
        "Consumption-GPU-NC8as-T4",
        "minReplicas=1",
        "maxReplicas=1",
        "$5 / 60 min",
        "exact revision",
        "startup + readiness probes",
        "system events",
        "CUDA attestation",
        "GpuUtilizationPercentage",
        "destroy + verified absence",
        "two cleaned placement failures",
        "Never score placement as model quality",
    ):
        assert marker in slide

    westus = slide.index("<code>westus</code>")
    westus2 = slide.index("<code>westus2</code>")
    canadacentral = slide.index("<code>canadacentral</code>")
    assert westus < westus2 < canadacentral
    assert "docs/features/SELF_IMPROVEMENT_MIXED_MODELS.md" in slide
    assert "docs/research/AZURE_TERRAFORM_EVENT_ELASTICITY_E2E_EVIDENCE.md" in slide


def test_reveal_deck_retains_every_live_build_token() -> None:
    """Presentation checks must resolve live provenance without stale warnings."""
    deck = DECK.read_text()

    for token in (
        "{{VERSION}}",
        "{{TEST_COUNT}}",
        "{{ROLE_COUNT}}",
        "{{GIT_SHA}}",
        "{{GENERATED_AT}}",
    ):
        assert token in deck
