"""Evidence-based response checker for guarding against unsupported factual claims.

KEEP LIST (V3.8): Domain-specific regex patterns for detecting unsupported claims
in agent responses. Not replaceable by any OSS library — the regexes encode
project-specific evidence rules (commit hashes, test paths, make targets).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit

_PATH_PATTERN = re.compile(
    r"https?://[^\s<>()]+"
    r"|(?<![a-zA-Z0-9._/\\-])"
    r"(?:[a-zA-Z0-9._-]+/)*"
    r"[a-zA-Z0-9._-]+\.[a-zA-Z0-9]+(?::\d+)?"
)
_LOCAL_SOURCE_PATTERN = re.compile(
    r"(?:[a-zA-Z0-9._-]+/)*"
    r"[a-zA-Z0-9._-]+\.[a-zA-Z0-9]+"
    r"(?::[1-9]\d*)?"
)

_CLAIM_PATTERNS = [
    re.compile(r"\b\w[\w ]*\s+(?:is|are|was|were|has|have|had)\s+\S", re.IGNORECASE),
    re.compile(r"\b\w[\w ]*\s+(?:uses?|contains?|returns?|does?|supports?)\s+\S", re.IGNORECASE),
    re.compile(r"\d+\s*%"),
    re.compile(r"\b(?:total|count|number)\s+(?:is|of|equals?)\s+\d+", re.IGNORECASE),
    re.compile(
        r"\b(?:fixed|implemented|changed|updated|defined|documented|located)"
        r"\s+(?:in|at)\s+\S",
        re.IGNORECASE,
    ),
]

_EXEMPT_PATTERNS = [
    re.compile(r"\?"),
    re.compile(r"^(?:I think|maybe|perhaps|possibly|in my opinion|IMO)\b", re.IGNORECASE),
    re.compile(r"^(?:OK|ok|okay|sure|yes|no|right|got it|understood)\b", re.IGNORECASE),
]

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+")
_TOKEN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "be",
        "been",
        "by",
        "does",
        "file",
        "for",
        "from",
        "had",
        "has",
        "have",
        "in",
        "is",
        "it",
        "line",
        "of",
        "on",
        "or",
        "per",
        "see",
        "src",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


@dataclass
class EvidenceResult:
    supported: bool
    claim: str
    sources: list[str]
    missing_sources: list[str] = field(default_factory=list)


class EvidenceChecker:
    def check_claim(self, claim: str, sources: list[str]) -> EvidenceResult:
        if not sources:
            return EvidenceResult(
                supported=False,
                claim=claim,
                sources=[],
                missing_sources=["no source provided"],
            )
        valid_sources = _valid_sources(sources)
        if valid_sources:
            return EvidenceResult(supported=True, claim=claim, sources=valid_sources)
        return EvidenceResult(
            supported=False,
            claim=claim,
            sources=sources,
            missing_sources=["no valid source provided"],
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
            inline_sources = _extract_sources(stripped)
            tool_sources = _matching_tool_sources(stripped, tool_outputs)
            all_sources = _deduplicate(inline_sources + tool_sources)
            results.append(self.check_claim(stripped, all_sources))
        return results


def _valid_sources(sources: list[str]) -> list[str]:
    return _deduplicate(source for source in sources if _is_valid_source(source))


def _is_valid_source(source: str) -> bool:
    """Validate an evidence reference without touching the filesystem."""
    candidate = source.strip().rstrip(".,;!?)]}")
    if candidate.startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        path_parts = parsed.path.split("/")
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and all(part not in {".", ".."} for part in path_parts)
        )

    if (
        not _LOCAL_SOURCE_PATTERN.fullmatch(candidate)
        or candidate.startswith(("/", "~"))
        or "\\" in candidate
    ):
        return False
    path_text = candidate.rsplit(":", maxsplit=1)[0]
    path_parts = path_text.split("/")
    path = PurePosixPath(path_text)
    return bool(
        not path.is_absolute()
        and path.suffix
        and all(part not in {"", ".", ".."} for part in path_parts)
    )


def _extract_sources(text: str) -> list[str]:
    return _valid_sources([match.group(0) for match in _PATH_PATTERN.finditer(text)])


def _matching_tool_sources(claim: str, tool_outputs: list[str]) -> list[str]:
    matching: list[str] = []
    claim_tokens = _meaningful_tokens(claim)
    for tool_output in tool_outputs:
        for line in tool_output.splitlines() or [tool_output]:
            for source in _extract_sources(line):
                context = line.replace(source, " ")
                evidence_tokens = _source_tokens(source) | _meaningful_tokens(context)
                if claim_tokens & evidence_tokens:
                    matching.append(source)
    return _deduplicate(matching)


def _source_tokens(source: str) -> set[str]:
    candidate = source.strip().rstrip(".,;!?)]}")
    if candidate.startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        source_text = f"{parsed.hostname or ''} {parsed.path}"
    else:
        source_text = candidate.rsplit(":", maxsplit=1)[0]
    return _meaningful_tokens(source_text)


def _meaningful_tokens(text: str) -> set[str]:
    return {
        normalized
        for raw_token in _TOKEN_PATTERN.findall(text.lower())
        if (normalized := _normalize_token(raw_token)) not in _TOKEN_STOPWORDS
    }


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith(("sses", "ches", "shes", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _deduplicate(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p for p in parts if p.strip()]


def _is_exempt(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _EXEMPT_PATTERNS)


def _is_factual_claim(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _CLAIM_PATTERNS)
