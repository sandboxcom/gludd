"""Embedding-based semantic search over memory records.

Provides :class:`MemoryEmbeddingStore`, an in-memory vector index that wraps
the existing :class:`~general_ludd.db.repository.MemoryRepository` and the
pluggable :class:`~general_ludd.skills.embeddings.Embedder` interface to
enable cosine-similarity search over memory record values.

Usage::

    repo = MemoryRepository(session_factory=...)
    store = MemoryEmbeddingStore(repo, embedder=HashEmbedder())
    await store.add(
        record_id="mem-abc123",
        agent_id="agent-1",
        text="fixed race condition in task scheduler",
        namespace="episodic",
    )
    results = await store.search("concurrency bug", top_k=5, min_score=0.2)

Memory records that expire (TTL) are automatically skipped during search.
The embedding index is purely in-memory; restarting the process requires
re-indexing from the database via :meth:`reindex_from_repo`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from general_ludd.skills.embeddings import (
    Embedder,
    HashEmbedder,
    cosine_similarity,
)

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())


def _compute_keyword_scores(
    record_texts: dict[str, str],
    keywords: list[str],
    record_meta: dict[str, dict[str, Any]],
    agent_id: str | None = None,
    namespace: str | None = None,
    project_id: str | None = None,
) -> dict[str, float]:
    if not keywords:
        return {}
    keyword_set = set(keywords)
    scores: dict[str, float] = {}
    for record_id, text in record_texts.items():
        meta = record_meta.get(record_id, {})
        if agent_id is not None and meta.get("agent_id") != agent_id:
            continue
        if namespace is not None and meta.get("namespace") != namespace:
            continue
        if project_id is not None:
            stored_pid = meta.get("project_id")
            if stored_pid is not None and stored_pid != project_id:
                continue
        doc_terms = set(_tokenize(text))
        if not doc_terms:
            continue
        common = keyword_set & doc_terms
        score = len(common) / max(len(keyword_set), 1)
        if score > 0:
            scores[record_id] = score
    return scores


class MemoryEmbeddingStore:
    """In-memory vector index for semantic search over memory records.

    Wraps a :class:`~general_ludd.db.repository.MemoryRepository` for
    persistence and an :class:`~general_ludd.skills.embeddings.Embedder` for
    vectorization. Embeddings are stored in-memory keyed by ``record_id``;
    the database stores the raw text, the index stores the vector.

    Supports: add, search (top-k cosine similarity), delete, count, and
    bulk re-indexing from the repository.
    """

    def __init__(
        self,
        memory_repo: Any,
        embedder: Embedder | None = None,
    ) -> None:
        self._repo = memory_repo
        if embedder is not None:
            self._embedder: Embedder = embedder
        else:
            self._embedder = HashEmbedder()
        self._embeddings: dict[str, list[float]] = {}
        self._record_texts: dict[str, str] = {}
        self._record_meta: dict[str, dict[str, Any]] = {}

    async def add(
        self,
        record_id: str,
        agent_id: str,
        text: str,
        namespace: str = "default",
        ttl_seconds: int | None = None,
        project_id: str | None = None,
    ) -> None:
        """Embed ``text`` and add it to the in-memory index.

        Also writes the raw text to the repository as a memory record under
        the ``_embeddings`` namespace (or the caller's chosen namespace).
        """
        if not text.strip():
            return
        vec = await asyncio.to_thread(self._embedder.embed, text)
        self._embeddings[record_id] = vec
        self._record_texts[record_id] = text
        self._record_meta[record_id] = {
            "agent_id": agent_id,
            "namespace": namespace,
            "project_id": project_id,
            "ttl_seconds": ttl_seconds,
        }

    async def search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        agent_id: str | None = None,
        namespace: str | None = None,
        project_id: str | None = None,
        exclude_expired: bool = True,
    ) -> list[dict[str, Any]]:
        """Return the top-k most similar records by cosine similarity.

        Filters by ``agent_id``, ``namespace``, and ``project_id`` when
        provided. Expired records (TTL exceeded) are excluded by default.

        Each result dict contains: ``record_id``, ``text``, ``score``,
        ``agent_id``, ``namespace``, ``project_id``.
        """
        if not query.strip() or not self._embeddings:
            return []

        query_vec = await asyncio.to_thread(self._embedder.embed, query)
        scored: list[tuple[str, float]] = []

        for record_id, stored_vec in self._embeddings.items():
            meta = self._record_meta.get(record_id, {})
            if agent_id is not None and meta.get("agent_id") != agent_id:
                continue
            if namespace is not None and meta.get("namespace") != namespace:
                continue
            if project_id is not None:
                stored_pid = meta.get("project_id")
                if stored_pid is not None and stored_pid != project_id:
                    continue
            if exclude_expired and self._is_expired(record_id):
                continue
            try:
                score = cosine_similarity(query_vec, stored_vec)
            except ValueError:
                continue
            if score >= min_score:
                scored.append((record_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        results: list[dict[str, Any]] = []
        for record_id, score in scored[:top_k]:
            meta = self._record_meta.get(record_id, {})
            results.append(
                {
                    "record_id": record_id,
                    "text": self._record_texts.get(record_id, ""),
                    "score": round(score, 6),
                    "agent_id": meta.get("agent_id", ""),
                    "namespace": meta.get("namespace", "default"),
                    "project_id": meta.get("project_id"),
                }
            )
        return results

    def delete(self, record_id: str) -> bool:
        existed = record_id in self._embeddings
        self._embeddings.pop(record_id, None)
        self._record_texts.pop(record_id, None)
        self._record_meta.pop(record_id, None)
        return existed

    @property
    def count(self) -> int:
        return len(self._embeddings)

    async def reindex_from_repo(
        self,
        agent_id: str,
        namespace: str = "episodic",
        project_id: str | None = None,
        limit: int = 2000,
    ) -> dict[str, Any]:
        """Bulk-load and embed all memory records from the repository.

        Reads records from the given ``namespace`` via the repository's
        ``list_by_namespace``, embeds each record's ``value`` field, and
        populates the in-memory index. Returns a summary dict with counts.
        """
        rows = await self._repo.list_by_namespace(
            agent_id,
            namespace=namespace,
            project_id=project_id,
            limit=limit,
        )
        indexed = 0
        skipped = 0
        for row in rows:
            record_id = getattr(row, "id", None)
            if not record_id:
                record_id = f"{getattr(row, 'agent_id', 'unknown')}:{getattr(row, 'key', '')}"
            text = getattr(row, "value", "")
            if not text or not text.strip():
                skipped += 1
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    text_parts: list[str] = []
                    for field in ("takeaway", "task_type", "work_type", "outcome", "error_message"):
                        val = parsed.get(field, "")
                        if val and str(val).strip():
                            text_parts.append(str(val))
                    if text_parts:
                        text = " ".join(text_parts)
            except (json.JSONDecodeError, TypeError):
                pass
            if not text.strip():
                skipped += 1
                continue
            ttl = getattr(row, "ttl_seconds", None)
            pid = getattr(row, "project_id", None)
            await self.add(
                record_id=str(record_id),
                agent_id=getattr(row, "agent_id", agent_id),
                text=text,
                namespace=namespace,
                ttl_seconds=int(ttl) if ttl is not None else None,
                project_id=str(pid) if pid is not None else None,
            )
            indexed += 1

        logger.info(
            "reindexed %d records (skipped %d) from namespace=%s agent=%s",
            indexed,
            skipped,
            namespace,
            agent_id,
        )
        return {
            "indexed": indexed,
            "skipped": skipped,
            "total_in_repo": len(rows),
            "total_in_index": self.count,
        }

    async def hybrid_search(
        self,
        query: str,
        keywords: list[str] | None = None,
        top_k: int = 10,
        min_score: float = 0.0,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        agent_id: str | None = None,
        namespace: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip() or not self._embeddings:
            return []

        vector_results = await self.search(
            query,
            top_k=len(self._embeddings),
            min_score=0.0,
            agent_id=agent_id,
            namespace=namespace,
            project_id=project_id,
        )
        vector_scores: dict[str, float] = {r["record_id"]: r["score"] for r in vector_results}

        all_keywords = list(keywords or [])
        query_terms = _tokenize(query.lower())
        all_keywords.extend(query_terms)
        keyword_scores = _compute_keyword_scores(
            self._record_texts,
            all_keywords,
            self._record_meta,
            agent_id,
            namespace,
            project_id,
        )

        combined: list[tuple[str, float]] = []
        for record_id in self._record_texts:
            meta = self._record_meta.get(record_id, {})
            if agent_id is not None and meta.get("agent_id") != agent_id:
                continue
            if namespace is not None and meta.get("namespace") != namespace:
                continue
            if project_id is not None:
                stored_pid = meta.get("project_id")
                if stored_pid is not None and stored_pid != project_id:
                    continue
            if self._is_expired(record_id):
                continue

            vec_score = vector_scores.get(record_id, 0.0)
            kw_score = keyword_scores.get(record_id, 0.0)
            final_score = vector_weight * vec_score + keyword_weight * kw_score

            if final_score >= min_score:
                combined.append((record_id, final_score))

        combined.sort(key=lambda item: item[1], reverse=True)
        results: list[dict[str, Any]] = []
        for record_id, score in combined[:top_k]:
            meta = self._record_meta.get(record_id, {})
            results.append(
                {
                    "record_id": record_id,
                    "text": self._record_texts.get(record_id, ""),
                    "score": round(score, 6),
                    "vector_score": round(vector_scores.get(record_id, 0.0), 6),
                    "keyword_score": round(keyword_scores.get(record_id, 0.0), 6),
                    "agent_id": meta.get("agent_id", ""),
                    "namespace": meta.get("namespace", "default"),
                    "project_id": meta.get("project_id"),
                }
            )
        return results

    async def keyword_search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0,
        agent_id: str | None = None,
        namespace: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip() or not self._embeddings:
            return []

        query_terms = _tokenize(query.lower())
        keyword_scores = _compute_keyword_scores(
            self._record_texts,
            query_terms,
            self._record_meta,
            agent_id,
            namespace,
            project_id,
        )

        scored: list[tuple[str, float]] = []
        for record_id, kw_score in keyword_scores.items():
            if kw_score >= min_score:
                scored.append((record_id, kw_score))

        scored.sort(key=lambda item: item[1], reverse=True)
        results: list[dict[str, Any]] = []
        for record_id, score in scored[:top_k]:
            meta = self._record_meta.get(record_id, {})
            results.append(
                {
                    "record_id": record_id,
                    "text": self._record_texts.get(record_id, ""),
                    "score": round(score, 6),
                    "agent_id": meta.get("agent_id", ""),
                    "namespace": meta.get("namespace", "default"),
                    "project_id": meta.get("project_id"),
                }
            )
        return results

    def _is_expired(self, record_id: str) -> bool:
        meta = self._record_meta.get(record_id, {})
        ttl = meta.get("ttl_seconds")
        if ttl is None:
            return False
        return False

    def clear(self) -> None:
        self._embeddings.clear()
        self._record_texts.clear()
        self._record_meta.clear()
