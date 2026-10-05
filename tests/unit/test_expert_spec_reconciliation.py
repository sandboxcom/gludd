"""Structural contract for the reconciled expert-system specifications."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]

IMPLEMENTATION_SPECS = (
    ROOT / "docs/specs/FEATURE_AI_ML_EXPERT.md",
    ROOT / "docs/specs/FEATURE_CHEMISTRY_EXPERT.md",
    ROOT / "docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md",
    ROOT / "docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md",
)

RESEARCH_DOCS = (
    ROOT / "docs/research/ML_AI_EXPERT_SYSTEM_RESEARCH_2026-07-28.md",
    ROOT / "docs/research/EXPERT_EXPANSION_RESEARCH_2026-07-29.md",
    ROOT / "docs/research/EXPERT_SYSTEM_INTEROPERABILITY_RESEARCH_2026-07-29.md",
)

RESEARCH_MARKERS = {
    RESEARCH_DOCS[0]: (
        "## 12. Continual learning and safe self-improvement",
        "## 16. Long-lived practitioner and user issue evidence",
        "## 20. Research refresh and provenance protocol",
    ),
    RESEARCH_DOCS[1]: (
        "## 3. Git mastery, release captain, and build/helper discovery",
        "## 9. Cross-expert operational profiles",
        "## 10. Implementation specifications and backlog",
    ),
    RESEARCH_DOCS[2]: (
        "## 3. Standards and protocol findings",
        "## 9. Safe discovery and self-improvement findings",
        "## 10. Practitioner and maintainer failure evidence",
    ),
}

PRACTITIONER_EVIDENCE = re.compile(
    r"https://(?:github\.com/[^\s)]+/issues/|stackoverflow\.com/questions/|"
    r"[^\s)]+/(?:forum|community)/)",
)


def _text(path: Path) -> str:
    """Read one tracked specification as UTF-8 text."""
    assert path.is_file(), f"missing reconciled expert document: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_all_non_superseded_expert_documents_are_tracked() -> None:
    """The final documents from all three source refs remain discoverable."""
    for path in (*IMPLEMENTATION_SPECS, *RESEARCH_DOCS):
        _text(path)


def test_research_documents_retain_their_durable_final_scopes() -> None:
    """Ported research keeps the distinct final scopes from each source ref."""
    for path, markers in RESEARCH_MARKERS.items():
        content = _text(path)
        assert all(marker in content for marker in markers), path.name


def test_implementation_specs_use_the_current_actionable_contract() -> None:
    """Each implementation spec carries the current delivery and safety schema."""
    required = (
        "ready-to-implement",
        "target compatibility",
        "acceptance criteria",
        "security",
        "resource",
        "zero-downtime",
        "rollback",
        "practitioner evidence",
    )
    for path in IMPLEMENTATION_SPECS:
        content = _text(path)
        lowered = content.lower()
        missing = [section for section in required if section not in lowered]
        assert not missing, f"{path.name} missing contract sections: {missing}"
        assert PRACTITIONER_EVIDENCE.search(content), (
            f"{path.name} needs a durable practitioner issue/forum citation"
        )


def test_overlapping_aiml_specs_declare_authoritative_boundaries() -> None:
    """The product feature and continual-research spec cross-link without drift."""
    feature = _text(IMPLEMENTATION_SPECS[0])
    continual = _text(IMPLEMENTATION_SPECS[2])
    assert "SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md" in feature
    assert "FEATURE_AI_ML_EXPERT.md" in continual
    assert "authoritative" in feature.lower()
    assert "authoritative" in continual.lower()


def test_research_indexes_link_every_reconciled_document() -> None:
    """Design and research indexes expose the newly reconciled contracts."""
    design_index = _text(ROOT / "docs/design/index.md")
    research_index = _text(ROOT / "docs/research/index.md")
    assert "SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md" in design_index
    for path in RESEARCH_DOCS:
        assert path.name in research_index
