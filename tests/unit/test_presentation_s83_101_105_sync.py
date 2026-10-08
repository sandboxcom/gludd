"""Pin candidate evidence for the S83.101-S83.105 presentation slice."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"

CONTRACTS = {
    "s83-101-104-concurrency-authority": (
        "S83.101 / S83.104",
        "INTEGRATED CANDIDATES",
        "owner + fencing token + persisted expiry",
        "PID + thread ownership",
        "110/110",
        "169/169",
        "exact-head gate, CI, and promotion evidence remain pending",
    ),
    "s83-103-canonical-worktree-paths": (
        "S83.103",
        "INTEGRATED CANDIDATE",
        "raw .. traversal",
        "symlink escape",
        "421/421 Git repository family",
        "formally complete",
    ),
    "s83-108-105-boundary-evidence": (
        "S83.108 / S83.106 / S83.105",
        "S83.105 CANDIDATE ONLY",
        "identity=rejected",
        "NUL worktree registry",
        "100 MB / 90%",
        "67/67",
        "86%",
        "ProjectType | str",
        "170/170",
        "96.95%",
        "No v0.1.2 completion claim",
    ),
}

SOURCE_RANGES = {
    "s83-101-104-concurrency-authority": (
        ("src/general_ludd/db/azure_cost_repository.py", "412-438"),
        ("src/general_ludd/git_automation/locking.py", "98-113"),
    ),
    "s83-103-canonical-worktree-paths": (
        ("src/general_ludd/git_automation/repo.py", "1119-1189"),
    ),
    "s83-108-105-boundary-evidence": (
        ("scripts/check_worktree_health.py", "39-70"),
        ("scripts/check_disk_usage.py", "72-118"),
        ("src/general_ludd/cloud/project_types.py", "899-914"),
    ),
}


def _slide(content: str, contract: str) -> str:
    """Return one compact, uniquely identified candidate slide."""
    opening = f'<section class="feature-sync-slide" data-contract="{contract}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_candidate_slides_keep_scope_evidence_and_release_status_honest() -> None:
    """Candidate evidence must never be presented as formal release closure."""
    content = DECK.read_text(encoding="utf-8")

    for contract, markers in CONTRACTS.items():
        slide = _slide(content, contract)
        for marker in markers:
            assert marker in slide
        assert slide.count("<li>") <= 6
        assert len(slide) < 5_000

    for contract in (
        "s83-101-104-concurrency-authority",
        "s83-108-105-boundary-evidence",
    ):
        assert _slide(content, contract).count('class="mermaid"') == 1


def test_candidate_slides_build_immutable_github_and_local_ace_ranges() -> None:
    """Every candidate slide must link exact committed file/line evidence."""
    sha = "e" * 40
    linked, citations = build_deck.link_source_citations(
        DECK.read_text(encoding="utf-8"),
        sha,
    )

    for contract, sources in SOURCE_RANGES.items():
        slide = _slide(linked, contract)
        for path, lines in sources:
            start, end = lines.split("-", 1)
            assert path in citations
            assert f'data-source-path="{path}"' in slide
            assert f'data-source-lines="{lines}"' in slide
            assert f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}" in slide
            assert f"#L{start}-L{end}" in slide
