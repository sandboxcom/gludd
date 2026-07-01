"""Local-SLM context/memory compaction with a self-improving eval loop.

This package compresses the conversation context / memory that is sent to the
main LLM for a specific work item, optionally using a small locally-run model
(SLM) as the summarizer.  It ships:

  - a pluggable ``Compactor`` interface (:mod:`.base`),
  - baseline compactors — no-op, truncation, and an adapter over the existing
    ``agents.context.ContextCompactor`` (:mod:`.baselines`),
  - an SLM compactor that does work-item-aware abstractive summarization via an
    injected model-call function, with an offline fallback (:mod:`.slm`),
  - an evaluation harness that scores a compactor by compression ratio AND
    downstream fidelity (:mod:`.evaluate`),
  - a self-improvement arena that A/B-evaluates candidate compactors on a
    corpus and promotes the best-scoring one (:mod:`.arena`).

Everything is dependency-injected (the SLM call is a plain callable), so the
whole package runs and tests fully offline with no model required.
"""

from __future__ import annotations

from general_ludd.compaction.arena import (
    ArenaResult,
    SelfImprovingCompactor,
    build_self_improving_compactor,
    generate_candidates,
    run_arena,
)
from general_ludd.compaction.base import (
    CompactionRequest,
    CompactionResult,
    Compactor,
    estimate_tokens,
)
from general_ludd.compaction.baselines import (
    ContextCompactorAdapter,
    NoOpCompactor,
    TruncationCompactor,
)
from general_ludd.compaction.evaluate import (
    CompactionMetrics,
    EvalSample,
    Probe,
    evaluate,
)
from general_ludd.compaction.slm import SLMCompactor, make_slm_summarize_fn

__all__ = [
    "ArenaResult",
    "CompactionMetrics",
    "CompactionRequest",
    "CompactionResult",
    "Compactor",
    "ContextCompactorAdapter",
    "EvalSample",
    "NoOpCompactor",
    "Probe",
    "SLMCompactor",
    "SelfImprovingCompactor",
    "TruncationCompactor",
    "build_self_improving_compactor",
    "estimate_tokens",
    "evaluate",
    "generate_candidates",
    "make_slm_summarize_fn",
    "run_arena",
]
