"""Self-improving compaction: a champion/challenger arena.

This is the mechanism that makes the claim "performs better than any other
compaction mechanism" *measured and self-correcting* rather than asserted:

  1. Keep a **champion** compactor (the one actually used in production).
  2. On :meth:`SelfImprovingCompactor.improve`, run every candidate (baselines,
     the current champion, and SLM variants) through :func:`~.evaluate.evaluate`
     over the current eval corpus, producing a leaderboard.
  3. Promote a challenger ONLY if it beats the champion's score by at least
     ``min_improvement`` — a gated, no-regression swap (mirrors gludd's
     self_improve gate + abtest philosophy).

Because the champion is always the arena's top scorer, the deployed compactor is
by construction the best of the evaluated pool.  Feeding the arena a growing,
real corpus (built from gludd's own run history) is how it keeps improving.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from general_ludd.compaction.base import (
    CompactionRequest,
    CompactionResult,
    Compactor,
)
from general_ludd.compaction.baselines import (
    ContextCompactorAdapter,
    NoOpCompactor,
    TruncationCompactor,
)
from general_ludd.compaction.evaluate import (
    CompactionMetrics,
    EvalSample,
    JudgeFn,
    evaluate,
)
from general_ludd.compaction.slm import SLMCompactor, SummarizeFn

logger = logging.getLogger(__name__)


class ArenaResult(BaseModel):
    """Outcome of one arena round."""

    model_config = {"strict": True}

    # Metrics for every candidate, sorted best-score-first.
    leaderboard: list[CompactionMetrics] = Field(default_factory=list)
    winner: str = ""
    incumbent: str = ""
    promoted: bool = False
    margin: float = 0.0


def generate_candidates(summarize_fn: SummarizeFn | None = None) -> list[Compactor]:
    """A diverse candidate pool spanning the config space the arena explores.

    Includes the no-op ceiling, recency truncation at a couple of depths, the
    existing ContextCompactor (with and without an SLM summarizer), and the SLM
    strategy at a couple of preserve-recent depths.  ``summarize_fn`` is the
    injected (optionally local-SLM) summarizer; when None, the SLM entries use
    their deterministic extractive fallback so the pool still runs offline.
    """
    candidates: list[Compactor] = [
        NoOpCompactor(),
        TruncationCompactor(),
        ContextCompactorAdapter(preserve_recent_count=4, summary_fn=None),
        SLMCompactor(summarize_fn, preserve_recent=2, name="slm_r2"),
        SLMCompactor(summarize_fn, preserve_recent=4, name="slm_r4"),
    ]
    if summarize_fn is not None:
        # The existing ContextCompactor gains an SLM summary_fn (single-arg).
        candidates.append(
            ContextCompactorAdapter(
                preserve_recent_count=4,
                summary_fn=lambda text: summarize_fn("", text),
                name="context_compactor_slm",
            )
        )
    return candidates


def run_arena(
    candidates: list[Compactor],
    corpus: list[EvalSample],
    *,
    incumbent: str | None = None,
    judge_fn: JudgeFn | None = None,
    min_improvement: float = 0.0,
) -> ArenaResult:
    """Evaluate every candidate on *corpus* and rank them.

    ``incumbent`` is the currently-deployed compactor's name; a challenger is
    only declared the winner if it beats the incumbent's score by
    ``min_improvement`` (else the incumbent holds).  With no incumbent, the top
    scorer wins outright.
    """
    # Deterministic ranking: primary by score, then break ties toward the MORE
    # faithful and MORE compressed candidate (never candidate-list order). The
    # tuple sorts descending on (score, mean_fidelity, -mean_ratio) so a higher
    # fidelity wins a score tie, and a lower ratio (more compression) wins after
    # that.
    board = sorted(
        (evaluate(c, corpus, judge_fn=judge_fn) for c in candidates),
        key=lambda m: (m.score, m.mean_fidelity, -m.mean_ratio),
        reverse=True,
    )
    if not board:
        return ArenaResult(incumbent=incumbent or "")

    top = board[0]
    if incumbent is None:
        return ArenaResult(
            leaderboard=board, winner=top.compactor,
            incumbent="", promoted=True, margin=top.score,
        )

    inc_score = next((m.score for m in board if m.compactor == incumbent), None)
    if inc_score is None:
        # Incumbent named but not scored in this pool (e.g. caller passed an
        # incumbent name not among `candidates`) — treat the top scorer as the
        # winner. This is defensive: SelfImprovingCompactor.improve() always adds
        # its champion to the pool AND only acts on a promotion when
        # `result.winner in self._by_name`, so it can never promote to a winner
        # it doesn't hold. Direct run_arena callers own their own pool hygiene.
        return ArenaResult(
            leaderboard=board, winner=top.compactor, incumbent=incumbent,
            promoted=True, margin=top.score,
        )

    margin = top.score - inc_score
    # Strict promotion: a challenger must EXCEED the incumbent by the required
    # margin. With ``min_improvement == 0`` an exact score tie is NOT a win —
    # prefer the incumbent (no-regret, no-churn). With a positive threshold we
    # preserve the old ``>=`` semantics (hitting the threshold exactly still
    # promotes), so SelfImprovingCompactor's default 0.01 gate is unchanged.
    promote = top.compactor != incumbent and (
        margin > min_improvement
        or (margin == min_improvement and min_improvement > 0)
    )
    return ArenaResult(
        leaderboard=board,
        winner=top.compactor if promote else incumbent,
        incumbent=incumbent,
        promoted=promote,
        margin=margin,
    )


class SelfImprovingCompactor:
    """A compactor whose strategy is re-selected by the arena, gated on wins.

    Use it exactly like any :class:`Compactor` (``.compact(request)``); call
    :meth:`improve` with fresh eval samples to let it consider promoting a better
    strategy.  Promotions are logged and never regress (gated by
    ``min_improvement``).
    """

    name = "self_improving"

    def __init__(
        self,
        candidates: list[Compactor],
        *,
        champion: Compactor | None = None,
        judge_fn: JudgeFn | None = None,
        min_improvement: float = 0.01,
    ) -> None:
        if not candidates:
            raise ValueError("SelfImprovingCompactor needs at least one candidate")
        self._candidates = candidates
        self._by_name = {c.name: c for c in candidates}
        self._champion = champion or candidates[0]
        self._by_name.setdefault(self._champion.name, self._champion)
        self._judge_fn = judge_fn
        self._min_improvement = min_improvement

    @property
    def champion(self) -> Compactor:
        return self._champion

    def compact(self, request: CompactionRequest) -> CompactionResult:
        return self._champion.compact(request)

    def improve(self, corpus: list[EvalSample]) -> ArenaResult:
        """Run an arena round; promote a strictly-better challenger (gated)."""
        pool = list(self._candidates)
        if self._champion not in pool:
            pool.append(self._champion)
        result = run_arena(
            pool,
            corpus,
            incumbent=self._champion.name,
            judge_fn=self._judge_fn,
            min_improvement=self._min_improvement,
        )
        if result.promoted and result.winner in self._by_name:
            prev = self._champion.name
            self._champion = self._by_name[result.winner]
            logger.info(
                "compaction champion promoted: %s -> %s (margin=%.4f)",
                prev, result.winner, result.margin,
            )
        return result


# Convenience factory: a ready self-improving compactor over the default pool.
def build_self_improving_compactor(
    summarize_fn: SummarizeFn | None = None,
    *,
    judge_fn: JudgeFn | None = None,
    min_improvement: float = 0.01,
    champion_name: str = "truncate",
) -> SelfImprovingCompactor:
    candidates = generate_candidates(summarize_fn)
    champion = next((c for c in candidates if c.name == champion_name), candidates[0])
    return SelfImprovingCompactor(
        candidates,
        champion=champion,
        judge_fn=judge_fn,
        min_improvement=min_improvement,
    )
