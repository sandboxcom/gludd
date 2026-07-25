"""Memory retrieval — relevance-scored querying of past episodic memories.

Supports text-based queries that return scored results matching by keyword
overlap, task type, outcome patterns, and recency boost.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScoredMemory:
    episode: Any  # Episode
    score: float
    match_reasons: list[str] = field(default_factory=list)


class MemoryRetriever:
    """Queries episodic memory with relevance scoring."""

    def __init__(self, memory_repo: Any) -> None:
        self._repo = memory_repo
        self._episodic_ns = "episodic"

    async def query(
        self,
        agent_id: str,
        query_text: str,
        task_type: str | None = None,
        project_id: str | None = None,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[ScoredMemory]:
        from general_ludd.memory.episodic import EpisodicMemoryRecorder

        recorder = EpisodicMemoryRecorder(self._repo)
        episodes = await recorder.list_episodes(agent_id, project_id=project_id, limit=500)
        results: list[ScoredMemory] = []

        query_lower = query_text.lower()
        query_terms = _tokenize(query_lower)

        for ep in episodes:
            if task_type and ep.task_type != task_type:
                continue

            score, reasons = self._score_episode(ep, query_terms, query_lower)
            score = self._boost_recency(ep, score)

            if score >= min_score:
                results.append(ScoredMemory(episode=ep, score=score, match_reasons=reasons))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _score_episode(
        self, ep: Any, query_terms: list[str], query_lower: str
    ) -> tuple[float, list[str]]:
        import json

        score = 0.0
        reasons: list[str] = []

        ep_text = (
            f"{ep.task_type} {ep.work_type} {ep.priority} "
            f"{ep.outcome} {ep.takeaway} {ep.error_message} "
            f"{json.dumps(ep.context) if ep.context else ''}"
        ).lower()
        ep_terms = _tokenize(ep_text)

        # Term overlap score (Jaccard-like)
        if query_terms:
            common = set(query_terms) & set(ep_terms)
            union = set(query_terms) | set(ep_terms)
            if union:
                overlap = len(common) / len(union)
                score += overlap * 0.4
                if overlap > 0.2:
                    reasons.append(f"term_overlap={overlap:.2f}")

        # Exact phrase match in takeaway
        if ep.takeaway and query_lower in ep.takeaway.lower():
            score += 0.3
            reasons.append("takeaway_match")

        # Task type exact match
        if ep.task_type.lower() in query_lower or query_lower in ep.task_type.lower():
            score += 0.15
            reasons.append("task_type_match")

        # Outcome match (look for "success" or "fail" in query)
        if "fail" in query_lower and ep.outcome == "failure":
            score += 0.15
            reasons.append("failure_query_match")
        if "success" in query_lower and ep.outcome == "success":
            score += 0.1
            reasons.append("success_query_match")

        # Error message match
        if ep.error_message and _any_overlap(query_terms, _tokenize(ep.error_message.lower())):
            score += 0.15
            reasons.append("error_pattern_match")

        # Takeaway keyword match
        if ep.takeaway and _any_overlap(query_terms, _tokenize(ep.takeaway.lower())):
            score += 0.15
            reasons.append("lesson_match")

        return min(score, 1.0), reasons

    def _boost_recency(self, ep: Any, score: float) -> float:
        if not ep.created_at:
            return score
        try:
            created = datetime.fromisoformat(ep.created_at)
            now = datetime.now(UTC)
            age_hours = max(0, (now - created).total_seconds() / 3600)
            if age_hours < 1:
                return min(score * 1.5, 1.0)
            if age_hours < 24:
                return min(score * 1.2, 1.0)
            if age_hours > 168:  # > 1 week
                return score * 0.9
        except (ValueError, TypeError):
            pass
        return score


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "and", "but", "or", "not", "no", "nor", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more", "most",
        "other", "some", "such", "only", "own", "same", "than", "too", "very",
        "just", "it", "its", "that", "this", "these", "those",
    }
    return [w for w in words if w not in stop and len(w) > 1]


def _any_overlap(terms_a: list[str], terms_b: list[str]) -> bool:
    return bool(set(terms_a) & set(terms_b))


def score_memory(memory: dict[str, Any], current_time: datetime | None = None) -> float:
    """Calculate importance/freshness score for a memory entry.

    Score = importance_weight * (1.0 / (1 + age_days)) * access_count_bonus
    """
    import_weight = float(memory.get("importance_weight", 1.0))
    access_count = int(memory.get("access_count", 0))
    created_at_str = memory.get("created_at", "")

    age_days = 0.0
    if created_at_str:
        try:
            created = datetime.fromisoformat(created_at_str)
            now = current_time or datetime.now(UTC)
            age_seconds = max(0.0, (now - created).total_seconds())
            age_days = age_seconds / 86400.0
        except (ValueError, TypeError):
            pass

    recency_factor = 1.0 / (1.0 + age_days)
    access_bonus = min(1.0 + access_count * 0.1, 2.0)
    score = import_weight * recency_factor * access_bonus
    return round(score, 4)


def hybrid_search(
    query: str,
    memories: list[dict[str, Any]],
    top_k: int = 10,
    bm25_weight: float = 0.3,
    semantic_weight: float = 0.7,
) -> list[tuple[dict[str, Any], float]]:
    """Combine keyword (BM25) and semantic similarity for ranking.

    BM25 scoring uses term frequency with inverse document frequency.
    Semantic similarity uses token overlap with Jaccard distance.
    Results are combined with the given weight ratio and sorted by score.
    """
    if not memories:
        return []

    query_terms = _tokenize(query.lower())
    if not query_terms:
        return [(m, 0.0) for m in memories[:top_k]]

    doc_texts: list[str] = []
    doc_terms: list[set[str]] = []
    for m in memories:
        text = " ".join(str(v) for v in m.values() if isinstance(v, (str, int, float)))
        doc_texts.append(text.lower())
        doc_terms.append(set(_tokenize(text.lower())))

    N = len(memories)
    avgdl = sum(len(t) for t in doc_terms) / N if N > 0 else 1.0
    k1 = 1.5
    b = 0.75

    df: dict[str, int] = {}
    for dt in doc_terms:
        for t in dt:
            df[t] = df.get(t, 0) + 1

    query_set = set(query_terms)

    bm25_scores: list[float] = []
    for i in range(N):
        score = 0.0
        dl = len(doc_terms[i])
        for term in query_terms:
            if df.get(term, 0) == 0:
                continue
            idf = max(0, int(((N - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5)) * 10))
            tf = sum(1 for t in doc_terms[i] if t == term)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (dl / avgdl))
            score += idf * (numerator / denominator) if denominator > 0 else 0.0
        bm25_scores.append(score)

    semantic_scores: list[float] = []
    for i in range(N):
        dt = doc_terms[i]
        if dt:
            overlap = len(query_set & dt) / len(query_set | dt) if query_set else 0.0
        else:
            overlap = 0.0
        semantic_scores.append(overlap)

    max_bm25 = max(bm25_scores) if bm25_scores else 1.0
    max_semantic = max(semantic_scores) if semantic_scores else 1.0
    norm_bm25 = [s / max_bm25 if max_bm25 > 0 else 0.0 for s in bm25_scores]
    norm_semantic = [s / max_semantic if max_semantic > 0 else 0.0 for s in semantic_scores]

    combined: list[tuple[dict[str, Any], float]] = []
    for i in range(N):
        score = bm25_weight * norm_bm25[i] + semantic_weight * norm_semantic[i]
        combined.append((memories[i], round(score, 4)))

    combined.sort(key=lambda x: x[1], reverse=True)
    return combined[:top_k]
