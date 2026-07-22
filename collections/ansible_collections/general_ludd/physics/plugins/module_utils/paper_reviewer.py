"""Research paper review role helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


_SECTION_NAMES = ["abstract", "introduction", "methods", "results", "discussion", "conclusion"]


@dataclass(frozen=True)
class ReviewConfig:
    paper_title: str = ""
    paper_text: str = ""
    review_depth: str = "standard"


def extract_sections(paper_text: str) -> dict[str, str]:
    lines = paper_text.splitlines()
    sections: dict[str, list[str]] = {}
    current = "body"
    for line in lines:
        normalized = line.strip().lower()
        if normalized in _SECTION_NAMES:
            current = normalized
            sections.setdefault(current, [])
            continue
        if line.strip():
            sections.setdefault(current, []).append(line.strip())
    return {name: "\n".join(body) for name, body in sections.items()}


def count_equations(paper_text: str) -> int:
    bs = chr(92)
    return (
        paper_text.count("=")
        + paper_text.count(f"{bs}begin{{equation}}")
        + paper_text.count(chr(36))
    )


def score_rigor(sections: dict[str, str], paper_text: str) -> dict[str, float]:
    has_methods = 1.0 if sections.get("methods") else 0.0
    has_results = 1.0 if sections.get("results") else 0.0
    has_discussion = 1.0 if sections.get("discussion") else 0.0
    equation_score = min(1.0, count_equations(paper_text) / 3.0)
    overall = round((has_methods + has_results + has_discussion + equation_score) / 4.0, 3)
    return {
        "methods": has_methods,
        "results": has_results,
        "discussion": has_discussion,
        "equations": equation_score,
        "overall_rigor": overall,
    }


def extract_findings(paper_text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", paper_text.strip()):
        lower = sentence.lower()
        if any(token in lower for token in ("result", "accuracy", "improve", "significant")):
            findings.append({"type": "claim", "text": sentence.strip()})
    return findings


def write_review_result(result: dict[str, object], output_dir: str) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "review_result.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return out


def review_paper(config: ReviewConfig) -> dict[str, object]:
    sections = extract_sections(config.paper_text)
    rigor = score_rigor(sections, config.paper_text)
    findings = extract_findings(config.paper_text)
    return {
        "config": asdict(config),
        "sections": list(sections),
        "rigor_scores": rigor,
        "findings_count": len(findings),
    }
