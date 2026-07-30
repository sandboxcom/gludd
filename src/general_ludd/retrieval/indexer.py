"""Codebase indexer for G3 semantic retrieval."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

from general_ludd.security.safe_diskcache import open_safe_diskcache

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = ".gludd/retrieval_cache"
MAX_CHUNK_CHARS = 2000


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text.lower())
    stripped = [t.strip("_") for t in tokens]
    return [t for t in stripped if len(t) > 1]


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    dot = sum(vec_a.get(k, 0.0) * b_w for k, b_w in vec_b.items())
    norm_a = sum(w * w for w in vec_a.values()) ** 0.5
    norm_b = sum(w * w for w in vec_b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class CodebaseIndexer:
    """Indexes source files for semantic codebase retrieval."""

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> None:
        path = os.path.expanduser(os.path.expandvars(str(cache_dir)))
        os.makedirs(path, mode=0o700, exist_ok=True)
        self._cache_dir = path
        self._cache: Any = open_safe_diskcache(path)

    def index_files(self, paths: list[Path], *, batch_size: int = 64) -> dict[str, Any]:
        files_indexed = 0
        chunks_indexed = 0
        errors = 0

        for filepath in paths:
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                errors += 1
                continue

            chunks = self._chunk_file(filepath, content)
            for i, chunk_text in enumerate(chunks):
                tokens = _tokenize(chunk_text)
                if not tokens:
                    continue
                word_counts = dict(Counter(tokens))
                chunk_key = f"{filepath}:{i}"
                self._cache[chunk_key] = {
                    "filepath": str(filepath),
                    "chunk_id": i,
                    "content": chunk_text,
                    "vector": word_counts,
                }
                chunks_indexed += 1
            files_indexed += 1

        return {
            "files_indexed": files_indexed,
            "chunks_indexed": chunks_indexed,
            "errors": errors,
        }

    def _chunk_file(self, filepath: Path, content: str) -> list[str]:
        suffix = filepath.suffix.lower()
        if suffix == ".py":
            return self._chunk_python(content)
        return self._chunk_generic(content)

    def _chunk_python(self, content: str) -> list[str]:
        try:
            from general_ludd.code_intelligence.extractor import ASTBlockExtractor

            extractor = ASTBlockExtractor()
            blocks = extractor.extract_blocks(content, language="python")
            if blocks:
                return [cast(str, b["source"]) for b in blocks]
        except Exception as exc:
            logger.debug("tree-sitter chunking unavailable: %s", exc)
        return self._chunk_generic(content)

    def _chunk_generic(self, content: str) -> list[str]:
        raw_chunks = re.split(r"\n\s*\n", content)
        result: list[str] = []
        for chunk in raw_chunks:
            stripped = chunk.strip()
            if not stripped:
                continue
            if len(stripped) <= MAX_CHUNK_CHARS:
                result.append(stripped)
            else:
                lines = stripped.split("\n")
                current = ""
                for line in lines:
                    if current and len(current) + len(line) + 1 > MAX_CHUNK_CHARS:
                        result.append(current.strip())
                        current = line
                    else:
                        current = current + "\n" + line if current else line
                if current.strip():
                    result.append(current.strip())
        return result

    def close(self) -> None:
        self._cache.close()

    @property
    def cache_dir(self) -> str:
        return self._cache_dir
