"""Embedding/RAG similarity API: POST /api/embeddings/similar.

Read-only surface over the canonical task-type embedding layer the
:class:`~general_ludd.scoring.router.AdaptiveRouter` already uses (Tier 2 RAG
routing). A role/playbook can ask "find the task types most similar to this
work description" so it can borrow a good model/prompt from a neighboring task
type.

v1 is canonical-task-types-only: the store holds the 10 canonical
:class:`~general_ludd.schemas.benchmark.TaskType` descriptions. The handler:

  - opens a session from ``app.state._session_factory``;
  - builds a :class:`~general_ludd.scoring.task_embeddings.TaskEmbeddingStore`
    with the default embedder (HashEmbedder offline, OpenAIEmbedder only when
    ``OPENAI_API_KEY`` is set);
  - seeds/loads the canonical vectors via ``ensure_embeddings``;
  - embeds the query text with the same embedder;
  - ranks every canonical type by cosine similarity (reusing
    :func:`~general_ludd.skills.embeddings.cosine_similarity`).

It is defensive: an empty/unseeded store, an embedder mismatch, or any
internal failure yields a 200 with empty ``results`` and the resolved
``embedding_method`` — never a 500. Bad input is rejected by pydantic (422).

PSK auth is applied by the daemon middleware (path is not public), exactly as
for /api/facts and /api/environment.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from general_ludd.scoring.task_embeddings import TaskEmbeddingStore
from general_ludd.skills.embeddings import OpenAIEmbedder, cosine_similarity

logger = logging.getLogger(__name__)


class EmbeddingSimilarRequest(BaseModel):
    """Request body for POST /api/embeddings/similar."""

    text: str = Field(..., description="The work description to match against.")
    top_k: int = Field(
        5, ge=1, le=20, description="Number of similar task types to return."
    )
    work_type: str | None = Field(
        None,
        description="Optional filter: restrict results to this task-type value.",
    )
    include_embedding: bool = Field(
        False,
        description="When true, the query embedding vector is returned.",
    )


class SimilarTaskResult(BaseModel):
    """One ranked canonical task type."""

    task_type: str
    similarity_score: float
    canonical_text: str
    embedding_dim: int


class EmbeddingSimilarResponse(BaseModel):
    """Response body for POST /api/embeddings/similar."""

    query_embedding: list[float] | None = None
    query_embedding_dim: int = 0
    results: list[SimilarTaskResult] = Field(default_factory=list)
    embedding_method: str = "hash"


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _embedding_method(store: TaskEmbeddingStore) -> str:
    """Report which embedder backend the store resolved to ("openai"/"hash")."""
    embedder = getattr(store, "_embedder", None)
    if isinstance(embedder, OpenAIEmbedder):
        return "openai"
    return "hash"


async def _similar(
    app: FastAPI, req: EmbeddingSimilarRequest
) -> EmbeddingSimilarResponse:
    """Core handler: seed/load canonical vectors, embed the query, rank.

    Defensive throughout — any failure (no session factory, unseeded store,
    embedder error, dimension mismatch) degrades to an empty result set with
    the resolved method rather than raising.
    """
    factory = _get_session_factory(app)
    if factory is None:
        return EmbeddingSimilarResponse(embedding_method="hash")

    try:
        async with factory() as session:
            store = TaskEmbeddingStore(session, embedder=None)
            method = _embedding_method(store)
            try:
                await store.ensure_embeddings()
            except Exception as exc:  # degrade, never 500
                logger.debug("ensure_embeddings failed: %s", exc)
                return EmbeddingSimilarResponse(embedding_method=method)

            query_vec = store._embedder.embed(req.text)
            query_dim = len(query_vec)

            rows_by_type = await store._load_rows_by_type()
            results: list[SimilarTaskResult] = []
            for task_type, row in rows_by_type.items():
                if req.work_type is not None and task_type != req.work_type:
                    continue
                if row.dim == 0 or row.embedding in ("", "[]"):
                    continue
                try:
                    other_vec = json.loads(row.embedding)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if len(other_vec) != query_dim:
                    continue
                score = cosine_similarity(query_vec, other_vec)
                results.append(
                    SimilarTaskResult(
                        task_type=task_type,
                        similarity_score=score,
                        canonical_text=row.canonical_text or "",
                        embedding_dim=row.dim,
                    )
                )

            results.sort(key=lambda r: r.similarity_score, reverse=True)
            results = results[: req.top_k]

            return EmbeddingSimilarResponse(
                query_embedding=query_vec if req.include_embedding else None,
                query_embedding_dim=query_dim,
                results=results,
                embedding_method=method,
            )
    except Exception as exc:  # never 500 on store/IO failure
        logger.debug("embeddings similarity failed: %s", exc)
        return EmbeddingSimilarResponse(embedding_method="hash")


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.post("/api/embeddings/similar", response_model=EmbeddingSimilarResponse)
    async def api_embeddings_similar(
        req: EmbeddingSimilarRequest,
    ) -> EmbeddingSimilarResponse:
        """Rank canonical task types by similarity to a work description.

        Read-only. v1 covers the 10 canonical task types only. Returns the
        ranked matches (highest cosine similarity first), the query embedding
        dimensionality, the resolved embedding method, and — when
        ``include_embedding`` is set — the query vector itself.
        """
        return await _similar(app, req)
