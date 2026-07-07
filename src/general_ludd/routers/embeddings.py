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
top-k most-similar items ranked by cosine similarity. It ships four real
corpora — ``skills`` (the live :class:`~general_ludd.skills.registry.SkillRegistry`
on ``app.state._skill_registry``; each skill's ``description`` is embedded
on-the-fly, no schema change), ``task_types`` (delegates to the same canonical
task-type logic as ``/similar``), ``prompts`` (the persisted
:class:`~general_ludd.db.models.PromptProfileModel` corpus; each ``prompt_text``
is embedded on-the-fly, no schema change), and ``traces`` (the in-process
:class:`~general_ludd.observability.trace_store.RecentTracesBuffer` on
``app.state._recent_traces``; each recent execution trace's work_type + phase
labels + span descriptions are concatenated and embedded on-the-fly, no schema
change). Any other ``corpus`` value is rejected by pydantic (422). It reuses
the SAME default embedder as ``/similar``
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

import asyncio
import json
import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

# Cap on how many top-level keys of an audit event's ``details`` JSON contribute
# to its searchable text. ``AuditEventModel.details`` is an unconstrained Text
# column, so a runaway/malicious payload could otherwise carry an unbounded
# number of keys; combined with the 500-char per-value cap this bounds the
# source_text (and thus per-event embed cost/memory) of an events search.
_MAX_DETAILS_KEYS = 100


class EmbeddingSimilarRequest(BaseModel):
    """Request body for POST /api/embeddings/similar."""

    text: str = Field(
        ...,
        max_length=20000,
        description="The work description to match against (max 20000 chars).",
    )
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
        None,
        max_length=20000,
        description="First string to compare (pairwise form; max 20000 chars).",
    )
    text_b: str | None = Field(
        None,
        max_length=20000,
        description="Second string to compare (pairwise form; max 20000 chars).",
    )
    texts: list[str] | None = Field(
        None,
        max_length=100,
        description=(
            "Batch form: 2+ strings (max 100); returns the pairwise similarity "
            "matrix. Each string is capped at 20000 chars."
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
        # Bound each batch string so a single request can't embed an
        # unbounded amount of text (O(n) embed cost / per-string blow-up).
        if self.texts is not None:
            for item in self.texts:
                if len(item) > 20000:
                    raise ValueError(
                        "each item in texts must be <= 20000 chars"
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

    ``corpus`` is constrained (pattern) to the REAL corpora: ``skills``
    (the live SkillRegistry, descriptions embedded on the fly), ``task_types``
    (the canonical task-type vectors, delegating to /similar), ``prompts``
    (the persisted :class:`PromptProfileModel.prompt_text` corpus, embedded on
    the fly — no schema change), ``traces`` (the in-process
    RecentTracesBuffer; each recent execution trace's work_type/phase
    labels/span descriptions are embedded on the fly — no schema change), and
    ``events`` (the persisted :class:`~general_ludd.db.models.AuditEventModel`
    corpus; each recent audit event's event_type + entity_type + a human
    summary of its ``details`` JSON is embedded on the fly — no schema change).
    object other value (metrics/system/osquery are later phases) is rejected by
    pydantic (422).
    """

    text: str = Field(
        ...,
        max_length=20000,
        description="The query string to search the corpus with (max 20000 chars).",
    )
    corpus: str = Field(
        "skills",
        pattern="^(skills|task_types|prompts|traces|events)$",
        description=(
            "Which real corpus to search: 'skills', 'task_types', 'prompts', "
            "'traces', or 'events'."
        ),
    )
    top_k: int = Field(
        5, ge=1, le=20, description="Number of corpus items to return."
    )
    include_embeddings: bool = Field(
        False,
        description="When true, the query embedding vector is returned.",
    )
    project_id: str | None = Field(
        None,
        description=(
            "When set, restrict the 'events' and 'traces' corpora to this "
            "project only (cross-tenant isolation). None searches all rows "
            "(back-compat); other corpora are unaffected."
        ),
    )


class SearchResultItem(BaseModel):
    """One ranked corpus item."""

    rank: int
    name: str
    source_text: str
    similarity_score: float
    metadata: dict[str, object] = Field(default_factory=dict)


class EmbeddingSearchResponse(BaseModel):
    """Response body for POST /api/embeddings/search."""

    corpus: str = "skills"
    query_embedding_dim: int = 0
    embedding_method: str = "hash"
    query_embedding: list[float] | None = None
    results: list[SearchResultItem] = Field(default_factory=list)


class MultiCorpusSearchRequest(BaseModel):
    """Request body for POST /api/embeddings/search-multi.

    Accepts a list of corpora to search and merges the ranked results across
    all of them into a single sorted result set. Each result item carries a
    ``corpus`` key in its metadata so the caller knows where it came from.
    """

    text: str = Field(
        ...,
        max_length=20000,
        description="The query string to search across corpora (max 20000 chars).",
    )
    corpora: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Which real corpora to search across: 'skills', 'task_types', "
            "'prompts', 'traces', and/or 'events'."
        ),
    )
    top_k: int = Field(
        5, ge=1, le=20, description="Number of merged corpus items to return."
    )
    include_embeddings: bool = Field(
        False,
        description="When true, the query embedding vector is returned.",
    )

    _VALID_CORPORA: frozenset[str] = frozenset(
        {"skills", "task_types", "prompts", "traces", "events"}
    )

    @model_validator(mode="after")
    def _check_corpora(self) -> MultiCorpusSearchRequest:
        for c in self.corpora:
            if c not in self._VALID_CORPORA:
                raise ValueError(f"unknown corpus: {c}")
        return self


class MultiCorpusSearchResponse(BaseModel):
    """Response body for POST /api/embeddings/search-multi."""

    corpora_searched: list[str]
    query_embedding_dim: int = 0
    embedding_method: str = "hash"
    query_embedding: list[float] | None = None
    results: list[SearchResultItem] = Field(default_factory=list)


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _parse_json_list(raw: object) -> list[object]:
    """Defensively decode a JSON-array column value to a list.

    PromptProfileModel stores ``tags``/``task_types`` as JSON-encoded strings
    (defaulting to ``"[]"``). Anything that is not a well-formed JSON array
    (None, malformed text, a non-list payload) degrades to an empty list rather
    than raising, so a single bad row can never break a search.
    """
    if isinstance(raw, list):
        return list(raw)
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


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

            query_vec = await asyncio.to_thread(store._embedder.embed, req.text)
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


def _search_skills(req: EmbeddingSearchRequest, registry: object) -> EmbeddingSearchResponse:
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

    skills: list[object] = []
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


async def _search_prompts(
    app: FastAPI, req: EmbeddingSearchRequest
) -> EmbeddingSearchResponse:
    """corpus=prompts: embed each persisted prompt_text on the fly, cosine-rank.

    Mirrors :func:`_search_skills` exactly but over the persisted
    :class:`~general_ludd.db.models.PromptProfileModel` corpus. No stored vectors
    (zero schema change): a session is opened from ``app.state._session_factory``,
    :class:`~general_ludd.db.repository.PromptProfileRepository`'s ``list_all`` is
    read (capped at ``_DEFAULT_LIST_LIMIT``), each ``prompt_text`` is embedded
    with the SAME default embedder as /similar and /compare, and the query is
    ranked against them. An absent factory, an empty prompt set, or any embed
    failure degrades to an empty result set (the caller wraps this so the route
    is a 200, never a 500). ``tags``/``task_types`` are JSON-decoded defensively.
    """
    from general_ludd.db.repository import (
        _DEFAULT_LIST_LIMIT,
        PromptProfileRepository,
    )

    embedder = _select_default_embedder()
    method = _method_of(embedder)

    query_vec = await asyncio.to_thread(embedder.embed, req.text)
    query_dim = len(query_vec)

    factory = _get_session_factory(app)
    if factory is None:
        return EmbeddingSearchResponse(
            corpus="prompts",
            query_embedding_dim=query_dim,
            embedding_method=method,
            query_embedding=query_vec if req.include_embeddings else None,
            results=[],
        )

    rows: list[object] = []
    async with factory() as session:
        repo = PromptProfileRepository(session)
        try:
            rows = list(await repo.list_all(limit=_DEFAULT_LIST_LIMIT))
        except Exception as exc:  # degrade, never 500
            logger.debug("prompt listing failed: %s", exc)
            rows = []

    scored: list[SearchResultItem] = []
    for row in rows:
        prompt_text = getattr(row, "prompt_text", "") or ""
        if not prompt_text:
            continue
        try:
            prompt_vec = await asyncio.to_thread(embedder.embed, prompt_text)
        except Exception as exc:  # skip the bad one, keep ranking the rest
            logger.debug("prompt embed failed: %s", exc)
            continue
        if len(prompt_vec) != query_dim:
            continue
        score = cosine_similarity(query_vec, prompt_vec)
        scored.append(
            SearchResultItem(
                rank=0,  # assigned after sort
                name=getattr(row, "name", "") or "",
                source_text=prompt_text,
                similarity_score=score,
                metadata={
                    "source": getattr(row, "source", "") or "",
                    "tags": _parse_json_list(getattr(row, "tags", None)),
                    "task_types": _parse_json_list(
                        getattr(row, "task_types", None)
                    ),
                    "version": getattr(row, "version", "") or "",
                },
            )
        )

    scored.sort(key=lambda r: r.similarity_score, reverse=True)
    scored = scored[: req.top_k]
    for i, item in enumerate(scored):
        item.rank = i + 1

    return EmbeddingSearchResponse(
        corpus="prompts",
        query_embedding_dim=query_dim,
        embedding_method=method,
        query_embedding=query_vec if req.include_embeddings else None,
        results=scored,
    )


def _trace_text(trace: dict[str, object]) -> str:
    """Build the searchable text for one snapshot trace row.

    Concatenates the trace's ``work_type`` with each span's phase label and
    description (``name``) into a single searchable string. Only genuinely-
    present fields contribute — nothing is fabricated. An empty/span-less trace
    yields just its work_type (possibly empty), which the caller skips.
    """
    parts: list[str] = []
    work_type = trace.get("work_type") or ""
    if work_type:
        parts.append(str(work_type))
    spans = trace.get("spans")
    if isinstance(spans, list):
        for span in spans:
            if not isinstance(span, dict):
                continue
            phase = span.get("phase") or ""
            name = span.get("name") or ""
            if phase:
                parts.append(str(phase))
            if name:
                parts.append(str(name))
    return " ".join(parts)


def _search_traces(
    app: FastAPI, req: EmbeddingSearchRequest
) -> EmbeddingSearchResponse:
    """corpus=traces: embed each recent execution trace on the fly, cosine-rank.

    Mirrors :func:`_search_prompts` / :func:`_search_skills` exactly but over the
    in-process :class:`~general_ludd.observability.trace_store.RecentTracesBuffer`
    on ``app.state._recent_traces`` (the SAME buffer GET /api/facts and
    /api/traces read). No stored vectors (zero schema change): ``snapshot()`` is
    taken, each trace's searchable text (work_type + phase labels + span
    descriptions, via :func:`_trace_text`) is embedded with the SAME default
    embedder as /similar and /compare, and the query is ranked against them. An
    absent buffer, an empty trace window, or any embed failure degrades to an
    empty result set (the caller wraps this so the route is a 200, never a 500).
    """
    embedder = _select_default_embedder()
    method = _method_of(embedder)

    query_vec = embedder.embed(req.text)
    query_dim = len(query_vec)

    buffer = getattr(app.state, "_recent_traces", None)
    traces: list[dict[str, object]] = []
    if buffer is not None and hasattr(buffer, "snapshot"):
        try:
            # Tenant isolation (XT trace-leak fix): scope the trace corpus to the
            # caller's project so this route cannot leak other tenants' trace
            # ids / todo_ids / costs (the same buffer /api/traces now scopes).
            # None = unscoped/global caller (back-compat); a scoped project also
            # excludes legacy None-project traces.
            snap = buffer.snapshot(project_id=req.project_id)
            recent = snap.get("recent", []) if isinstance(snap, dict) else []
            if isinstance(recent, list):
                traces = [t for t in recent if isinstance(t, dict)]
        except Exception as exc:  # degrade, never 500
            logger.debug("traces snapshot failed: %s", exc)
            traces = []

    scored: list[SearchResultItem] = []
    for trace in traces:
        source_text = _trace_text(trace)
        if not source_text:
            continue
        try:
            trace_vec = embedder.embed(source_text)
        except Exception as exc:  # skip the bad one, keep ranking the rest
            logger.debug("trace embed failed: %s", exc)
            continue
        if len(trace_vec) != query_dim:
            continue
        score = cosine_similarity(query_vec, trace_vec)
        spans = trace.get("spans")
        by_phase = sorted(
            {
                str(s.get("phase"))
                for s in spans
                if isinstance(s, dict) and s.get("phase")
            }
        ) if isinstance(spans, list) else []
        scored.append(
            SearchResultItem(
                rank=0,  # assigned after sort
                name=str(trace.get("trace_id") or ""),
                source_text=source_text,
                similarity_score=score,
                metadata={
                    "todo_id": trace.get("todo_id") or "",
                    "work_type": trace.get("work_type") or "",
                    "total_cost_usd": trace.get("total_cost_usd") or 0.0,
                    "span_count": trace.get(
                        "span_count",
                        len(spans) if isinstance(spans, list) else 0,
                    ),
                    "by_phase": by_phase,
                },
            )
        )

    scored.sort(key=lambda r: r.similarity_score, reverse=True)
    scored = scored[: req.top_k]
    for i, item in enumerate(scored):
        item.rank = i + 1

    return EmbeddingSearchResponse(
        corpus="traces",
        query_embedding_dim=query_dim,
        embedding_method=method,
        query_embedding=query_vec if req.include_embeddings else None,
        results=scored,
    )


def _event_details_summary(raw: object) -> str:
    """Build a flat human summary of an audit event's ``details`` JSON column.

    ``AuditEventModel.details`` is a JSON-text column (default ``"{}"``). Decode
    it defensively to a dict and flatten its top-level scalar entries into a
    ``key=value`` string (e.g. ``todo_id=42 model=glm-4.6``), so the event's
    payload contributes searchable terms. Anything that is not a well-formed
    JSON object — None, malformed text, a non-dict payload — degrades to an
    empty string rather than raising, so a single bad row can never break a
    search. Both the number of keys (``_MAX_DETAILS_KEYS``) and the size of each
    value (500 chars) are bounded, so a runaway/oversized ``details`` payload
    (the column has no size constraint) can never blow up the source text and,
    transitively, the embed cost/memory of a search.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    parts: list[str] = []
    for key, value in parsed.items():
        # Cap the number of keys so a details dict with an unbounded key count
        # can't produce an unboundedly large source_text (memory amplification).
        if len(parts) >= _MAX_DETAILS_KEYS:
            break
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
        else:
            try:
                text = json.dumps(value, separators=(",", ":"))
            except (TypeError, ValueError):
                text = str(value)
        # Bound any single value so one giant payload can't dominate the text.
        parts.append(f"{key}={text[:500]}")
    return " ".join(parts)


def _event_text(row: object) -> str:
    """Build the searchable text for one audit-event row.

    Concatenates the event's ``event_type`` and ``entity_type`` with a flattened
    human summary of its ``details`` JSON (via :func:`_event_details_summary`).
    Only genuinely-present fields contribute — nothing is fabricated. A row with
    no type fields and empty details yields an empty string, which the caller
    skips.
    """
    parts: list[str] = []
    event_type = getattr(row, "event_type", "") or ""
    entity_type = getattr(row, "entity_type", "") or ""
    if event_type:
        parts.append(str(event_type))
    if entity_type:
        parts.append(str(entity_type))
    summary = _event_details_summary(getattr(row, "details", None))
    if summary:
        parts.append(summary)
    return " ".join(parts)


async def _search_events(
    app: FastAPI, req: EmbeddingSearchRequest
) -> EmbeddingSearchResponse:
    """corpus=events: embed each recent audit event on the fly, cosine-rank.

    Mirrors :func:`_search_prompts` exactly but over the persisted
    :class:`~general_ludd.db.models.AuditEventModel` corpus. No stored vectors
    (zero schema change): a session is opened from ``app.state._session_factory``,
    the most-recent ``_DEFAULT_LIST_LIMIT`` audit events are read via a bounded
    ``created_at``-descending select (the AuditEventRepository has no
    project-agnostic recent-list method), each event's searchable text
    (event_type + entity_type + a human summary of its ``details`` JSON, via
    :func:`_event_text`) is embedded with the SAME default embedder as /similar
    and /compare, and the query is ranked against them. An absent factory, an
    empty event log, or any embed failure degrades to an empty result set (the
    caller wraps this so the route is a 200, never a 500). The ``details`` JSON
    is decoded defensively.
    """
    from sqlalchemy import select

    from general_ludd.db.models import AuditEventModel
    from general_ludd.db.repository import _DEFAULT_LIST_LIMIT

    embedder = _select_default_embedder()
    method = _method_of(embedder)

    query_vec = await asyncio.to_thread(embedder.embed, req.text)
    query_dim = len(query_vec)

    factory = _get_session_factory(app)
    if factory is None:
        return EmbeddingSearchResponse(
            corpus="events",
            query_embedding_dim=query_dim,
            embedding_method=method,
            query_embedding=query_vec if req.include_embeddings else None,
            results=[],
        )

    rows: list[object] = []
    async with factory() as session:
        try:
            # XT-8: scope the events corpus to the requested project so semantic
            # search cannot surface another tenant's audit events. project_id
            # column exists + is indexed (models.py), so the filter is zero-cost;
            # None leaves the query unscoped (back-compat).
            stmt = select(AuditEventModel).order_by(
                AuditEventModel.created_at.desc()
            )
            if req.project_id is not None:
                stmt = stmt.where(AuditEventModel.project_id == req.project_id)
            stmt = stmt.limit(_DEFAULT_LIST_LIMIT)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
        except Exception as exc:  # degrade, never 500
            logger.debug("audit-event listing failed: %s", exc)
            rows = []

    scored: list[SearchResultItem] = []
    for row in rows:
        source_text = _event_text(row)
        if not source_text:
            continue
        try:
            event_vec = await asyncio.to_thread(embedder.embed, source_text)
        except Exception as exc:  # skip the bad one, keep ranking the rest
            logger.debug("audit-event embed failed: %s", exc)
            continue
        if len(event_vec) != query_dim:
            continue
        score = cosine_similarity(query_vec, event_vec)
        event_type = getattr(row, "event_type", "") or ""
        entity_id = getattr(row, "entity_id", "") or ""
        created_at = getattr(row, "created_at", None)
        created_at_str = ""
        if created_at is not None:
            iso = getattr(created_at, "isoformat", None)
            created_at_str = iso() if callable(iso) else str(created_at)
        scored.append(
            SearchResultItem(
                rank=0,  # assigned after sort
                name=(
                    f"{event_type}:{entity_id}"
                    if event_type and entity_id
                    else (event_type or str(getattr(row, "id", "")))
                ),
                source_text=source_text,
                similarity_score=score,
                metadata={
                    "event_type": event_type,
                    "entity_type": getattr(row, "entity_type", "") or "",
                    "entity_id": entity_id,
                    "actor": getattr(row, "actor", "") or "",
                    "created_at": created_at_str,
                },
            )
        )

    scored.sort(key=lambda r: r.similarity_score, reverse=True)
    scored = scored[: req.top_k]
    for i, item in enumerate(scored):
        item.rank = i + 1

    return EmbeddingSearchResponse(
        corpus="events",
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
    if req.corpus == "prompts":
        try:
            return await _search_prompts(app, req)
        except Exception as exc:  # never 500
            logger.debug("prompts search failed: %s", exc)
            return EmbeddingSearchResponse(corpus="prompts")
    if req.corpus == "traces":
        try:
            return _search_traces(app, req)
        except Exception as exc:  # never 500
            logger.debug("traces search failed: %s", exc)
            return EmbeddingSearchResponse(corpus="traces")
    if req.corpus == "events":
        try:
            return await _search_events(app, req)
        except Exception as exc:  # never 500
            logger.debug("events search failed: %s", exc)
            return EmbeddingSearchResponse(corpus="events")
    # corpus == "skills" (pydantic guarantees one of the five).
    try:
        registry = getattr(app.state, "_skill_registry", None)
        return _search_skills(req, registry)
    except Exception as exc:  # never 500
        logger.debug("skills search failed: %s", exc)
        return EmbeddingSearchResponse(corpus="skills")


async def _search_multi(
    app: FastAPI, req: MultiCorpusSearchRequest
) -> MultiCorpusSearchResponse:
    """corpus=multi: fan out, tag by corpus, merge, re-rank.

    Creates a per-corpus ``EmbeddingSearchRequest``, calls each individual
    search, tags every result with a ``corpus`` metadata key, then pools
    all results together, sorts by similarity descending, caps by ``top_k``,
    and reassigns ranks. A failed/empty individual corpus is silently skipped
    — the caller gets whatever results the surviving corpora produced, never
    a 500.
    """
    registry = getattr(app.state, "_skill_registry", None)

    pooled: list[SearchResultItem] = []
    corpora_searched: list[str] = []
    query_dim = 0
    method = "hash"

    for corpus in req.corpora:
        sub = EmbeddingSearchRequest(
            text=req.text,
            corpus=corpus,
            top_k=20,  # fetch generously; cap after merge
            include_embeddings=False,
            project_id=None,
        )
        try:
            if corpus == "task_types":
                resp = await _search_task_types(app, sub)
            elif corpus == "prompts":
                resp = await _search_prompts(app, sub)
            elif corpus == "traces":
                resp = _search_traces(app, sub)
            elif corpus == "events":
                resp = await _search_events(app, sub)
            else:  # skills
                resp = _search_skills(sub, registry)
        except Exception as exc:  # degrade — skip the broken corpus
            logger.debug("multi-corpus %s search failed: %s", corpus, exc)
            continue

        if resp.query_embedding_dim > 0:
            query_dim = resp.query_embedding_dim
        if resp.embedding_method and resp.embedding_method != "hash":
            method = resp.embedding_method
        corpora_searched.append(corpus)

        for item in resp.results:
            item.metadata = dict(item.metadata)
            item.metadata["corpus"] = corpus
            pooled.append(item)

    pooled.sort(key=lambda r: r.similarity_score, reverse=True)
    pooled = pooled[: req.top_k]
    for i, item in enumerate(pooled):
        item.rank = i + 1

    query_vec: list[float] | None = None
    if req.include_embeddings:
        try:
            embedder = _select_default_embedder()
            query_vec = embedder.embed(req.text)
            if query_dim == 0:
                query_dim = len(query_vec)
        except Exception as exc:
            logger.debug("multi-corpus query embed failed: %s", exc)

    return MultiCorpusSearchResponse(
        corpora_searched=sorted(corpora_searched),
        query_embedding_dim=query_dim,
        embedding_method=method,
        query_embedding=query_vec,
        results=pooled,
    )


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
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
        summary=(
            "Search a real corpus (skills/task_types/prompts/traces) by "
            "embedding similarity"
        ),
        description=(
            "Take a string a bot produced and search a real corpus with it (RAG "
            "search): returns the top_k most-similar corpus items ranked by "
            "cosine similarity. Corpora: `skills` (the live SkillRegistry — "
            "each skill description is embedded on the fly, no schema change), "
            "`task_types` (the canonical task-type vectors, delegating to the "
            "/similar logic), `prompts` (the persisted prompt_profiles — "
            "each prompt_text embedded on the fly, no schema change), and "
            "`traces` (the in-process recent-execution-traces buffer — each "
            "trace's work_type/phase/span descriptions embedded on the fly, no "
            "schema change). object other `corpus` value is rejected (422). Reuses "
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

        Read-only. ``corpus`` is ``skills`` (default), ``task_types``,
        ``prompts``, or ``traces``. Returns
        the ranked matches (highest cosine similarity first), the query
        embedding dimensionality, the resolved embedding method, and — when
        ``include_embeddings`` is set — the query vector itself. Never 500s.
        """
        return await _search(app, req)

    @app.post(
        "/api/embeddings/search-multi",
        response_model=MultiCorpusSearchResponse,
        summary=(
            "Search multiple corpora (skills/task_types/prompts/traces/events) "
            "by embedding similarity and merge results"
        ),
        description=(
            "Take a string a bot produced and search multiple real corpora with "
            "it (multi-RAG search): fans out to each requested corpus, tags "
            "every result with its source corpus, merges the results into a "
            "single ranked list, and returns the top_k items. Reuses the same "
            "default embedder as /search and /compare; `embedding_method` is "
            "\"openai\" or \"hash\". Defensive: a failed/empty corpus is skipped; "
            "bad input -> 422. Never 500s."
        ),
    )
    async def api_embeddings_search_multi(
        req: MultiCorpusSearchRequest,
    ) -> MultiCorpusSearchResponse:
        """Search multiple corpora and merge the results.

        Read-only. ``corpora`` is a list of one or more of ``skills``,
        ``task_types``, ``prompts``, ``traces``, ``events``. Returns the
        merged ranked matches (highest cosine similarity first), the query
        embedding dimensionality, the resolved embedding method, and — when
        ``include_embeddings`` is set — the query vector itself. Never 500s.
        """
        return await _search_multi(app, req)
