"""Pin the October feature-sync presentation contracts and source navigation."""

from __future__ import annotations

from pathlib import Path

from scripts import build_deck

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs/presentation/deck/index.html"

FEATURE_CONTRACTS = (
    "s83-109-local-game-boundary",
    "s83-118-122-branch-reconciliation",
    "s91-3-enforcement-executable-modes",
)


def _slide(content: str, contract: str) -> str:
    """Return one uniquely identified Reveal slide body."""
    opening = f'<section class="feature-sync-slide" data-contract="{contract}">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def _decision_slide(content: str) -> str:
    """Return the stable decision-codification slide body."""
    opening = '<section data-contract="decision-log-codification-v1">'
    assert content.count(opening) == 1
    return content.split(opening, 1)[1].split("</section>", 1)[0]


def test_feature_sync_slides_pin_scope_evidence_and_honest_pending_work() -> None:
    """Implemented slices and unfinished release proof must both remain visible."""
    content = DECK.read_text(encoding="utf-8")
    expected_markers = {
        "s83-109-local-game-boundary": (
            "S83.109",
            "missing PID file",
            "terminal cleanup",
            "pillow&gt;=12.3.0",
            "254/254",
            "95%",
            "120,314",
            "500 files",
            "exact-head full-gate and promotion proof remain pending",
            "Ansible #44318",
            "Pillow #515",
        ),
        "s83-118-122-branch-reconciliation": (
            "S83.118 / .119 / .121 / .122",
            "ancestor",
            "patch-equivalent",
            "unique/current",
            "strict-greater cursor",
            "10,000-ref ceiling",
            "terminal snapshot",
            "current-only",
            "30/30",
            "exact-head global-gate and promotion proof remain pending",
            "GitHub CLI #8536",
        ),
        "s91-3-enforcement-executable-modes": (
            "S91.3",
            "S83.110",
            "read-only AST audit",
            "1,055",
            "S83.111",
            "deny-first",
            "113/113",
            "Git index",
            "100755",
            "47 runtime surfaces",
            "151 hook-runtime checks",
            "108/108 manifest",
            "source bytes unchanged",
            "exact-head full-gate and promotion proof remain pending",
            "OpenCode #7006",
        ),
    }

    for contract, markers in expected_markers.items():
        slide = _slide(content, contract)
        for marker in markers:
            assert marker in slide
        assert slide.count("<li>") <= 8
        assert len(slide) < 5_000

    branch_slide = _slide(content, "s83-118-122-branch-reconciliation")
    assert branch_slide.count('class="mermaid"') == 1
    assert branch_slide.count("-->") <= 4


def test_decision_slide_matches_the_automatic_durable_review_boundary() -> None:
    """The stable S83.169 slide must not retain the obsolete pending claims."""
    slide = _decision_slide(DECK.read_text(encoding="utf-8"))

    for marker in (
        "S83.169",
        "automatic durable live REVIEW",
        "same-host SQLite WAL",
        "BEGIN IMMEDIATE",
        "terminal application feedback",
        "Automatic signed replay capture",
        "same-host observability are implemented",
        "deployed live-traffic proof remain pending",
        "multi-host state is not claimed",
    ):
        assert marker in slide

    assert "durable multiworker configuration" not in slide
    assert "live REVIEW and CLI tests" not in slide


def test_feature_sync_citations_build_immutable_file_line_source_links() -> None:
    """Each synced feature must open an exact local Ace range or GitHub anchor."""
    authored = DECK.read_text(encoding="utf-8")
    sha = "d" * 40
    linked, citations = build_deck.link_source_citations(authored, sha)
    expected_sources = {
        "s83-109-local-game-boundary": (
            "playbooks/local_model_stop.yml",
            "17-101",
        ),
        "s83-118-122-branch-reconciliation": (
            "scripts/branch_reconciliation_inventory.py",
            "590-749",
        ),
        "s91-3-enforcement-executable-modes": (
            "tests/unit/test_enforcement_executable_modes.py",
            "12-56",
        ),
    }

    for contract, (path, lines) in expected_sources.items():
        slide = _slide(linked, contract)
        assert path in citations
        assert 'class="source-link"' in slide
        assert f'data-source-path="{path}"' in slide
        assert f'data-source-lines="{lines}"' in slide
        assert f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}" in slide
        start, end = lines.split("-", 1)
        assert f"#L{start}-L{end}" in slide

    guardrail_slide = _slide(linked, "s91-3-enforcement-executable-modes")
    for path, lines in (
        (
            "docs/features/NAG_FREE_ENFORCEMENT_SKIP_SMELL_CONTRACT.md",
            "58-74",
        ),
        ("docs/features/EXTERNAL_DIRECTORY_PERMISSION_CONTRACT.md", "37-68"),
    ):
        assert path in citations
        assert f'data-source-path="{path}"' in guardrail_slide
        assert f'data-source-lines="{lines}"' in guardrail_slide
        assert f"https://github.com/sandboxcom/gludd/blob/{sha}/{path}" in guardrail_slide
        start, end = lines.split("-", 1)
        assert f"#L{start}-L{end}" in guardrail_slide

    decision = _decision_slide(linked)
    assert 'data-source-path="src/general_ludd/event_loop/review_orchestration.py"' in decision
    assert 'data-source-lines="222-285"' in decision
