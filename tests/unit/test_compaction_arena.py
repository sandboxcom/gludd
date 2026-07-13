"""Unit tests for compaction/arena.py — arena, SelfImprovingCompactor, candidate generation."""

from __future__ import annotations

import pytest

from general_ludd.agents.context import ContextMessage
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
)
from general_ludd.compaction.baselines import NoOpCompactor, TruncationCompactor
from general_ludd.compaction.evaluate import (
    EvalSample,
    Probe,
)


def _msg(role: str = "user", content: str = "hello", is_system: bool = False) -> ContextMessage:
    return ContextMessage(role=role, content=content, is_system=is_system)


class _FakeCompactor:
    name = "fake"
    def compact(self, request: CompactionRequest) -> CompactionResult:
        return CompactionResult(
            messages=list(request.messages),
            method=self.name,
            original_tokens=100,
            compacted_tokens=80,
        )


class TestGenerateCandidates:
    def test_always_includes_baselines(self):
        candidates = generate_candidates()
        names = {c.name for c in candidates}
        assert "noop" in names
        assert "truncate" in names
        assert "context_compactor" in names or any("slm" in n for n in names)

    def test_includes_slm_variants(self):
        candidates = generate_candidates()
        names = {c.name for c in candidates}
        assert "slm_r2" in names
        assert "slm_r4" in names

    def test_adds_context_compactor_slm_when_summarize_fn_provided(self):
        def fn(g, t):
            return "summary"
        candidates = generate_candidates(fn)
        names = {c.name for c in candidates}
        assert "context_compactor_slm" in names

    def test_no_context_compactor_slm_when_fn_none(self):
        candidates = generate_candidates(None)
        names = {c.name for c in candidates}
        assert "context_compactor_slm" not in names


class TestRunArena:
    def _eval_sample(self) -> EvalSample:
        return EvalSample(
            messages=[_msg("user", "test")],
            goal="test",
            probes=[Probe(question="q", expected=["test"])],
        )

    def test_empty_candidates_returns_default(self):
        result = run_arena([], [self._eval_sample()])
        assert result.leaderboard == []
        assert result.winner == ""

    def test_no_incumbent_top_scorer_wins(self):
        result = run_arena(
            [NoOpCompactor(), TruncationCompactor()],
            [self._eval_sample()],
            incumbent=None,
        )
        assert result.promoted is True
        assert result.winner != ""

    def test_incumbent_beats_challenger_no_promotion(self):
        noop = NoOpCompactor()
        trunc = TruncationCompactor()
        result = run_arena(
            [noop, trunc],
            [self._eval_sample()],
            incumbent=noop.name,
            min_improvement=0.0,
        )
        if not result.promoted:
            assert result.winner == noop.name

    def test_incumbent_not_in_pool_top_scorer_wins(self):
        result = run_arena(
            [NoOpCompactor(), TruncationCompactor()],
            [self._eval_sample()],
            incumbent="nonexistent",
        )
        assert result.promoted is True
        assert result.winner != ""

    def test_leaderboard_sorted_by_score(self):
        result = run_arena(
            [NoOpCompactor(), TruncationCompactor()],
            [self._eval_sample()],
        )
        if len(result.leaderboard) >= 2:
            assert result.leaderboard[0].score >= result.leaderboard[1].score

    def test_margin_set_on_no_incumbent(self):
        result = run_arena(
            [NoOpCompactor()],
            [self._eval_sample()],
            incumbent=None,
        )
        assert result.margin == result.leaderboard[0].score


class TestSelfImprovingCompactor:
    def _sample(self) -> EvalSample:
        return EvalSample(
            messages=[_msg("user", "test")],
            goal="test",
            probes=[Probe(question="q", expected=["test"])],
        )

    def test_init_empty_candidates_raises(self):
        with pytest.raises(ValueError, match="at least one candidate"):
            SelfImprovingCompactor([])

    def test_init_with_candidates(self):
        sic = SelfImprovingCompactor([NoOpCompactor()])
        assert sic.champion.name == "noop"

    def test_init_with_explicit_champion(self):
        sic = SelfImprovingCompactor(
            [NoOpCompactor(), TruncationCompactor()],
            champion=TruncationCompactor(),
        )
        assert sic.champion.name == "truncate"

    def test_compact_delegates_to_champion(self):
        sic = SelfImprovingCompactor([NoOpCompactor()])
        request = CompactionRequest(messages=[_msg("user", "hello")])
        result = sic.compact(request)
        assert result.method == "noop"

    def test_improve_no_better_no_promotion(self):
        sic = SelfImprovingCompactor(
            [NoOpCompactor(), TruncationCompactor()],
            min_improvement=0.0,
        )
        original_champion = sic.champion
        sic.improve([self._sample()])
        assert sic.champion is original_champion

    def test_improve_promotes_when_better(self):
        class _HighScoreCompactor:
            name = "highscore"
            def compact(self, request: CompactionRequest) -> CompactionResult:
                return CompactionResult(
                    messages=list(request.messages),
                    method=self.name,
                    original_tokens=100,
                    compacted_tokens=10,
                )

        class _LowScoreCompactor:
            name = "lowscore"
            def compact(self, request: CompactionRequest) -> CompactionResult:
                return CompactionResult(
                    messages=list(request.messages),
                    method=self.name,
                    original_tokens=100,
                    compacted_tokens=90,
                )

        sic = SelfImprovingCompactor(
            [_LowScoreCompactor(), _HighScoreCompactor()],
            champion=_LowScoreCompactor(),
            min_improvement=0.0,
        )
        sic.improve([self._sample()])
        assert sic.champion.name == "highscore"

    def test_improve_adds_champion_to_pool(self):
        champion = _FakeCompactor()
        sic = SelfImprovingCompactor(
            [NoOpCompactor()],
            champion=champion,
        )
        result = sic.improve([self._sample()])
        assert result.incumbent == "fake"

    def test_improve_returns_arena_result(self):
        sic = SelfImprovingCompactor([NoOpCompactor()])
        result = sic.improve([self._sample()])
        assert isinstance(result, ArenaResult)
        assert result.leaderboard != []


class TestBuildSelfImprovingCompactor:
    def test_default_champion_is_truncate(self):
        sic = build_self_improving_compactor()
        assert sic.champion.name in {"truncate", "noop"}

    def test_custom_champion_name(self):
        sic = build_self_improving_compactor(champion_name="noop")
        assert sic.champion.name == "noop"

    def test_nonexistent_champion_falls_back(self):
        sic = build_self_improving_compactor(champion_name="nonexistent")
        assert sic.champion.name != ""
