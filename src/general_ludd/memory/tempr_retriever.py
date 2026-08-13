"""TEMPR multi-strategy memory retrieval — native implementation.

TEMPR = Temporal + Embedding (Semantic) + Multi-keyword (BM25) + Pgraph (entity graph).
Four parallel retrieval strategies fused via Reciprocal Rank Fusion (RRF).

Replaces the Hindsight sidecar dependency with a native Python implementation
that integrates directly with gludd's memory subsystem.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────

_DEFAULT_STRATEGY_WEIGHTS = {
    "semantic": 0.25,
    "bm25": 0.25,
    "temporal": 0.25,
    "graph": 0.25,
}

_BM25_K1 = 1.5
_BM25_B = 0.75

_TEMPORAL_DECAY_LAMBDA = 0.01  # per day

_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "and", "but", "or", "not", "no", "nor", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "than", "too", "very",
    "just", "it", "its", "that", "this", "these", "those",
}


# ── helper functions ─────────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    keys = set(vec_a.keys()) | set(vec_b.keys())
    dot = sum(vec_a.get(k, 0.0) * vec_b.get(k, 0.0) for k in keys)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _is_within_date_range(
    created: datetime | None,
    date_range: tuple[datetime, datetime],
) -> bool:
    """Return whether ``created`` is inside an inclusive explicit range.

    Explicit filtering is fail-closed: a document without a parseable creation
    timestamp cannot be proven to belong to the requested range.
    """
    if created is None:
        return False

    start, end = date_range
    normalized_created = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
    normalized_start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    normalized_end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    return normalized_start <= normalized_created <= normalized_end


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────


def reciprocal_rank_fusion(
    results: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    doc_scores: dict[str, float] = {}
    for strategy_results in results:
        for rank, (doc_id, _score) in enumerate(strategy_results):
            rrf = 1.0 / (k + rank + 1)
            doc_scores[doc_id] = doc_scores.get(doc_id, 0.0) + rrf
    fused = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    return fused


# ── Temporal Expression Parsing ──────────────────────────────────────────────


def parse_temporal_expression(
    text: str,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Parse natural-language temporal expressions into (start, end) date ranges.

    Returns (None, None) if no expression is recognized.
    """
    if not text or not text.strip():
        return None, None
    text_lower = text.lower().strip()
    ref = now or datetime.now(UTC)
    ref_date = ref.replace(hour=0, minute=0, second=0, microsecond=0)

    # "last N days" / "past N days"
    m = re.match(r"(?:last|past)\s+(\d+)\s+days?", text_lower)
    if m:
        n = int(m.group(1))
        return ref_date - _timedelta(days=n), ref_date

    # "last week"
    if text_lower == "last week":
        monday = ref_date - _timedelta(days=ref_date.weekday())
        return monday - _timedelta(days=7), monday

    # "this week"
    if text_lower == "this week":
        monday = ref_date - _timedelta(days=ref_date.weekday())
        return monday, None

    # "this month"
    if text_lower == "this month":
        start = ref_date.replace(day=1)
        return start, None

    # "last month"
    if text_lower == "last month":
        first_this = ref_date.replace(day=1)
        if first_this.month == 1:
            last_start = first_this.replace(year=first_this.year - 1, month=12)
        else:
            last_start = first_this.replace(month=first_this.month - 1)
        return last_start, first_this

    # "yesterday"
    if text_lower == "yesterday":
        yday = ref_date - _timedelta(days=1)
        return yday, ref_date

    # "today"
    if text_lower == "today":
        return ref_date, None

    # "in March 2024" / "in January"
    m = re.match(r"in\s+(\w+)\s+(\d{4})", text_lower)
    if m:
        month_name = m.group(1)
        year = int(m.group(2))
        month_num = _MONTH_MAP.get(month_name)
        if month_num:
            start = datetime(year, month_num, 1, tzinfo=UTC)
            end_month = month_num + 1
            end_year = year
            if end_month > 12:
                end_month = 1
                end_year = year + 1
            end = datetime(end_year, end_month, 1, tzinfo=UTC)
            return start, end

    # "in March" (current year)
    m = re.match(r"in\s+(\w+)$", text_lower)
    if m:
        month_num = _MONTH_MAP.get(m.group(1))
        if month_num:
            year = ref.year
            start = datetime(year, month_num, 1, tzinfo=UTC)
            end_month = month_num + 1
            end_year = year
            if end_month > 12:
                end_month = 1
                end_year = year + 1
            end = datetime(end_year, end_month, 1, tzinfo=UTC)
            return start, end

    return None, None


def _timedelta(*, days: int = 0, hours: int = 0) -> Any:
    from datetime import timedelta as _td
    return _td(days=days, hours=hours)


# ── TEMPRResult ──────────────────────────────────────────────────────────────


@dataclass
class TEMPRResult:
    doc_id: str
    content: str
    scores: dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    retrieved_at: float = field(default_factory=time.time)


# ── TEMPRRetriever ───────────────────────────────────────────────────────────


class TEMPRRetriever:
    """Multi-strategy memory retriever: Semantic + BM25 + Temporal + Graph.

    Runs four retrieval strategies in parallel and fuses results via RRF.
    Configurable strategy weights control the contribution of each strategy
    to the fused ranking.

    Usage::

        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.3, "bm25": 0.4, ...})
        retriever.index(documents)
        results = retriever.retrieve("query text", top_k=10)
    """

    def __init__(
        self,
        strategy_weights: dict[str, float] | None = None,
        max_workers: int = 4,
    ) -> None:
        self.strategy_weights = strategy_weights or dict(_DEFAULT_STRATEGY_WEIGHTS)
        self._max_workers = max_workers
        self._documents: list[dict[str, Any]] = []

        # BM25 precomputed state
        self._avg_doc_len: float = 0.0
        self._term_idf: dict[str, float] = {}
        self._doc_term_freqs: list[Counter[str]] = []

        # Graph precomputed state
        self._doc_entities: list[set[str]] = []
        self._entity_doc_map: dict[str, set[int]] = {}

    # ── indexing ─────────────────────────────────────────────────────────

    def index(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self._precompute_bm25()
        self._precompute_graph()

    # ── retrieval ────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        date_range: tuple[datetime, datetime] | None = None,
    ) -> list[TEMPRResult]:
        if not self._documents:
            return []

        # Parse temporal expression from query for temporal strategy
        temporal_start, temporal_end = parse_temporal_expression(query)
        date_filtered_doc_ids: frozenset[str] | None = None
        if date_range is not None:
            date_filtered_doc_ids = frozenset(
                doc["id"]
                for doc in self._documents
                if _is_within_date_range(_parse_iso_datetime(doc.get("created_at")), date_range)
            )

        eligible_doc_ids: set[str] | None = None
        if date_range is not None:
            range_start, range_end = date_range
            eligible_doc_ids = set()
            for document in self._documents:
                created = _parse_iso_datetime(document.get("created_at"))
                doc_id = document.get("id")
                if (
                    isinstance(doc_id, str)
                    and created is not None
                    and range_start <= created <= range_end
                ):
                    eligible_doc_ids.add(doc_id)

        # Run strategies in parallel
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures: dict[str, Any] = {}

            weights = self.strategy_weights
            if weights.get("semantic", 0) > 0:
                futures["semantic"] = executor.submit(self._semantic_search, query, date_filtered_doc_ids)
            if weights.get("bm25", 0) > 0:
                futures["bm25"] = executor.submit(self._bm25_search, query, date_filtered_doc_ids)
            if weights.get("temporal", 0) > 0:
                futures["temporal"] = executor.submit(
                    self._temporal_search, query, temporal_start, temporal_end, date_range,
                )
            if weights.get("graph", 0) > 0:
                futures["graph"] = executor.submit(self._graph_search, query, date_filtered_doc_ids)

            strategy_results: dict[str, list[tuple[str, float]]] = {}
            for future in as_completed(futures.values()):
                for name, fut in futures.items():
                    if future is fut and name not in strategy_results:
                        try:
                            scored_results = fut.result()
                            if date_filtered_doc_ids is not None:
                                scored_results = [
                                    result for result in scored_results
                                    if result[0] in date_filtered_doc_ids
                                ]
                            strategy_results[name] = scored_results
                        except Exception:
                            logger.warning("Strategy %s failed", name, exc_info=True)
                            strategy_results[name] = []

        if eligible_doc_ids is not None:
            strategy_results = {
                name: [
                    (doc_id, score)
                    for doc_id, score in scored
                    if doc_id in eligible_doc_ids
                ]
                for name, scored in strategy_results.items()
            }

        # Fuse via RRF
        all_lists = list(strategy_results.values())
        fused = reciprocal_rank_fusion(all_lists, k=60)[:top_k]

        # Build per-document score dicts
        doc_score_maps: dict[str, dict[str, float]] = {r[0]: {} for r in fused}
        for strategy_name, scored_list in strategy_results.items():
            for doc_id, score in scored_list:
                if doc_id in doc_score_maps:
                    doc_score_maps[doc_id][strategy_name] = score

        # Build doc content map
        content_map: dict[str, str] = {}
        for doc in self._documents:
            content_map[doc["id"]] = doc.get("content", "")

        results = []
        for doc_id, final_score in fused:
            results.append(TEMPRResult(
                doc_id=doc_id,
                content=content_map.get(doc_id, ""),
                scores=doc_score_maps.get(doc_id, {}),
                final_score=round(final_score, 6),
            ))
        return results

    # ── Semantic strategy ────────────────────────────────────────────────

    def _semantic_search(
        self,
        query: str,
        eligible_doc_ids: frozenset[str] | None = None,
    ) -> list[tuple[str, float]]:
        query_vec = self._text_to_tfidf_vector(query)
        scored: list[tuple[str, float]] = []
        for doc in self._documents:
            if eligible_doc_ids is not None and doc["id"] not in eligible_doc_ids:
                continue
            doc_vec = self._text_to_tfidf_vector(doc["content"])
            sim = _cosine_similarity(query_vec, doc_vec)
            scored.append((doc["id"], sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _text_to_tfidf_vector(self, text: str) -> dict[str, float]:
        terms = _tokenize(text)
        if not terms:
            return {}
        tf = Counter(terms)
        max_tf = max(tf.values()) if tf else 1
        total_docs = max(len(self._documents), 1)

        vec: dict[str, float] = {}
        for term, count in tf.items():
            norm_tf = count / max_tf
            doc_count = len(self._entity_doc_map.get(term, set())) if self._term_idf else 0
            if self._term_idf:
                idf = self._term_idf.get(term, math.log((total_docs + 1) / (1 + 1)))
            else:
                idf = math.log((total_docs + 1) / (doc_count + 1))
            vec[term] = norm_tf * idf
        return vec

    # ── BM25 strategy ────────────────────────────────────────────────────

    def _bm25_search(
        self,
        query: str,
        eligible_doc_ids: frozenset[str] | None = None,
    ) -> list[tuple[str, float]]:
        query_terms = _tokenize(query)
        if not query_terms or not self._documents:
            return []

        n = len(self._documents)
        scored: list[tuple[str, float]] = []
        for idx, doc in enumerate(self._documents):
            if eligible_doc_ids is not None and doc["id"] not in eligible_doc_ids:
                continue
            score = 0.0
            tf_counter = self._doc_term_freqs[idx]
            doc_len = sum(tf_counter.values())
            for term in query_terms:
                tf = tf_counter.get(term, 0)
                if tf == 0:
                    continue
                idf = self._term_idf.get(term, math.log((n + 1) / (1 + 1)))
                numerator = tf * (_BM25_K1 + 1)
                denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / max(self._avg_doc_len, 1))
                score += idf * numerator / denominator
            scored.append((doc["id"], score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _precompute_bm25(self) -> None:
        n = len(self._documents)
        if n == 0:
            self._avg_doc_len = 0.0
            self._term_idf = {}
            self._doc_term_freqs = []
            return

        self._doc_term_freqs = []
        total_terms = 0
        doc_freqs: Counter[str] = Counter()

        for doc in self._documents:
            terms = _tokenize(doc["content"])
            tf = Counter(terms)
            self._doc_term_freqs.append(tf)
            total_terms += sum(tf.values())
            doc_freqs.update(tf.keys())

        self._avg_doc_len = total_terms / n if n > 0 else 0.0

        self._term_idf = {}
        for term, df in doc_freqs.items():
            self._term_idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    # ── Temporal strategy ────────────────────────────────────────────────

    def _temporal_search(
        self,
        query: str,
        temporal_start: datetime | None,
        temporal_end: datetime | None,
        date_range: tuple[datetime, datetime] | None,
    ) -> list[tuple[str, float]]:
        now = datetime.now(UTC)
        scored: list[tuple[str, float]] = []

        for doc in self._documents:
            created_str = doc.get("created_at")
            created = _parse_iso_datetime(created_str)
            if created is None:
                if date_range is not None:
                    continue
                score = 0.0
            else:
                # Date range filtering — exclude entirely, not zero-score
                if date_range is not None and not _is_within_date_range(created, date_range):
                    continue

                # Temporal expression filtering — exclude entirely
                if temporal_start is not None:
                    if created < temporal_start:
                        continue
                    if temporal_end is not None and created >= temporal_end:
                        continue

                # Exponential decay based on age in days
                age_seconds = (now - created).total_seconds()
                age_days = max(0, age_seconds) / 86400.0
                score = math.exp(-_TEMPORAL_DECAY_LAMBDA * age_days)

            scored.append((doc["id"], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ── Graph strategy ───────────────────────────────────────────────────

    def _graph_search(
        self,
        query: str,
        eligible_doc_ids: frozenset[str] | None = None,
    ) -> list[tuple[str, float]]:
        query_entities = self._extract_entities(query)
        if not self._documents:
            return []

        scored: list[tuple[str, float]] = []
        for idx, doc in enumerate(self._documents):
            if eligible_doc_ids is not None and doc["id"] not in eligible_doc_ids:
                continue
            doc_ents = self._doc_entities[idx] if idx < len(self._doc_entities) else set()
            score = 0.0
            reasons = 0

            # Direct entity overlap with query
            direct_overlap = query_entities & doc_ents
            if direct_overlap:
                score += len(direct_overlap) / max(len(query_entities), 1) * 0.4
                reasons += 1

            # Multi-hop: docs that share entities with other docs that match query
            if query_entities:
                connected_entities = set()
                for qe in query_entities:
                    linked_docs = self._entity_doc_map.get(qe, set())
                    for linked_idx in linked_docs:
                        if linked_idx < len(self._doc_entities):
                            connected_entities |= self._doc_entities[linked_idx]
                # Overlap between this doc's entities and the connected set
                # (excluding entities already in direct overlap)
                indirect_overlap = (doc_ents & connected_entities) - direct_overlap
                if indirect_overlap:
                    score += len(indirect_overlap) / max(len(connected_entities), 1) * 0.2
                    reasons += 1

            # Base score from any entity presence; zero if no overlap
            score = min(score, 1.0) if reasons > 0 else 0.0

            scored.append((doc["id"], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _extract_entities(self, text: str) -> set[str]:
        entities: set[str] = set()
        # Multi-word capitalized sequences (proper nouns)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
            entities.add(match.group(0).lower())

        # Acronyms / all-caps terms
        for match in re.finditer(r"\b([A-Z]{2,}(?:\d+)?)\b", text):
            entities.add(match.group(0).lower())

        # Single capitalized words — collect all, then exclude sentence-start ones
        for match in re.finditer(r"\b([A-Z][a-z]{2,})\b", text):
            word = match.group(0)
            start = match.start()
            # Exclude if at position 0 (sentence start) — check preceding chars
            if start > 0:
                preceding = text[start - 1]
                if preceding == "." or preceding == "!" or preceding == "?":
                    pass  # after sentence-ending punctuation, could be entity
                elif preceding != " ":
                    continue
            entities.add(word.lower())
        return entities

    def _precompute_graph(self) -> None:
        self._doc_entities = []
        self._entity_doc_map = {}
        for idx, doc in enumerate(self._documents):
            entities = self._extract_entities(doc["content"])
            self._doc_entities.append(entities)
            for ent in entities:
                if ent not in self._entity_doc_map:
                    self._entity_doc_map[ent] = set()
                self._entity_doc_map[ent].add(idx)
