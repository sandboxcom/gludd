"""Evaluation harness: score a compactor by compression AND downstream fidelity.

A good compactor cuts tokens *without losing the information the work item
needs*.  So every candidate is scored on two axes:

  - **compression** — how much smaller the context got (1 - token ratio), and
  - **fidelity** — whether goal-relevant facts SURVIVE the compaction.

Fidelity is measured with probes: each :class:`Probe` names facts (``expected``
substrings — file paths, ids, numbers, decisions) that must still be recoverable
from the compacted context.  The default judge is a fast, offline
keyword-retention check; an LLM judge can be injected via ``judge_fn`` for a
stricter semantic check.  The composite :attr:`CompactionMetrics.score` is what
the arena maximizes, so "beats every other mechanism" is a measured claim, not
an assertion.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import CompactionRequest, Compactor

# judge(compacted_context_text, probe_question, expected_facts) -> retained?
JudgeFn = Callable[[str, str, list[str]], bool]


class Probe(BaseModel):
    """A fact the compacted context must still let a reader recover."""

    model_config = {"strict": True}

    question: str
    # Substrings that must survive compaction (exact ids/paths/numbers/decisions).
    expected: list[str] = Field(default_factory=list)


class EvalSample(BaseModel):
    """One evaluation case: a context, the work-item goal, and fidelity probes."""

    model_config = {"strict": True}

    messages: list[ContextMessage] = Field(default_factory=list)
    goal: str = ""
    probes: list[Probe] = Field(default_factory=list)
    target_tokens: int | None = None
    preserve_recent: int = 4


class CompactionMetrics(BaseModel):
    """Aggregate score for a compactor over a corpus."""

    model_config = {"strict": True}

    compactor: str = ""
    samples: int = 0
    mean_ratio: float = 1.0
    mean_fidelity: float = 0.0
    mean_tokens_saved: float = 0.0
    score: float = 0.0


def _keyword_retention_judge(text: str, _question: str, expected: list[str]) -> bool:
    """Offline default judge: every expected fact appears in the compacted text.

    An empty ``expected`` list is treated as vacuously retained.
    """
    if not expected:
        return True
    hay = text.lower()
    return all(fact.lower() in hay for fact in expected)


def _context_text(messages: list[ContextMessage]) -> str:
    return "\n".join(m.content for m in messages)


def _sample_fidelity(
    compacted: list[ContextMessage],
    probes: list[Probe],
    judge: JudgeFn,
) -> float:
    if not probes:
        return 1.0
    text = _context_text(compacted)
    retained = sum(1 for p in probes if judge(text, p.question, p.expected))
    return retained / len(probes)


def evaluate(
    compactor: Compactor,
    corpus: list[EvalSample],
    *,
    judge_fn: JudgeFn | None = None,
    fidelity_weight: float = 0.7,
    compression_weight: float = 0.3,
) -> CompactionMetrics:
    """Run *compactor* over *corpus* and return aggregate metrics.

    ``score = fidelity_weight * mean_fidelity + compression_weight *
    (1 - mean_ratio)`` — high when the compactor keeps goal-relevant facts AND
    shrinks the context.  A drop-everything strategy tanks fidelity; a no-op
    tanks compression; the winner is the one that balances both.
    """
    judge = judge_fn or _keyword_retention_judge
    if not corpus:
        return CompactionMetrics(compactor=getattr(compactor, "name", "?"))

    ratios: list[float] = []
    fidelities: list[float] = []
    saved: list[int] = []
    for sample in corpus:
        result = compactor.compact(
            CompactionRequest(
                messages=sample.messages,
                goal=sample.goal,
                target_tokens=sample.target_tokens,
                preserve_recent=sample.preserve_recent,
            )
        )
        ratios.append(result.ratio)
        saved.append(result.tokens_saved)
        fidelities.append(_sample_fidelity(result.messages, sample.probes, judge))

    n = len(corpus)
    mean_ratio = sum(ratios) / n
    mean_fidelity = sum(fidelities) / n
    score = fidelity_weight * mean_fidelity + compression_weight * (1.0 - mean_ratio)
    return CompactionMetrics(
        compactor=getattr(compactor, "name", "?"),
        samples=n,
        mean_ratio=mean_ratio,
        mean_fidelity=mean_fidelity,
        mean_tokens_saved=sum(saved) / n,
        score=score,
    )
