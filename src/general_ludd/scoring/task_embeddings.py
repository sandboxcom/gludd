"""Canonical task-type embeddings for Tier 2 RAG routing (Step 3).

The adaptive router (``scoring/router.py``) historically treated each task type
as an independent 10-arm bandit: a sparse history for ``bug_fix`` told it
nothing about ``debugging``. Step 3 of the Tier 2 RAG plan fixes that by giving
every :class:`~general_ludd.schemas.benchmark.TaskType` a canonical semantic
embedding. Cosine similarity between task types then turns the flat bandit into
a soft cluster — a historical result for a neighbor task can weight the current
decision when direct history is thin.

This module owns two things:

- :data:`CANONICAL_TASK_DESCRIPTIONS` — the stable, hand-written description for
  each task type. These are the embedding substrate; changing them invalidates
  stored vectors, so they are intentionally explicit and descriptive.
- :class:`TaskEmbeddingStore` — the lazy read/seed store. On
  :meth:`~TaskEmbeddingStore.ensure_embeddings` it loads every
  :class:`~general_ludd.db.models.TaskEmbeddingModel` row, seeds any missing
  task types from the canonical map, and embeds any row whose stored vector is
  empty (``dim == 0`` / ``embedding == "[]"``). Existing vectors are preserved
  so a re-run never recomputes paid-for embeddings.

The embedder mirrors :class:`~general_ludd.skills.embeddings.SkillEmbedder`'s
selection rule: :class:`~general_ludd.skills.embeddings.HashEmbedder` by default
(no external deps), :class:`~general_ludd.skills.embeddings.OpenAIEmbedder` when
``OPENAI_API_KEY`` is available, and an explicit ``embedder`` argument always
wins.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from sqlalchemy import select

from general_ludd.db.models import TaskEmbeddingModel
from general_ludd.schemas.benchmark import TaskType
from general_ludd.skills.embeddings import (
    Embedder,
    HashEmbedder,
    OpenAIEmbedder,
    cosine_similarity,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


CANONICAL_TASK_DESCRIPTIONS: dict[TaskType, str] = {
    TaskType.BUG_FIX: (
        "Fix a defect: diagnose a reported failure, isolate the root cause, "
        "and apply a minimal code change that resolves the incorrect behavior "
        "while preserving existing functionality."
    ),
    TaskType.FEATURE: (
        "Implement a new product feature from a specification: add new "
        "functionality, wire it through the relevant layers, and cover it "
        "with tests that verify the intended user-facing behavior."
    ),
    TaskType.REFACTOR: (
        "Refactor existing code to improve structure, readability, or "
        "design without changing observable behavior: extract abstractions, "
        "reduce duplication, rename for clarity, and keep the test suite green."
    ),
    TaskType.TEST_WRITE: (
        "Write unit, integration, or end-to-end tests for existing or new "
        "functionality: cover edge cases, assert expected behavior, and "
        "establish a regression safety net."
    ),
    TaskType.CODE_REVIEW: (
        "Review a pull request or diff for correctness, style, security, "
        "performance, and maintainability: surface defects, suggest "
        "improvements, and verify adherence to project conventions."
    ),
    TaskType.DOCUMENTATION: (
        "Write or update documentation: reference material, guides, API docs, "
        "READMEs, or inline comments that explain how code works and how to "
        "use it effectively."
    ),
    TaskType.DEBUGGING: (
        "Debug a problem: reproduce the failure, trace execution, inspect "
        "state, form and test hypotheses about the cause, and converge on a "
        "diagnosis that a fix can target."
    ),
    TaskType.OPTIMIZATION: (
        "Optimize code for performance, memory, or resource usage: profile "
        "the current behavior, identify the bottleneck, and apply a change "
        "that measurably improves throughput, latency, or footprint without "
        "trading away correctness."
    ),
    TaskType.SECURITY_FIX: (
        "Remediate a security vulnerability: patch the flaw, harden the "
        "affected code path against the relevant threat model, and verify "
        "the fix with regression tests that reproduce the exploit attempt."
    ),
    TaskType.INTEGRATION: (
        "Integrate two or more systems or services: wire APIs, exchange data "
        "across boundaries, reconcile schemas or contracts, and validate the "
        "end-to-end interaction with tests."
    ),
}


def _select_default_embedder() -> Embedder:
    """Pick the default embedder, mirroring SkillEmbedder's selection rule.

    HashEmbedder unless ``OPENAI_API_KEY`` is set and the OpenAIEmbedder
    constructs successfully; any RuntimeError falls back to HashEmbedder so
    the store is always usable offline.
    """
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return OpenAIEmbedder()
        except RuntimeError:
            return HashEmbedder()
    return HashEmbedder()


class TaskEmbeddingStore:
    """Lazy read/seed store of canonical task-type embeddings.

    Wraps an async SQLAlchemy session plus a pluggable :class:`Embedder`.
    :meth:`ensure_embeddings` is idempotent: it only embeds rows whose stored
    vector is empty, so re-running it is cheap and never overwrites paid-for
    vectors. :meth:`similarity_to` reads the cached vectors back and returns a
    cosine-similarity map over the other task types.
    """

    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder | None = None,
    ) -> None:
        self._session = session
        if embedder is not None:
            self._embedder: Embedder = embedder
        else:
            self._embedder = _select_default_embedder()

    async def ensure_embeddings(self) -> None:
        """Seed any missing task types and embed any empty vectors.

        Idempotent. For each canonical task type with no row, a row is created
        with the canonical text and embedded. Existing rows whose ``embedding``
        is still the default empty list (``dim == 0``) are embedded from their
        ``canonical_text`` (the canonical description is used when the row has
        no text). Rows that already hold a non-empty vector are left untouched.
        """
        rows_by_type = await self._load_rows_by_type()

        for task_type, canonical_text in CANONICAL_TASK_DESCRIPTIONS.items():
            row = rows_by_type.get(task_type.value)
            if row is None:
                vec = self._embedder.embed(canonical_text)
                row = TaskEmbeddingModel(
                    task_type=task_type.value,
                    canonical_text=canonical_text,
                    embedding=json.dumps(vec),
                    dim=len(vec),
                )
                self._session.add(row)
                rows_by_type[task_type.value] = row
            elif _is_empty_vector(row):
                text = row.canonical_text or canonical_text
                vec = self._embedder.embed(text)
                row.canonical_text = text
                row.embedding = json.dumps(vec)
                row.dim = len(vec)

        await self._session.flush()

    async def similarity_to(
        self, task_type: TaskType | str
    ) -> dict[str, float]:
        """Cosine similarity of ``task_type`` against every other embedded type.

        Returns a ``{other_task_type: cosine_similarity}`` dict. The query type
        itself is excluded. Rows without a usable embedding, or whose vector
        dimensionality differs from the query's (e.g. a stale row embedded with
        a different embedder), are skipped — the router consumes partial
        similarity maps without crashing.

        Raises :class:`KeyError` if ``task_type`` has no row or no embedded
        vector, since a missing query vector is a caller bug, not a partial
        state to paper over.
        """
        rows_by_type = await self._load_rows_by_type()
        query_row = rows_by_type.get(_task_type_value(task_type))
        if query_row is None or _is_empty_vector(query_row):
            raise KeyError(_task_type_value(task_type))

        query_vec = json.loads(query_row.embedding)
        query_dim = len(query_vec)

        sims: dict[str, float] = {}
        for other_type, row in rows_by_type.items():
            if other_type == query_row.task_type:
                continue
            if _is_empty_vector(row):
                continue
            other_vec = json.loads(row.embedding)
            if len(other_vec) != query_dim:
                continue
            sims[other_type] = cosine_similarity(query_vec, other_vec)
        return sims

    async def _load_rows_by_type(self) -> dict[str, TaskEmbeddingModel]:
        result = await self._session.execute(select(TaskEmbeddingModel))
        return {row.task_type: row for row in result.scalars().all()}


def _is_empty_vector(row: TaskEmbeddingModel) -> bool:
    """True if the row has no embedded vector yet (needs seeding)."""
    return row.dim == 0 or row.embedding in ("", "[]")


def _task_type_value(task_type: TaskType | str) -> str:
    """Normalize a TaskType | str to its string value for DB lookups."""
    return task_type.value if isinstance(task_type, TaskType) else task_type
