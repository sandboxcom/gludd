"""Pin the S83.179 shadow evidence and S83.180 reconciliation deck story."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"
CONTRACT = "s83-179-180-shadow-reconciliation"


def _slide(content: str) -> str:
    """Return the compact S83.179/S83.180 feature-sync slide body."""
    opening = f'<section class="feature-sync-slide" data-contract="{CONTRACT}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_shadow_receipts_and_merge_queue_are_bounded_and_honest() -> None:
    """The deck must keep shadow evidence separate from gate and merge authority."""
    deck = DECK.read_text(encoding="utf-8")
    slide = _slide(deck)
    ordered_markers = (
        "Authenticate",
        "Import legacy failures",
        "Keep execution mandatory",
        "Inventory",
        "Preflight + checkpoint",
        "Replay only",
    )

    assert [slide.index(marker) for marker in ordered_markers] == sorted(
        slide.index(marker) for marker in ordered_markers
    )
    for marker in (
        "S83.179 + S83.180",
        "SHADOW / READ-ONLY &middot; RELEASE PROOF PENDING",
        "HMAC-SHA256",
        "action, manifest, kind, signer, issue time, and expiry",
        "verified",
        "future-eligible",
        "skip_authorized=false",
        "skips=0",
        "NON-REUSABLE",
        "64 MiB / 250,000 lines / 64 failures / 32 KiB output",
        "never enters the pass-receipt store",
        "one worker / 16-file execution",
        "terminal gate remains authoritative",
        "terminal ref/head snapshot",
        "unique heads",
        "ancestor and patch-equivalent heads stay collapsed",
        "256-head ceiling",
        "git merge-tree",
        "predicted-clean",
        "never mergeable",
        "version-2 receipt",
        "target + source tips + collapsed groups + cursor",
        "ordered reachable prefix",
        "never performs a merge",
        "No test is skipped",
        "no ref, index, worktree, or push is mutated",
        "Full gate, hosted provenance, merge, push, and release proof remain pending",
    ):
        assert marker in slide

    assert "badge-green" not in slide
    assert slide.count("<li>") == 6
    assert len(slide) < 5_000
    assert deck.count("<section") == 63


def test_shadow_reconciliation_builds_exact_repository_source_ranges() -> None:
    """Implementation claims must open immutable GitHub and local viewer ranges."""
    authored = DECK.read_text(encoding="utf-8")
    sha = "c" * 40
    linked, citations = build_deck.link_source_citations(authored, sha)
    slide = _slide(linked)
    expected_sources = (
        ("scripts/ci_receipt_auth.py", "225-321"),
        ("scripts/ci_legacy_gate_log_import.py", "102-338"),
        ("scripts/branch_reconciliation_inventory.py", "1406-1565"),
        ("scripts/branch_reconciliation_inventory.py", "1891-2034"),
    )

    for path, lines in expected_sources:
        start, end = lines.split("-", 1)
        assert path in citations
        assert f'data-source-path="{path}"' in slide
        assert f'data-source-lines="{lines}"' in slide
        assert (
            f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}"
            f"#L{start}-L{end}"
        ) in slide

    assert slide.count('class="source-link"') == 4


def test_shadow_reconciliation_retains_practitioner_findings() -> None:
    """Long-lived operator reports must remain attached to the mitigations."""
    slide = _slide(DECK.read_text(encoding="utf-8"))

    for source in (
        "https://github.com/sigstore/cosign/issues/1273",
        "https://github.com/pytest-dev/pytest/issues/6399",
        "https://github.com/orgs/community/discussions/46757",
        "https://gitlab.com/gitlab-org/gitlab/-/issues/229156",
    ):
        assert source in slide
