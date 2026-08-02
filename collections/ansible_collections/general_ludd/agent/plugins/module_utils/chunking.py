"""
Chunking strategies for document splitting.

Three strategies are provided:
- SlidingWindowStrategy: overlapping sliding-window chunks.
- SentenceBoundaryStrategy: split on sentence boundaries (``. ! ?``),
  respecting a maximum chunk size.
- FixedSizeStrategy: exact-size split with no overlap.

Usage::

    from ansible_collections.general_ludd.agent.plugins.module_utils.chunking import (
        ChunkingStrategy,
        SlidingWindowStrategy,
        SentenceBoundaryStrategy,
        FixedSizeStrategy,
    )

    strategy = SlidingWindowStrategy(chunk_size=500, overlap=100)
    chunks = strategy.chunk("long document text...")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Chunk:
    """A single text chunk with metadata."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    index: int = 0


class ChunkingStrategy(Protocol):
    """Protocol for chunking strategies."""

    def chunk(self, text: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        """Split ``text`` into a list of :class:`Chunk` objects."""
        ...


class SlidingWindowStrategy:
    """Split text into overlapping sliding-window chunks.

    Parameters
    ----------
    chunk_size:
        Target size of each chunk in characters.
    overlap:
        Number of characters each chunk overlaps with the next.
    """

    def __init__(self, chunk_size: int = 1000, overlap: int = 200) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0:
            raise ValueError("overlap must be non-negative")
        if overlap >= chunk_size:
            raise ValueError("overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        if not text:
            return []

        meta = metadata or {}
        step = self.chunk_size - self.overlap
        chunks: list[Chunk] = []

        for idx, start in enumerate(range(0, len(text), step)):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append(Chunk(text=chunk_text, metadata=dict(meta), index=idx))

        return chunks


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class SentenceBoundaryStrategy:
    """Split text on sentence boundaries, respecting a maximum chunk size.

    Sentences are split on ``. ! ?`` followed by whitespace.  If a single
    sentence exceeds ``max_chunk_size`` it is force-split at the limit.

    Parameters
    ----------
    max_chunk_size:
        Maximum characters per chunk.  Sentences are accumulated until
        adding the next would exceed this limit.
    """

    def __init__(self, max_chunk_size: int = 1000) -> None:
        if max_chunk_size <= 0:
            raise ValueError("max_chunk_size must be positive")
        self.max_chunk_size = max_chunk_size

    @staticmethod
    def _force_split(text: str, size: int) -> list[str]:
        pieces: list[str] = []
        for start in range(0, len(text), size):
            pieces.append(text[start : start + size])
        return pieces

    def chunk(self, text: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        if not text:
            return []

        meta = metadata or {}
        sentences = _SENTENCE_RE.split(text)

        chunks: list[Chunk] = []
        current: list[str] = []
        current_len = 0

        def flush_current() -> None:
            nonlocal current, current_len
            if current:
                chunks.append(
                    Chunk(
                        text=" ".join(current),
                        metadata=dict(meta),
                        index=len(chunks),
                    )
                )
                current = []
                current_len = 0

        for sentence in sentences:
            s = sentence.strip()
            if not s:
                continue

            s_len = len(s)

            if s_len > self.max_chunk_size:
                flush_current()
                for piece in self._force_split(s, self.max_chunk_size):
                    chunks.append(Chunk(text=piece, metadata=dict(meta), index=len(chunks)))
                continue

            if current and current_len + 1 + s_len > self.max_chunk_size:
                flush_current()

            current.append(s)
            current_len += s_len

        flush_current()
        return chunks


class FixedSizeStrategy:
    """Split text into exact-size chunks with no overlap.

    Parameters
    ----------
    chunk_size:
        Exact size of each chunk in characters.
    """

    def __init__(self, chunk_size: int = 500) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    def chunk(self, text: str, metadata: dict[str, Any] | None = None) -> list[Chunk]:
        if not text:
            return []

        meta = metadata or {}
        chunks: list[Chunk] = []

        for idx, start in enumerate(range(0, len(text), self.chunk_size)):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            chunks.append(Chunk(text=chunk_text, metadata=dict(meta), index=idx))

        return chunks
