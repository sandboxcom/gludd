"""Retrieval service: hybrid lexical/vector search with reranking (AIML-006).

Implements docs/specs/FEATURE_AI_ML_EXPERT.md §6.2 (Retrieval):

  - BM25/lexical and dense vector scoring behind one interface.
  - Hybrid fusion (weighted combination) and reranking.
  - Every search records: query rewrite, index version, filter policy,
    retrieved source IDs, scores, reranker version, and citation spans.
  - Evaluation metrics: recall@k, MRR, nDCG.

The dense backend is a deterministic hash-based vector stub — production
deployments swap in a real embedding model without changing the interface.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass


def _require_nonempty_str(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class RetrievedPassage:
    """A single retrieved passage with per-backend scores and citation span."""

    source_id: str
    content: str
    lexical_score: float
    dense_score: float
    hybrid_score: float
    citation_span: tuple[int, int]
    rank: int

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "source_id")
        _require_nonempty_str(self.content, "content")
        if self.rank < 0:
            raise ValueError(f"rank must be >= 0, got {self.rank}")
        if self.citation_span[0] < 0 or self.citation_span[1] < self.citation_span[0]:
            raise ValueError(f"citation_span must be (start, end) with 0 <= start <= end, got {self.citation_span}")


@dataclass(frozen=True)
class RetrievalResult:
    """Recorded retrieval result (spec §6.2).

    Every answer records the query rewrite, index version, filter policy,
    retrieved source IDs, scores, reranker version, and chosen citation spans.
    Raw confidential queries are excluded from this record by the caller;
    the service records whatever rewrite the caller supplies.
    """

    query: str
    query_rewrite: str
    index_version: str
    filter_policy: str
    passages: tuple[RetrievedPassage, ...]
    reranker_version: str
    retrieved_source_ids: tuple[str, ...]
    latency_ms: float

    def __post_init__(self) -> None:
        _require_nonempty_str(self.query, "query")
        _require_nonempty_str(self.query_rewrite, "query_rewrite")
        _require_nonempty_str(self.index_version, "index_version")
        _require_nonempty_str(self.reranker_version, "reranker_version")


@dataclass(frozen=True)
class RetrievalMetrics:
    """Evaluation metrics for a retrieval run (spec §6.2).

    recall@k, MRR, and nDCG are the core ranking-quality metrics. Answer
    faithfulness, citation precision, freshness, latency, and cost are
    tracked separately by the evaluation harness.
    """

    recall_at_k: float
    mrr: float
    ndcg: float

    def __post_init__(self) -> None:
        for name in ("recall_at_k", "mrr", "ndcg"):
            val = getattr(self, name)
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0], got {val}")


# Weight for hybrid fusion: lexical vs. dense. Dense is weighted higher
# because it captures semantic similarity beyond exact-term overlap.
_LEXICAL_WEIGHT: float = 0.4
_DENSE_WEIGHT: float = 0.6

# Dimensionality of the hash-based dense vector stub.
_VEC_DIM: int = 64


class RetrievalService:
    """Hybrid lexical/vector retrieval with reranking and recording.

    Provides BM25-like lexical scoring and a dense vector stub behind one
    interface. Reranking applies hybrid fusion (weighted lexical + dense).
    Every search produces a fully recorded ``RetrievalResult``.

    The dense backend is a deterministic hash-based vector — production
    deployments substitute a real embedding model without changing callers.
    """

    def __init__(
        self,
        *,
        index_version: str = "v1",
        reranker_version: str = "v1",
        filter_policy: str = "default",
    ) -> None:
        _require_nonempty_str(index_version, "index_version")
        _require_nonempty_str(reranker_version, "reranker_version")
        _require_nonempty_str(filter_policy, "filter_policy")
        self._corpus: list[tuple[str, str]] = []
        self._index_version = index_version
        self._reranker_version = reranker_version
        self._filter_policy = filter_policy

    def index(self, source_id: str, content: str) -> None:
        """Add a document to the in-memory corpus."""
        _require_nonempty_str(source_id, "source_id")
        _require_nonempty_str(content, "content")
        self._corpus.append((source_id, content))

    # -- scoring backends --------------------------------------------------

    @staticmethod
    def _lexical_score(query_terms: list[str], doc: str) -> float:
        """BM25-like lexical overlap score (term coverage ratio)."""
        if not query_terms:
            return 0.0
        doc_terms = set(doc.lower().split())
        if not doc_terms:
            return 0.0
        overlap = sum(1 for t in query_terms if t in doc_terms)
        return overlap / len(query_terms)

    @staticmethod
    def _hash_vec(text: str) -> list[float]:
        """Deterministic hash-based dense vector (stub for real embeddings)."""
        vec: list[float] = [0.0] * _VEC_DIM
        for word in text.lower().split():
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            vec[h % _VEC_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @classmethod
    def _dense_score(cls, query: str, doc: str) -> float:
        """Cosine similarity of hash-based vectors."""
        qv = cls._hash_vec(query)
        dv = cls._hash_vec(doc)
        dot = sum(a * b for a, b in zip(qv, dv, strict=True))
        return max(0.0, dot)

    def _hybrid_score(self, lexical: float, dense: float) -> float:
        return _LEXICAL_WEIGHT * lexical + _DENSE_WEIGHT * dense

    # -- public API --------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        k: int = 10,
        query_rewrite: str | None = None,
    ) -> RetrievalResult:
        """Hybrid search with reranking and full result recording.

        Returns the top-``k`` passages reranked by hybrid fusion score.
        The result records the query rewrite, index version, filter policy,
        retrieved source IDs, per-passage scores, reranker version, and
        citation spans.
        """
        _require_nonempty_str(query, "query")
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")

        start = time.perf_counter()
        rewrite = query_rewrite if query_rewrite else query
        query_terms = rewrite.lower().split()

        scored: list[tuple[str, str, float, float]] = []
        for source_id, content in self._corpus:
            lex = self._lexical_score(query_terms, content)
            dens = self._dense_score(rewrite, content)
            scored.append((source_id, content, lex, dens))

        # Rerank by hybrid fusion score (descending).
        scored.sort(
            key=lambda x: self._hybrid_score(x[2], x[3]),
            reverse=True,
        )
        top = scored[:k]

        passages: list[RetrievedPassage] = []
        for rank, (source_id, content, lex, dens) in enumerate(top):
            hybrid = self._hybrid_score(lex, dens)
            passages.append(
                RetrievedPassage(
                    source_id=source_id,
                    content=content,
                    lexical_score=lex,
                    dense_score=dens,
                    hybrid_score=hybrid,
                    citation_span=(0, len(content)),
                    rank=rank,
                )
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        source_ids = tuple(p.source_id for p in passages)

        return RetrievalResult(
            query=query,
            query_rewrite=rewrite,
            index_version=self._index_version,
            filter_policy=self._filter_policy,
            passages=tuple(passages),
            reranker_version=self._reranker_version,
            retrieved_source_ids=source_ids,
            latency_ms=elapsed_ms,
        )

    def evaluate(
        self,
        query: str,
        relevant_ids: set[str],
        *,
        k: int = 10,
    ) -> RetrievalMetrics:
        """Compute recall@k, MRR, and nDCG for a single query.

        ``relevant_ids`` is the ground-truth set of relevant source IDs.
        """
        _require_nonempty_str(query, "query")
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")

        result = self.search(query, k=k)
        retrieved_ids = [p.source_id for p in result.passages]
        top_k = retrieved_ids[:k]

        # recall@k: fraction of relevant docs retrieved in top-k.
        relevant_in_top_k = sum(1 for sid in top_k if sid in relevant_ids)
        recall = relevant_in_top_k / len(relevant_ids) if relevant_ids else 0.0

        # MRR: reciprocal rank of first relevant doc.
        mrr = 0.0
        for i, sid in enumerate(retrieved_ids):
            if sid in relevant_ids:
                mrr = 1.0 / (i + 1)
                break

        # nDCG: normalized discounted cumulative gain (binary relevance).
        dcg = sum(
            (1.0 if retrieved_ids[i] in relevant_ids else 0.0) / math.log2(i + 2)
            for i in range(min(k, len(retrieved_ids)))
        )
        ideal_hits = min(len(relevant_ids), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits)) if ideal_hits > 0 else 1.0
        ndcg = dcg / idcg if idcg > 0 else 0.0

        return RetrievalMetrics(
            recall_at_k=min(1.0, recall),
            mrr=min(1.0, mrr),
            ndcg=min(1.0, ndcg),
        )


__all__ = [
    "RetrievalMetrics",
    "RetrievalResult",
    "RetrievalService",
    "RetrievedPassage",
]
