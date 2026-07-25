"""Hindsight memory adapter — TEMPR multi-strategy retrieval via Docker sidecar.

Integrates Hindsight (Semantic+BM25+Graph+Temporal) as a configurable backend
for CrossConversationMemory. Falls back to an internal in-memory store when
the Hindsight sidecar is unreachable or disabled.

Environment:
  HINDSIGHT_URL     — HTTP base URL (default http://localhost:8888)
  HINDSIGHT_ENABLED — "true" / "1" to activate (default false)
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_HINDSIGHT_IMPORT_ERROR: str | None = None
try:
    from hindsight_client import Hindsight as _HindsightClient  # type: ignore[import-not-found]
except ImportError as exc:
    _HindsightClient = None
    _HINDSIGHT_IMPORT_ERROR = str(exc)


class _InMemoryStore:
    """Lightweight in-memory fallback with basic keyword matching."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def retain(self, content: str, metadata: dict[str, object] | None = None) -> str:
        record_id = f"mem_{int(time.time() * 1_000_000)}_{len(self._records)}"
        entry = {
            "id": record_id,
            "content": content,
            "metadata": dict(metadata or {}),
            "created_at": time.time(),
        }
        with self._lock:
            self._records.append(entry)
        return record_id

    def recall(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        scored = self._score_all(query)
        return scored[:top_k]

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self.recall(query, top_k)

    def _score_all(self, query: str) -> list[dict[str, Any]]:
        ql = query.lower()
        qterms = _tokenize(ql)
        results: list[dict[str, Any]] = []
        with self._lock:
            for rec in self._records:
                score = 0.0
                content_lower = rec["content"].lower()
                if ql in content_lower:
                    score += 0.5
                for term in qterms:
                    if term in content_lower:
                        score += 0.15
                meta_str = str(rec.get("metadata", {})).lower()
                for term in qterms:
                    if term in meta_str:
                        score += 0.05
                if score > 0:
                    results.append({
                        "id": rec["id"],
                        "content": rec["content"],
                        "metadata": rec.get("metadata", {}),
                        "score": round(min(score, 1.0), 3),
                    })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results


class HindsightMemoryAdapter:
    """Thread-safe singleton adapter wrapping Hindsight with fallback.

    Implements the retain / recall / search interface and adds
    Hindsight-specific reflect() and create_memory_bank().

    Usage::

        adapter = HindsightMemoryAdapter.get_instance()
        adapter.retain("some content", {"source": "test"})
        results = adapter.recall("some query")
        answer = adapter.reflect("summarize recent work")
    """

    _instance: HindsightMemoryAdapter | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        *,
        url: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._url = url or os.environ.get("HINDSIGHT_URL", "http://localhost:8888")
        self._enabled = (
            enabled
            if enabled is not None
            else os.environ.get("HINDSIGHT_ENABLED", "").lower() in ("true", "1")
        )
        self._client: Any = None
        self._fallback: _InMemoryStore = _InMemoryStore()
        self._connected: bool = False
        self._lock = threading.Lock()

        if self._enabled:
            self._connect()

    # ---------------------------------------------------------------- singleton

    @classmethod
    def get_instance(cls, **kwargs: Any) -> HindsightMemoryAdapter:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ----------------------------------------------------------------- connect

    def _connect(self) -> None:
        if _HindsightClient is None:
            logger.warning(
                "hindsight_client not installed (%s) — using fallback",
                _HINDSIGHT_IMPORT_ERROR or "unknown",
            )
            self._connected = False
            return
        try:
            self._client = _HindsightClient(base_url=self._url)
            self._connected = True
            logger.info("HindsightMemoryAdapter connected to %s", self._url)
        except Exception as exc:
            logger.warning("Hindsight sidecar unreachable at %s: %s — using fallback", self._url, exc)
            self._connected = False

    # ------------------------------------------------------------------ retain

    def retain(
        self, content: str, metadata: dict[str, object] | None = None,
    ) -> str:
        if self._connected and self._client is not None:
            try:
                obs_id = self._client.observe(
                    content=content,
                    metadata=metadata or {},
                )
                return str(obs_id)
            except Exception as exc:
                logger.warning("Hindsight retain failed: %s — using fallback", exc)
        return self._fallback.retain(content, metadata)

    # ------------------------------------------------------------------ recall

    def recall(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self._connected and self._client is not None:
            try:
                results = self._client.recall(query=query, top_k=top_k)
                return list(results)
            except Exception as exc:
                logger.warning("Hindsight recall failed: %s — using fallback", exc)
        return self._fallback.recall(query, top_k)

    # ------------------------------------------------------------------ search

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self._connected and self._client is not None:
            try:
                results = self._client.search(query=query, top_k=top_k)
                return list(results)
            except Exception as exc:
                logger.warning("Hindsight search failed: %s — using fallback", exc)
        return self._fallback.search(query, top_k)

    # ----------------------------------------------------------------- reflect

    def reflect(self, query: str) -> str:
        if self._connected and self._client is not None:
            try:
                answer = self._client.reflect(query=query)
                return str(answer)
            except Exception as exc:
                logger.warning("Hindsight reflect failed: %s — using fallback", exc)
        scored = self._fallback.recall(query, top_k=3)
        if not scored:
            return ""
        parts = [f"- {r['content'][:200]}" for r in scored]
        return "Fallback — top matches:\n" + "\n".join(parts)

    # ------------------------------------------------------- create_memory_bank

    def create_memory_bank(
        self,
        name: str,
        mission: str = "",
        directives: list[str] | None = None,
        disposition: str = "helpful",
    ) -> dict[str, Any]:
        if self._connected and self._client is not None:
            try:
                result = self._client.create_memory_bank(
                    name=name,
                    mission=mission,
                    directives=directives or [],
                    disposition=disposition,
                )
                return dict(result) if result else {}
            except Exception as exc:
                logger.warning("Hindsight create_memory_bank failed: %s", exc)
        return {
            "name": name,
            "mission": mission,
            "directives": directives or [],
            "disposition": disposition,
            "backend": "fallback",
            "created": True,
        }

    # ---------------------------------------------------------------- health

    def health_check(self) -> dict[str, Any]:
        return {
            "backend": "hindsight" if self._connected else "fallback",
            "enabled": self._enabled,
            "url": self._url,
            "connected": self._connected,
        }

    @property
    def is_connected(self) -> bool:
        return self._connected


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
