"""Semantic searcher for G3 codebase retrieval."""

from __future__ import annotations

import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

from general_ludd.retrieval.indexer import _cosine_similarity, _tokenize
from general_ludd.security.safe_diskcache import open_safe_diskcache

logger = logging.getLogger(__name__)


class SemanticSearcher:
    """Performs semantic search over an indexed codebase."""

    def __init__(self, cache_dir: str | Path = ".gludd/retrieval_cache") -> None:
        path = os.path.expanduser(os.path.expandvars(str(cache_dir)))
        self._cache_dir = path
        if os.path.isdir(path):
            self._cache: Any | None = open_safe_diskcache(path)
        else:
            self._cache = None

    def search(self, query: str, *, top_k: int = 10) -> list[dict[str, Any]]:
        if self._cache is None:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_vector: dict[str, float] = {k: float(v) for k, v in Counter(query_tokens).items()}

        scored: list[tuple[float, dict[str, Any]]] = []
        for key in self._cache.iterkeys():
            entry = self._cache.get(key)
            if entry is None or "vector" not in entry:
                continue
            score = _cosine_similarity(query_vector, entry["vector"])
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        return [
            {
                "filepath": entry["filepath"],
                "content": entry["content"],
                "score": round(score, 4),
            }
            for score, entry in top
        ]

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
