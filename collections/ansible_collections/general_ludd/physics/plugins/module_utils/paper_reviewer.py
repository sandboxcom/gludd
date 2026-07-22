"""Compatibility helpers for research-paper review CLI workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReviewConfig:
    paper_title: str
    paper_text: str
    review_depth: str


def extract_sections(text: str) -> dict[str, str]:
    headings = {"abstract", "introduction", "methods", "results", "discussion", "conclusion"}
    sections: dict[str, str] = {}
    current = "body"
    for line in text.splitlines():
        key = line.strip().lower()
        if key in headings:
            current = key
            sections.setdefault(current, "")
        else:
            sections[current] = (sections.get(current, "") + "\n" + line).strip()
    return sections


def count_equations(text: str) -> int:
    return len(re.findall(r"\$[^$]+\$|\\begin\{equation\}", text))


def score_rigor(sections: dict[str, str], text: str) -> dict[str, float]:
    method_score = 1.0 if "methods" in sections else 0.4
    result_score = 1.0 if "results" in sections else 0.4
    evidence_score = min(1.0, (len(re.findall(r"\d+(?:\.\d+)?%?", text)) + count_equations(text)) / 3.0)
    overall = round((method_score + result_score + evidence_score) / 3.0, 3)
    return {
        "methods": method_score,
        "results": result_score,
        "evidence": evidence_score,
        "overall_rigor": overall,
    }


def extract_findings(text: str) -> list[str]:
    findings: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if re.search(r"\b(finding|show|demonstrate|result|converge|accuracy)\b", sentence, re.I):
            findings.append(sentence)
    return findings


def write_review_result(result: dict[str, Any], output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "review_result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out
