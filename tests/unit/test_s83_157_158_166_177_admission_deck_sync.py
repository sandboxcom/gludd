"""Pin the integrated S83.157/.158/.166/.177 admission story in the deck."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT = "s83-157-158-166-177-admission-chain"


def _slide(content: str) -> str:
    """Return the one compact admission-chain slide body."""
    opening = f'<section class="feature-sync-slide" data-contract="{CONTRACT}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_admission_chain_is_ordered_and_does_not_claim_release_completion() -> None:
    """The deck must distinguish integrated controls from unreached release proof."""
    deck = DECK.read_text(encoding="utf-8")
    slide = _slide(deck)
    ordered_markers = (
        "S83.177",
        "S83.158",
        "S83.157",
        "S83.166",
    )

    assert [slide.index(marker) for marker in ordered_markers] == sorted(
        slide.index(marker) for marker in ordered_markers
    )
    for marker in (
        "clean committed feature branch",
        "stops on the first failed deterministic check",
        "claim transaction committed and active session released",
        "claim_transaction_open",
        "zero provider or dispatch calls",
        "protected GitHub Environment",
        "before OIDC or paid compute",
        "RELEASE_ALLOW_INCOMPLETE_TASKS=0",
        "RELEASE_ALLOW_INVALID_RECEIPT=0",
        "exact-head full gate, hosted replay, and terminal publication remain pending",
        "No release-completion claim is made",
    ):
        assert marker in slide

    assert "badge-green" not in slide
    assert slide.count("<li>") == 4
    assert len(slide) < 5_000
    assert deck.count("<section") == 61


def test_admission_chain_builds_four_exact_repository_source_ranges() -> None:
    """Every stage must expose an immutable GitHub anchor and local Ace range."""
    authored = DECK.read_text(encoding="utf-8")
    sha = "e" * 40
    linked, citations = build_deck.link_source_citations(authored, sha)
    slide = _slide(linked)
    expected_sources = {
        "docs/features/INTEGRATION_ADMISSION.md": "5-42",
        "docs/features/DURABLE_CLAIM_COMPUTE_FENCE.md": "21-33",
        "docs/azure-gha-oidc-live-proof.md": "46-79",
        "docs/features/RELEASE_PREDECESSOR_ADMISSION.md": "11-28",
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


def test_admission_chain_retains_long_lived_practitioner_findings() -> None:
    """The concise slide must retain the operator evidence behind each boundary."""
    slide = _slide(DECK.read_text(encoding="utf-8"))

    for source in (
        "https://github.com/orgs/community/discussions/43988",
        "https://github.com/sidekiq/sidekiq/issues/5239",
        "https://github.com/orgs/community/discussions/12241",
        "https://github.com/actions/runner/issues/789",
    ):
        assert source in slide
