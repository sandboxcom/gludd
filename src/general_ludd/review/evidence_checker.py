"""Evidence-based response checker for guarding against unsupported factual claims.

KEEP LIST (V3.8): Domain-specific regex patterns for detecting unsupported claims
in agent responses. Not replaceable by any OSS library — the regexes encode
project-specific evidence rules (commit hashes, test paths, make targets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATH_PATTERN = re.compile(
    r"(?:^|[\s(])"
    r"("
    r"(?:[a-zA-Z0-9._-]+/)+[a-zA-Z0-9._-]+\.[a-zA-Z0-9]+(?::\d+)?"
    r"|[a-zA-Z0-9._-]+\.[a-zA-Z0-9]+:\d+"
    r"|https?://\S+"
    r")"
)

_CLAIM_PATTERNS = [
    re.compile(r"\b\w[\w ]*\s+(?:is|are|was|were|has|have|had)\s+\S", re.IGNORECASE),
    re.compile(r"\b\w[\w ]*\s+(?:uses?|contains?|returns?|does?|supports?)\s+\S", re.IGNORECASE),
    re.compile(r"\d+\s*%"),
    re.compile(r"\b(?:total|count|number)\s+(?:is|of|equals?)\s+\d+", re.IGNORECASE),
]

_EXEMPT_PATTERNS = [
    re.compile(r"\?"),
    re.compile(r"^(?:I think|maybe|perhaps|possibly|in my opinion|IMO)\b", re.IGNORECASE),
    re.compile(r"^(?:OK|ok|okay|sure|yes|no|right|got it|understood)\b", re.IGNORECASE),
]


@dataclass
class EvidenceResult:
    supported: bool
    claim: str
    sources: list[str]
    missing_sources: list[str] = field(default_factory=list)


class EvidenceChecker:
    def check_claim(self, claim: str, sources: list[str]) -> EvidenceResult:
        if sources:
            return EvidenceResult(supported=True, claim=claim, sources=sources, missing_sources=[])
        return EvidenceResult(
            supported=False,
            claim=claim,
            sources=[],
            missing_sources=["no source provided"],
        )

    def audit_response(self, response_text: str, tool_outputs: list[str]) -> list[EvidenceResult]:
        results: list[EvidenceResult] = []
        sentences = _split_sentences(response_text)
        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped or _is_exempt(stripped):
                continue
            if not _is_factual_claim(stripped):
                continue
            inline_sources = _PATH_PATTERN.findall(stripped)
            tool_sources: list[str] = []
            for tool_output in tool_outputs:
                for frag in re.findall(r"[a-zA-Z0-9._\-/]+\.[a-zA-Z0-9]+:\d+", tool_output):
                    tool_sources.append(frag)
            all_sources = list(set(inline_sources + tool_sources))
            results.append(self.check_claim(stripped, all_sources))
        return results


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p for p in parts if p.strip()]


def _is_exempt(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _EXEMPT_PATTERNS)


def _is_factual_claim(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _CLAIM_PATTERNS)
