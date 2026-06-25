"""Embedding/RAG similarity API: POST /api/embeddings/similar and /compare.

Read-only surface over the canonical task-type embedding layer the
:class:`~general_ludd.scoring.router.AdaptiveRouter` already uses (Tier 2 RAG
routing). A role/playbook can ask "find the task types most similar to this
work description" so it can borrow a good model/prompt from a neighboring task
type.

``POST /api/embeddings/compare`` is a second, complementary surface: it takes
two arbitrary strings (``text_a``/``text_b``) produced by separate
bots/agents and returns their pairwise cosine similarity, so a role can decide
how to proceed (near-duplicate -> merge/dedupe; divergent -> escalate). A batch
form (``texts``) instead returns the full symmetric pairwise similarity matrix.
Both reuse the SAME default-embedder selection as ``/similar`` so the score is
comparable across surfaces.

``POST /api/embeddings/search`` is the generic RAG-search surface: a role takes
a string a bot produced and searches a real corpus with it, getting back the
top-k most-similar items ranked by cosine similarity. v1 ships two real
corpora — ``skills`` (the live :class:`~general_ludd.skills.registry.SkillRegistry`
on ``app.state._skill_registry``; each skill's ``description`` is embedded
on-the-fly, no schema change) and ``task_types`` (delegates to the same
canonical task-type logic as ``/similar``). Any other ``corpus`` value is
rejected by pydantic (422). It reuses the SAME default embedder as ``/similar``
and ``/compare`` so the scores are comparable. Fully defensive: an empty/absent
registry or any embed failure yields a 200 with empty ``results`` — never a 500.

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
from pydantic import BaseModel, Field, model_validator

from general_ludd.scoring.task_embeddings import (
    TaskEmbeddingStore,
    _select_default_embedder,
)
from general_ludd.skills.embeddings import (
    Embedder,
    OpenAIEmbedder,
    cosine_similarity,
)

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


class EmbeddingCompareRequest(BaseModel):
    """Request body for POST /api/embeddings/compare.

    Two mutually-exclusive forms are accepted:

    - the pairwise form: ``text_a`` AND ``text_b`` (both required) -> the
      single cosine similarity between the two strings;
    - the batch form: ``texts`` (a list of >= 2 strings) -> the full symmetric
      pairwise similarity matrix among them.

    Exactly one form must be supplied; anything else is rejected (422).
    """

    text_a: str | None = Field(
        None, description="First string to compare (pairwise form)."
    )
    text_b: str | None = Field(
        None, description="Second string to compare (pairwise form)."
    )
    texts: list[str] | None = Field(
        None,
        description=(
            "Batch form: 2+ strings; returns the pairwise similarity matrix."
        ),
    )
    include_embeddings: bool = Field(
        False,
        description="When true, the computed embedding vectors are returned.",
    )

    @model_validator(mode="after")
    def _check_form(self) -> EmbeddingCompareRequest:
        has_pair = self.text_a is not None and self.text_b is not None
        has_batch = self.texts is not None and len(self.texts) >= 2
        if has_pair == has_batch:
            raise ValueError(
                "supply either (text_a AND text_b) OR texts with len >= 2"
            )
        return self


class EmbeddingCompareResponse(BaseModel):
    """Response body for POST /api/embeddings/compare.

    For the pairwise form, ``similarity`` is set and ``matrix`` is None. For the
    batch form, ``matrix`` is the symmetric pairwise matrix (1.0 on the
    diagonal) and ``similarity`` is None.
    """

    similarity: float | None = None
    matrix: list[list[float]] | None = None
    embedding_method: str = "hash"
    dim: int = 0
    embeddings: list[list[float]] | None = None


class EmbeddingSearchRequest(BaseModel):
    """Request body for POST /api/embeddings/search.

    ``corpus`` is constrained (pattern) to the two REAL v1 corpora: ``skills``
    (the live SkillRegistry, descriptions embedded on the fly) and
    ``task_types`` (the canonical task-type vectors, delegating to /similar).
    Any other value (memory/todos are future) is rejected by pydantic (422).
    """

    text: str = Field(..., description="The query string to search the corpus with.")
    corpus: str = Field(
        "skills",
        pattern="^(skills|task_types)$",
        description="Which real corpus to search: 'skills' or 'task_types'.",
    )
    top_k: int = Field(
        5, ge=1, le=20, description="Number of corpus items to return."
    )
    include_embeddings: bool = Field(
        False,
        description="When true, the query embedding vector is returned.",
    )


class SearchResultItem(BaseModel):
    """One ranked corpus item."""

    rank: int
    name: str
    source_text: str
    similarity_score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingSearchResponse(BaseModel):
    """Response body for POST /api/embeddings/search."""

    corpus: str = "skills"
    query_embedding_dim: int = 0
    embedding_method: str = "hash"
    query_embedding: list[float] | None = None
    results: list[SearchResultItem] = Field(default_factory=list)


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


def _method_of(embedder: Embedder | None) -> str:
    """Report which embedder backend was resolved ("openai"/"hash")."""
    if isinstance(embedder, OpenAIEmbedder):
        return "openai"
    return "hash"


def _compare(req: EmbeddingCompareRequest) -> EmbeddingCompareResponse:
    """Embed the supplied strings and compute pairwise cosine similarity.

    Uses the SAME default-embedder selection as ``/similar`` (via
    :func:`_select_default_embedder`) so scores are comparable across surfaces.
    Fully defensive: any embedder/IO failure degrades to a 200 with
    ``similarity=None``/empty matrix and the resolved method — never a 500.
    Validation (which form was supplied) is enforced by the pydantic model, so
    bad input is a 422 before this handler runs.
    """
    try:
        embedder = _select_default_embedder()
    except Exception as exc:  # never 500 on embedder construction
        logger.debug("embedder selection failed: %s", exc)
        return EmbeddingCompareResponse(similarity=None, embedding_method="hash")

    method = _method_of(embedder)

    try:
        if req.texts is not None:
            vectors = [embedder.embed(t) for t in req.texts]
            dim = len(vectors[0]) if vectors else 0
            n = len(vectors)
            matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
            for i in range(n):
                matrix[i][i] = 1.0
                for j in range(i + 1, n):
                    score = cosine_similarity(vectors[i], vectors[j])
                    matrix[i][j] = score
                    matrix[j][i] = score
            return EmbeddingCompareResponse(
                similarity=None,
                matrix=matrix,
                embedding_method=method,
                dim=dim,
                embeddings=vectors if req.include_embeddings else None,
            )

        # Pairwise form — text_a/text_b guaranteed non-None by the validator.
        vec_a = embedder.embed(req.text_a or "")
        vec_b = embedder.embed(req.text_b or "")
        similarity = cosine_similarity(vec_a, vec_b)
        return EmbeddingCompareResponse(
            similarity=similarity,
            matrix=None,
            embedding_method=method,
            dim=len(vec_a),
            embeddings=[vec_a, vec_b] if req.include_embeddings else None,
        )
    except Exception as exc:  # never 500 on embed/similarity failure
        logger.debug("embeddings compare failed: %s", exc)
        empty_matrix: list[list[float]] | None = (
            [] if req.texts is not None else None
        )
        return EmbeddingCompareResponse(
            similarity=None,
            matrix=empty_matrix,
            embedding_method=method,
        )


async def _search_task_types(
    app: FastAPI, req: EmbeddingSearchRequest
) -> EmbeddingSearchResponse:
    """corpus=task_types: delegate to the canonical /similar ranking.

    Reuses :func:`_similar` verbatim (the canonical task-type vectors) and
    adapts its rows to the generic ``SearchResultItem`` shape so the search
    surface stays uniform across corpora.
    """
    similar = await _similar(
        app,
        EmbeddingSimilarRequest(
            text=req.text,
            top_k=req.top_k,
            work_type=None,
            include_embedding=req.include_embeddings,
        ),
    )
    results = [
        SearchResultItem(
            rank=i + 1,
            name=r.task_type,
            source_text=r.canonical_text,
            similarity_score=r.similarity_score,
            metadata={"embedding_dim": r.embedding_dim},
        )
        for i, r in enumerate(similar.results)
    ]
    return EmbeddingSearchResponse(
        corpus="task_types",
        query_embedding_dim=similar.query_embedding_dim,
        embedding_method=similar.embedding_method,
        query_embedding=similar.query_embedding,
        results=results,
    )


def _search_skills(req: EmbeddingSearchRequest, registry: Any) -> EmbeddingSearchResponse:
    """corpus=skills: embed each skill description on the fly, cosine-rank.

    The skill corpus has no stored vectors (zero schema change): the live
    SkillRegistry's ``list_skills()`` is read, each ``Skill.description`` is
    embedded with the SAME default embedder as /similar and /compare, and the
    query is ranked against them. An absent registry, an empty skill set, or
    any embed failure degrades to an empty result set (the caller wraps this so
    the route is a 200, never a 500).
    """
    embedder = _select_default_embedder()
    method = _method_of(embedder)

    query_vec = embedder.embed(req.text)
    query_dim = len(query_vec)

    skills: list[Any] = []
    if registry is not None and hasattr(registry, "list_skills"):
        try:
            skills = list(registry.list_skills())
        except Exception as exc:  # degrade, never 500
            logger.debug("skill listing failed: %s", exc)
            skills = []

    scored: list[SearchResultItem] = []
    for skill in skills:
        description = getattr(skill, "description", "") or ""
        if not description:
            continue
        try:
            skill_vec = embedder.embed(description)
        except Exception as exc:  # skip the bad one, keep ranking the rest
            logger.debug("skill embed failed: %s", exc)
            continue
        if len(skill_vec) != query_dim:
            continue
        score = cosine_similarity(query_vec, skill_vec)
        scored.append(
            SearchResultItem(
                rank=0,  # assigned after sort
                name=getattr(skill, "name", ""),
                source_text=description,
                similarity_score=score,
                metadata={
                    "category": getattr(skill, "category", "") or "",
                    "tags": list(getattr(skill, "tags", []) or []),
                },
            )
        )

    scored.sort(key=lambda r: r.similarity_score, reverse=True)
    scored = scored[: req.top_k]
    for i, item in enumerate(scored):
        item.rank = i + 1

    return EmbeddingSearchResponse(
        corpus="skills",
        query_embedding_dim=query_dim,
        embedding_method=method,
        query_embedding=query_vec if req.include_embeddings else None,
        results=scored,
    )


async def _search(
    app: FastAPI, req: EmbeddingSearchRequest
) -> EmbeddingSearchResponse:
    """Dispatch a corpus search; never 500s (degrades to empty results)."""
    if req.corpus == "task_types":
        try:
            return await _search_task_types(app, req)
        except Exception as exc:  # never 500
            logger.debug("task_types search failed: %s", exc)
            return EmbeddingSearchResponse(corpus="task_types")
    # corpus == "skills" (pydantic guarantees one of the two).
    try:
        registry = getattr(app.state, "_skill_registry", None)
        return _search_skills(req, registry)
    except Exception as exc:  # never 500
        logger.debug("skills search failed: %s", exc)
        return EmbeddingSearchResponse(corpus="skills")


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

    @app.post(
        "/api/embeddings/compare",
        response_model=EmbeddingCompareResponse,
        summary="Compare two strings (or a batch) by embedding similarity",
        description=(
            "Embed two arbitrary strings produced by separate bots/agents and "
            "return their pairwise cosine similarity so a role can decide how "
            "to proceed (near-duplicate -> merge/dedupe; divergent -> "
            "escalate). Supply `text_a` AND `text_b` for the single-pair form "
            "(sets `similarity`), or `texts` (2+) for the batch form (sets the "
            "symmetric `matrix`, 1.0 on the diagonal). Reuses the same default "
            "embedder as /similar; `embedding_method` is \"openai\" or \"hash\". "
            "Defensive: failures degrade to 200 with no score; bad input -> 422."
        ),
    )
    async def api_embeddings_compare(
        req: EmbeddingCompareRequest,
    ) -> EmbeddingCompareResponse:
        """Compute pairwise embedding similarity between strings.

        Read-only. Either ``text_a``+``text_b`` (single similarity) or
        ``texts`` (pairwise matrix). Never 500s on embedder failure.
        """
        return _compare(req)

    @app.post(
        "/api/embeddings/search",
        response_model=EmbeddingSearchResponse,
        summary="Search a real corpus (skills/task_types) by embedding similarity",
        description=(
            "Take a string a bot produced and search a real corpus with it (RAG "
            "search): returns the top_k most-similar corpus items ranked by "
            "cosine similarity. v1 corpora: `skills` (the live SkillRegistry — "
            "each skill description is embedded on the fly, no schema change) and "
            "`task_types` (the canonical task-type vectors, delegating to the "
            "/similar logic). Any other `corpus` value is rejected (422). Reuses "
            "the same default embedder as /similar and /compare; "
            "`embedding_method` is \"openai\" or \"hash\". Defensive: an absent/"
            "empty corpus or any embed failure degrades to a 200 with empty "
            "`results`; bad input -> 422."
        ),
    )
    async def api_embeddings_search(
        req: EmbeddingSearchRequest,
    ) -> EmbeddingSearchResponse:
        """Search a real corpus for the items most similar to ``text``.

        Read-only. ``corpus`` is ``skills`` (default) or ``task_types``. Returns
        the ranked matches (highest cosine similarity first), the query
        embedding dimensionality, the resolved embedding method, and — when
        ``include_embeddings`` is set — the query vector itself. Never 500s.
        """
        return await _search(app, req)
