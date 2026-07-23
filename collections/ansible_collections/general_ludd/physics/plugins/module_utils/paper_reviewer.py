"""Research paper review helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReviewConfig:
    def __init__(self, paper_title: str = "", paper_text: str = "", review_depth: str = "standard") -> None:
        self.paper_title = paper_title
        self.paper_text = paper_text
        self.review_depth = review_depth


def extract_sections(text: str) -> dict[str, str]:
    known = {"abstract", "introduction", "methods", "results", "discussion", "conclusion"}
    sections: dict[str, str] = {}
    current = "body"
    chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower in known:
            if chunks:
                sections[current] = "\n".join(chunks).strip()
            current = lower
            chunks = []
        elif line:
            chunks.append(line)
    if chunks:
        sections[current] = "\n".join(chunks).strip()
    return sections or {"body": text}


def score_rigor(sections: dict[str, str], text: str) -> dict[str, float]:
    has_methods = 1.0 if "methods" in sections else 0.5
    has_results = 1.0 if "results" in sections or "result" in text.lower() else 0.5
    evidence_words = ("accuracy", "converges", "demonstrate", "show")
    evidence = 1.0 if any(word in text.lower() for word in evidence_words) else 0.4
    overall = round((has_methods + has_results + evidence) / 3.0, 3)
    return {
        "methods": has_methods,
        "results": has_results,
        "evidence": evidence,
        "overall": overall,
    }


def extract_findings(text: str) -> list[str]:
    findings: list[str] = []
    for sentence in text.replace("\n", " ").split("."):
        clean = sentence.strip()
        if clean and any(word in clean.lower() for word in ("finding", "show", "demonstrate", "accuracy", "converges")):
            findings.append(clean)
    return findings


def count_equations(text: str) -> int:
    return text.count("=") + text.count("\\begin{equation}")


def write_review_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "paper_review_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
