"""Embedding-based skill matching for the RAG routing Tier 1 integration.

Provides a pluggable embedder interface for computing skill-description
embeddings and cosine-similarity matching against a user query. The default
backend is a dependency-free hash-based bag-of-words embedder; when an OpenAI
API key is available, ``text-embedding-3-small`` is used instead.

This module backs ``SkillRegistry.match_trigger``'s embedding fallback: when
the existing literal-substring fast path returns no matches, query embeddings
are compared against every registered skill's description embedding and the
skills above a similarity threshold are returned.

Design notes:
- ``HashEmbedder`` uses token hashing into a fixed-dimensional vector, giving a
  lightweight bag-of-words representation that is good enough to surface
  synonym matches (e.g. "deadlock" → "concurrency") without external deps.
- ``SkillEmbedder`` caches per-skill vectors keyed by ``Skill.name`` so each
  description is embedded exactly once per registry lifetime.
- The OpenAI embedder is imported lazily so the dependency stays optional.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from general_ludd.skills.skill import Skill


_DEFAULT_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
        "the", "this", "to", "was", "were", "will", "with", "i", "you",
        "we", "they", "he", "she", "but", "not", "your", "our", "their",
    }
)


@runtime_checkable
class Embedder(Protocol):
    """Pluggable embedder interface."""

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for *text*."""
        ...


def _tokenize(text: str) -> list[str]:
    return [tok for tok in _TOKEN_RE.findall(text.lower()) if tok not in _STOPWORDS]


def _stem(word: str) -> str:
    """Minimal suffix stripper so plurals/verb forms collapse to a common root.

    Handles the common English inflections (-s, -es, -ed, -ing, -ies/-ied→y)
    without a full Porter stemmer. Keeps the hash embedder's bag-of-words
    representation robust to minor morphological variation.
    """
    if len(word) <= 3:
        return word
    for suffix in ("ment", "ing", "ies", "ied", "es", "ed", "s"):
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if len(stem) < 3:
                continue
            if suffix in ("ies", "ied"):
                return stem + "y"
            return stem
    return word


class HashEmbedder:
    """Dependency-free bag-of-words embedder using feature hashing.

    Each token is hashed into one of ``dim`` buckets; the corresponding
    dimension's count is incremented and the resulting vector is L2-normalized.
    This yields a lightweight semantic representation that still surfaces
    vocabulary overlap (so "deadlock" and "concurrency bug" share signal via
    surrounding context even without a learned model).
    """

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        """Create a feature-hashing embedder with *dim* dimensions."""
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        """Return a normalized feature-hashed vector for *text*."""
        vec = [0.0] * self.dim
        for token in _tokenize(text):
            feature = _stem(token)
            h = hashlib.sha256(feature.encode("utf-8")).digest()  # nosec B324 — feature hashing, not crypto
            bucket = int.from_bytes(h[:8], "little") % self.dim
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


class OpenAIEmbedder:
    """OpenAI ``text-embedding-3-small`` embedder.

    Imported lazily so the ``openai`` package remains an optional dependency.
    Construction fails fast if the package is missing or no API key is set.
    """

    MODEL = "text-embedding-3-small"

    def __init__(self, api_key: str | None = None) -> None:
        """Create the optional OpenAI embedder with an explicit or environment key."""
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "openai package is required for OpenAIEmbedder"
            ) from exc
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIEmbedder")
        self._client = openai.OpenAI(api_key=key)

    def embed(self, text: str) -> list[float]:  # pragma: no cover - network
        """Request and return an embedding from the configured OpenAI model."""
        resp = self._client.embeddings.create(model=self.MODEL, input=text)
        return list(resp.data[0].embedding)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in ``[-1, 1]``; returns 0.0 for zero vectors.

    Raises ``ValueError`` when the vectors differ in length.
    """
    if len(a) != len(b):
        raise ValueError(
            f"vector length mismatch: {len(a)} != {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SkillEmbedder:
    """Embeds skill descriptions with caching and query embedding.

    By default uses :class:`HashEmbedder` (no external deps). When
    ``use_openai_if_available=True`` and ``OPENAI_API_KEY`` is set, it uses
    :class:`OpenAIEmbedder` (``text-embedding-3-small``). An explicit
    ``embedder`` argument always wins.
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        use_openai_if_available: bool = False,
    ) -> None:
        """Select an embedder and initialize the per-skill vector cache."""
        if embedder is not None:
            self._embedder: Embedder = embedder
        elif use_openai_if_available and os.environ.get("OPENAI_API_KEY"):
            try:
                self._embedder = OpenAIEmbedder()
            except RuntimeError:
                self._embedder = HashEmbedder()
        else:
            self._embedder = HashEmbedder()
        self._cache: dict[str, list[float]] = {}

    def embed_skill(self, skill: Skill) -> list[float]:
        """Return a cached embedding for ``skill`` keyed by its name."""
        if skill.name in self._cache:
            return self._cache[skill.name]
        vec = self._embedder.embed(skill.description)
        self._cache[skill.name] = vec
        return vec

    def embed_query(self, text: str) -> list[float]:
        """Embed a raw user query (never cached)."""
        return self._embedder.embed(text)

    def clear_cache(self) -> None:
        """Remove every cached skill embedding."""
        self._cache.clear()
