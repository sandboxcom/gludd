"""End-to-end / integration tests for the compaction subsystem.

Proves the full compaction pipeline works: request → compact → evaluate → arena
→ self-improve → aggressiveness controller decision — all running offline with
dependency-injected callables.
"""

from __future__ import annotations

import pytest

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction import (
    CompactionRequest,
    CompactionResult,
    ContextCompactorAdapter,
    EvalSample,
    NoOpCompactor,
    Probe,
    SelfImprovingCompactor,
    SLMCompactor,
    TruncationCompactor,
    build_self_improving_compactor,
    estimate_tokens,
    evaluate,
    generate_candidates,
    run_arena,
)
from general_ludd.compaction.aggressive import LEVELS, CompactionLevel, level_at
from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)

# ---- helpers -----------------------------------------------------------------

def _msg(content: str, *, role: str = "user", system: bool = False) -> ContextMessage:
    return ContextMessage(
        role=role,
        content=content,
        token_estimate=estimate_tokens(content),
        is_system=system,
    )


def _long_context() -> list[ContextMessage]:
    """Messages that carry goal-relevant facts early, then droppable filler."""
    return [
        _msg("You are a coding agent.", role="system", system=True),
        _msg("The API key lives at config/secrets.yml and the retry cap is 42."),
        _msg("We chose the postgres backend after benchmarking three options."),
        _msg(
            "We paused for a short coffee break and chatted about weekend plans "
            "and the local weather.",
            role="assistant",
        ),
        _msg(
            "Someone brought up the upcoming office relocation but we did not dig "
            "into any of the specifics."
        ),
        _msg(
            "There was a brief tangent about which code editor everyone on the "
            "team prefers using lately.",
            role="assistant",
        ),
        _msg(
            "We casually agreed to circle back on the broader roadmap discussion "
            "at some later point in time."
        ),
        _msg(
            "A teammate shared a funny meme in the channel and everyone had a good "
            "laugh together for a moment.",
            role="assistant",
        ),
        _msg(
            "After that we refocused ourselves and got ready to pick the actual "
            "work back up once again."
        ),
        _msg("continue with the plan"),
        _msg("proceeding now"),
    ]


def _good_summarize(goal: str, text: str) -> str:
    keep = []
    for line in text.splitlines():
        if "config/secrets.yml" in line or "42" in line or "postgres" in line:
            keep.append(line)
    return "SUMMARY: " + " | ".join(keep) if keep else "SUMMARY: (none)"


def _lossy_summarize(goal: str, text: str) -> str:
    return "the agent discussed some setup and then continued the plan"


def _corpus() -> list[EvalSample]:
    return [
        EvalSample(
            messages=_long_context(),
            goal="find the API key location and retry cap",
            probes=[
                Probe(
                    question="Where is the API key?",
                    expected=["config/secrets.yml"],
                ),
                Probe(question="Retry cap?", expected=["42"]),
            ],
            preserve_recent=2,
        )
    ]


# ============================================================================ #
# 1. CompactionRequest creation                                                #
# ============================================================================ #


class TestCompactionRequest:
    def test_default_values(self):
        req = CompactionRequest()
        assert req.messages == []
        assert req.goal == ""
        assert req.target_tokens is None
        assert req.preserve_recent == 4

    def test_with_messages_and_goal(self):
        msgs = _long_context()
        req = CompactionRequest(messages=msgs, goal="find the DB backend")
        assert req.messages == msgs
        assert req.goal == "find the DB backend"
        assert len(req.messages) == 11

    def test_with_target_tokens(self):
        req = CompactionRequest(
            messages=_long_context(), target_tokens=500, preserve_recent=2
        )
        assert req.target_tokens == 500
        assert req.preserve_recent == 2

    def test_preserve_recent_zero_is_honored(self):
        # An explicit 0 means "keep zero recent messages" — distinct from the
        # default of 4. The request object must store it faithfully.
        req = CompactionRequest(messages=_long_context(), preserve_recent=0)
        assert req.preserve_recent == 0


# ============================================================================ #
# 2. CompactionResult fields                                                   #
# ============================================================================ #


class TestCompactionResult:
    def test_fields_present(self):
        msgs = _long_context()
        r = CompactionResult(
            messages=msgs,
            method="test_method",
            original_tokens=200,
            compacted_tokens=100,
            dropped_messages=5,
        )
        assert r.messages == msgs
        assert r.method == "test_method"
        assert r.original_tokens == 200
        assert r.compacted_tokens == 100
        assert r.dropped_messages == 5

    def test_ratio_full_compression(self):
        r = CompactionResult(original_tokens=200, compacted_tokens=100)
        assert r.ratio == pytest.approx(0.5)

    def test_ratio_no_compression(self):
        r = CompactionResult(original_tokens=100, compacted_tokens=100)
        assert r.ratio == 1.0

    def test_ratio_zero_original_is_one(self):
        r = CompactionResult(original_tokens=0, compacted_tokens=5)
        assert r.ratio == 1.0

    def test_tokens_saved(self):
        r = CompactionResult(original_tokens=200, compacted_tokens=80)
        assert r.tokens_saved == 120

    def test_tokens_saved_never_negative(self):
        r = CompactionResult(original_tokens=0, compacted_tokens=5)
        assert r.tokens_saved == 0

    def test_default_result_fields(self):
        r = CompactionResult()
        assert r.messages == []
        assert r.method == "noop"
        assert r.original_tokens == 0
        assert r.compacted_tokens == 0
        assert r.dropped_messages == 0
        assert r.ratio == 1.0
        assert r.tokens_saved == 0

    def test_result_from_noop_compactor(self):
        msgs = _long_context()
        r = NoOpCompactor().compact(CompactionRequest(messages=msgs))
        assert r.method == "noop"
        assert r.messages == msgs
        assert r.ratio == 1.0
        assert r.tokens_saved == 0
        assert r.dropped_messages == 0
        assert r.original_tokens == r.compacted_tokens


# ============================================================================ #
# 3. NoOpCompactor — messages unchanged, ratio 1.0                             #
# ============================================================================ #


class TestNoOpCompactor:
    def test_messages_unchanged(self):
        msgs = _long_context()
        r = NoOpCompactor().compact(CompactionRequest(messages=msgs, goal="test"))
        assert r.messages == msgs
        assert r.ratio == 1.0
        assert r.tokens_saved == 0
        assert r.dropped_messages == 0

    def test_empty_messages(self):
        r = NoOpCompactor().compact(CompactionRequest(messages=[]))
        assert r.messages == []
        assert r.ratio == 1.0

    def test_with_goal_no_effect_on_noop(self):
        msgs = _long_context()
        r = NoOpCompactor().compact(
            CompactionRequest(messages=msgs, goal="find API key")
        )
        assert r.messages == msgs
        assert r.original_tokens == r.compacted_tokens


# ============================================================================ #
# 4. TruncationCompactor — keeps system + recent N, drops old                  #
# ============================================================================ #


class TestTruncationCompactor:
    def test_keeps_system_and_recent_drops_old(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=2)
        )
        # System message survives.
        assert any(m.is_system for m in r.messages)
        # Recent tail (last 2 non-system) survives.
        joined = "\n".join(m.content for m in r.messages)
        assert "continue with the plan" in joined
        assert "proceeding now" in joined
        # Old fact-bearing message was dropped.
        assert "config/secrets.yml" not in joined

    def test_compression_occurs(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=2)
        )
        assert r.compacted_tokens < r.original_tokens
        assert r.tokens_saved > 0
        assert r.dropped_messages > 0

    def test_honors_target_tokens(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=1, target_tokens=30)
        )
        assert r.compacted_tokens <= r.original_tokens

    def test_preserve_recent_zero_keeps_only_system(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=0)
        )
        # Only system messages should survive.
        non_system = [m for m in r.messages if not m.is_system]
        assert len(non_system) == 0

    def test_all_system_messages_preserved(self):
        msgs = [
            _msg("sys_1", role="system", system=True),
            _msg("user_1"),
            _msg("sys_2", role="system", system=True),
            _msg("user_2"),
            _msg("user_3"),
        ]
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=1)
        )
        system_msgs = [m for m in r.messages if m.is_system]
        assert len(system_msgs) == 2
        contents = {m.content for m in system_msgs}
        assert "sys_1" in contents
        assert "sys_2" in contents


# ============================================================================ #
# 5. SLMCompactor with good summarize function                                 #
# ============================================================================ #


class TestSLMCompactorGoodFn:
    def test_compresses_while_preserving_facts(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, goal="find the API key location")
        )
        assert r.method == "slm"
        assert r.compacted_tokens < r.original_tokens
        joined = "\n".join(m.content for m in r.messages)
        assert "config/secrets.yml" in joined
        assert "42" in joined

    def test_prior_context_header_present(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, goal="find API key")
        )
        assert any("prior context" in m.content for m in r.messages)

    def test_recent_messages_preserved_verbatim(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, goal="find API key")
        )
        joined = "\n".join(m.content for m in r.messages)
        assert "continue with the plan" in joined
        assert "proceeding now" in joined

    def test_dropped_messages_equals_old_count(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, preserve_recent=2)
        )
        # Total messages: 1 system + 10 non-system = 11. preserve_recent=2
        # keeps the 2 most recent non-system + 1 system. The other 8 non-system
        # messages are summarized (= dropped).
        non_system = [m for m in msgs if not m.is_system]
        assert r.dropped_messages == len(non_system) - 2

    def test_honors_target_tokens_soft_bound(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, target_tokens=100)
        )
        assert r.compacted_tokens <= r.original_tokens

    def test_no_old_messages_is_passthrough(self):
        msgs = [_msg("sys", role="system", system=True), _msg("only recent")]
        r = SLMCompactor(_good_summarize, preserve_recent=4).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.dropped_messages == 0
        assert r.messages == msgs
        assert r.compacted_tokens == r.original_tokens


# ============================================================================ #
# 6. SLMCompactor without summarize function (extractive fallback)             #
# ============================================================================ #


class TestSLMCompactorNoFn:
    def test_falls_back_to_extractive(self):
        msgs = _long_context()
        r = SLMCompactor(None, preserve_recent=2, fallback_max_chars=60).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.method == "slm"
        assert r.messages
        assert any("prior context" in m.content for m in r.messages)

    def test_produces_result_without_crashing(self):
        # Even with no model wired, the compactor must return a valid result.
        msgs = _long_context()
        r = SLMCompactor(None, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, goal="test")
        )
        assert isinstance(r, CompactionResult)
        assert len(r.messages) > 0

    def test_fallback_preserves_system_and_recent(self):
        msgs = _long_context()
        r = SLMCompactor(None, preserve_recent=3).compact(
            CompactionRequest(messages=msgs)
        )
        assert any(m.is_system for m in r.messages)
        joined = "\n".join(m.content for m in r.messages)
        # The recent tail should still be there verbatim.
        assert "continue with the plan" in joined
        assert "proceeding now" in joined

    def test_fail_soft_when_summarizer_raises(self):
        def boom(goal: str, text: str) -> str:
            raise RuntimeError("model down")

        msgs = _long_context()
        r = SLMCompactor(boom, preserve_recent=2).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.method == "slm"
        assert r.messages

    def test_fail_soft_when_summarizer_returns_empty(self):
        def empty(goal: str, text: str) -> str:
            return ""

        msgs = _long_context()
        r = SLMCompactor(empty, preserve_recent=2).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.messages
        assert any("prior context" in m.content for m in r.messages)

    def test_fail_soft_when_summarizer_returns_whitespace_only(self):
        def whitespace(goal: str, text: str) -> str:
            return "   \n  "

        msgs = _long_context()
        r = SLMCompactor(whitespace, preserve_recent=2).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.messages
        assert any("prior context" in m.content for m in r.messages)

    def test_preserve_recent_zero_summarizes_everything(self):
        msgs = _long_context()
        non_system = [m for m in msgs if not m.is_system]
        r = SLMCompactor(_good_summarize, preserve_recent=4).compact(
            CompactionRequest(
                messages=msgs, goal="find the API key location", preserve_recent=0
            )
        )
        assert r.dropped_messages == len(non_system)
        assert not any(m.content == "proceeding now" for m in r.messages)
        joined = "\n".join(m.content for m in r.messages)
        assert "config/secrets.yml" in joined


# ============================================================================ #
# 7. evaluate() with NoOpCompactor — full fidelity, no compression             #
# ============================================================================ #


class TestEvaluateNoOp:
    def test_full_fidelity_no_compression(self):
        m = evaluate(NoOpCompactor(), _corpus())
        assert m.mean_fidelity == 1.0
        assert m.mean_ratio == 1.0
        assert m.mean_tokens_saved == 0.0
        assert m.score > 0.0  # fidelity_weight * 1.0 + compression_weight * 0.0

    def test_samples_count_correct(self):
        corpus = _corpus()
        m = evaluate(NoOpCompactor(), corpus)
        assert m.samples == len(corpus)

    def test_compactor_name_in_metrics(self):
        m = evaluate(NoOpCompactor(), _corpus())
        assert m.compactor == "noop"

    def test_empty_corpus_returns_defaults(self):
        m = evaluate(NoOpCompactor(), [])
        assert m.samples == 0
        assert m.compactor == "noop"
        assert m.mean_ratio == 1.0
        assert m.mean_fidelity == 0.0


# ============================================================================ #
# 8. evaluate() with TruncationCompactor — compression, loses fidelity         #
# ============================================================================ #


class TestEvaluateTruncate:
    def test_compression_but_loses_fidelity(self):
        m = evaluate(TruncationCompactor(), _corpus())
        assert m.mean_fidelity < 1.0
        assert m.mean_ratio < 1.0
        assert m.mean_tokens_saved > 0.0

    def test_score_lower_than_noop(self):
        # Truncation trades fidelity for compression; on this corpus the
        # composite score reflects the fidelity loss.
        trunc = evaluate(TruncationCompactor(), _corpus())
        noop = evaluate(NoOpCompactor(), _corpus())
        # Noop has perfect fidelity + zero compression; truncate has imperfect
        # fidelity + some compression.  With fidelity_weight=0.7, the fidelity
        # loss should dominate, making truncate score lower than noop.
        assert trunc.score < noop.score


# ============================================================================ #
# 9. evaluate() with good SLMCompactor — high fidelity AND compression         #
# ============================================================================ #


class TestEvaluateGoodSLM:
    def test_high_fidelity_and_compression(self):
        m = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        assert m.mean_fidelity == 1.0
        assert m.mean_ratio < 1.0
        assert m.score > 0.0

    def test_beats_truncation_on_score(self):
        good = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        trunc = evaluate(TruncationCompactor(), _corpus())
        assert good.score > trunc.score

    def test_beats_noop_on_score(self):
        # The good SLM keeps all facts AND compresses, so composite score should
        # exceed noop's (which has perfect fidelity but no compression bonus).
        good = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        noop = evaluate(NoOpCompactor(), _corpus())
        assert good.score > noop.score

    def test_lossy_slm_scores_below_good_slm(self):
        good = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        lossy = evaluate(SLMCompactor(_lossy_summarize, preserve_recent=2), _corpus())
        assert good.score > lossy.score


# ============================================================================ #
# 10. run_arena with multiple compactors — picks best one                      #
# ============================================================================ #


class TestRunArena:
    def test_leaderboard_sorted_best_first(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(candidates, _corpus())
        scores = [m.score for m in result.leaderboard]
        assert scores == sorted(scores, reverse=True)
        assert len(result.leaderboard) == 3

    def test_picks_best_on_corpus(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(candidates, _corpus())
        assert result.winner == "slm"
        assert result.promoted is True

    def test_gates_on_incumbent_margin(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(
            candidates, _corpus(), incumbent="slm", min_improvement=0.5
        )
        # slm is already best; with a large required margin no challenger
        # unseats it.
        assert result.winner == "slm"
        assert result.promoted is False

    def test_gates_on_tiny_margin_allows_promotion(self):
        # Default min_improvement=0.0: even a tie doesn't promote (strict).
        # With min_improvement=0.01 and the good SLM measurably ahead, it should
        # promote over a weaker incumbent.
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(
            candidates, _corpus(), incumbent="truncate", min_improvement=0.01
        )
        assert result.winner == "slm"
        assert result.promoted is True

    def test_incumbent_not_in_pool_promotes_top(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(
            candidates,
            _corpus(),
            incumbent="ghost_not_in_pool",
            min_improvement=0.9,
        )
        assert result.incumbent == "ghost_not_in_pool"
        assert result.promoted is True
        assert result.winner == result.leaderboard[0].compactor

    def test_empty_candidates_returns_default(self):
        result = run_arena([], _corpus())
        assert result.leaderboard == []
        assert result.winner == ""
        assert result.promoted is False

    def test_all_fields_present(self):
        candidates = [NoOpCompactor(), TruncationCompactor()]
        result = run_arena(candidates, _corpus())
        assert result.leaderboard
        assert result.winner
        assert result.incumbent == ""
        assert isinstance(result.promoted, bool)
        assert isinstance(result.margin, float)

    def test_deterministic_with_same_inputs(self):
        candidates = [NoOpCompactor(), TruncationCompactor()]
        r1 = run_arena(candidates, _corpus())
        r2 = run_arena(candidates, _corpus())
        assert r1.winner == r2.winner
        assert r1.margin == pytest.approx(r2.margin)


# ============================================================================ #
# 11. SelfImprovingCompactor improves champion on a corpus                     #
# ============================================================================ #


class TestSelfImprovingCompactor:
    def test_promotes_better_champion(self):
        candidates = [
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        sic = SelfImprovingCompactor(
            candidates, champion=candidates[0], min_improvement=0.01
        )
        assert sic.champion.name == "truncate"
        result = sic.improve(_corpus())
        assert result.promoted is True
        assert sic.champion.name == "slm"

    def test_does_not_regress_optimal_champion(self):
        sic = build_self_improving_compactor(
            _good_summarize, min_improvement=0.0, champion_name="slm_r2"
        )
        before = sic.champion.name
        sic.improve(_corpus())
        assert sic.champion.name == before

    def test_champion_score_equals_or_exceeds_all_others(self):
        sic = build_self_improving_compactor(_good_summarize, min_improvement=0.0)
        result = sic.improve(_corpus())
        champion_score = next(
            m.score for m in result.leaderboard if m.compactor == sic.champion.name
        )
        assert champion_score == max(m.score for m in result.leaderboard)

    def test_used_as_compactor_directly(self):
        sic = build_self_improving_compactor(_good_summarize)
        r = sic.compact(CompactionRequest(messages=_long_context(), goal="test"))
        assert r.messages
        assert r.method

    def test_improve_returns_arena_result(self):
        sic = build_self_improving_compactor(_good_summarize)
        result = sic.improve(_corpus())
        assert result.leaderboard
        assert isinstance(result.promoted, bool)
        assert result.incumbent

    def test_empty_candidates_rejected(self):
        with pytest.raises(ValueError, match="at least one candidate"):
            SelfImprovingCompactor([])

    def test_champion_added_to_pool_if_missing(self):
        # If the champion was added externally and is not in the candidate list,
        # improve() must still include it in the pool so evaluation is complete.
        base = [TruncationCompactor(), NoOpCompactor()]
        champ = SLMCompactor(_good_summarize, preserve_recent=2)
        sic = SelfImprovingCompactor(base, champion=champ)
        result = sic.improve(_corpus())
        # The champion (slm) should appear in the leaderboard.
        names = {m.compactor for m in result.leaderboard}
        assert champ.name in names


# ============================================================================ #
# 12. build_self_improving_compactor factory function                          #
# ============================================================================ #


class TestBuildSelfImprovingCompactor:
    def test_returns_self_improving_compactor(self):
        sic = build_self_improving_compactor()
        assert isinstance(sic, SelfImprovingCompactor)
        assert sic.name == "self_improving"

    def test_default_champion_is_truncate(self):
        sic = build_self_improving_compactor()
        assert sic.champion.name == "truncate"

    def test_custom_champion_name(self):
        sic = build_self_improving_compactor(None, champion_name="noop")
        assert sic.champion.name == "noop"

    def test_unknown_champion_name_falls_back_to_first_candidate(self):
        # "bogus" is not in the default pool → falls back to candidates[0].
        sic = build_self_improving_compactor(None, champion_name="bogus")
        assert isinstance(sic.champion, NoOpCompactor)

    def test_with_summarize_fn(self):
        sic = build_self_improving_compactor(_good_summarize, champion_name="slm_r2")
        assert sic.champion.name == "slm_r2"
        # Must be able to compact without error.
        r = sic.compact(CompactionRequest(messages=_long_context(), goal="test"))
        assert r.messages

    def test_can_improve_after_building(self):
        sic = build_self_improving_compactor(_good_summarize)
        result = sic.improve(_corpus())
        assert result.leaderboard

    def test_min_improvement_respected(self):
        # With a huge min_improvement, no promotion occurs; champion stays.
        sic = build_self_improving_compactor(
            _good_summarize, min_improvement=0.99, champion_name="noop"
        )
        before = sic.champion.name
        sic.improve(_corpus())
        assert sic.champion.name == before


# ============================================================================ #
# 13. generate_candidates — all expected compactor types                       #
# ============================================================================ #


class TestGenerateCandidates:
    def test_includes_all_baseline_types(self):
        pool = generate_candidates(None)
        names = {c.name for c in pool}
        assert "noop" in names
        assert "truncate" in names
        assert "context_compactor" in names
        assert "slm_r2" in names
        assert "slm_r4" in names

    def test_count_offline(self):
        pool = generate_candidates(None)
        assert len(pool) == 5

    def test_with_summarize_fn_adds_context_compactor_slm(self):
        pool = generate_candidates(_good_summarize)
        names = {c.name for c in pool}
        assert "context_compactor_slm" in names
        # That variant uses the injected summarizer; call it to verify it works.
        adapter = next(c for c in pool if c.name == "context_compactor_slm")
        r = adapter.compact(CompactionRequest(messages=_long_context(), goal="test"))
        assert r.method

    def test_slm_candidates_use_injected_fn(self):
        pool = generate_candidates(_good_summarize)
        slm_candidates = [c for c in pool if c.name.startswith("slm_")]
        assert len(slm_candidates) >= 2
        for c in slm_candidates:
            r = c.compact(CompactionRequest(messages=_long_context(), goal="test"))
            assert r.method == c.name

    def test_noop_is_first_in_list(self):
        pool = generate_candidates(None)
        assert isinstance(pool[0], NoOpCompactor)


# ============================================================================ #
# 14. CompactionAggressivenessController.next_level — pure decision            #
# ============================================================================ #


class TestNextLevel:
    def _next(
        self,
        current_level: int,
        passed: int,
        total: int,
        *,
        floor: float = 0.9,
        min_samples: int = 20,
        max_level: int = 3,
    ) -> int:
        sample = AccuracySample(passed=passed, total=total)
        return CompactionAggressivenessController.next_level(
            current_level, sample,
            floor=floor, min_samples=min_samples, max_level=max_level,
        )

    # --- HOLD when sample.total < min_samples ---

    def test_hold_when_below_min_samples(self):
        # 19 < 20 → hold, regardless of good accuracy.
        assert self._next(1, 19, 19) == 1

    def test_hold_at_zero_with_few_samples(self):
        assert self._next(0, 10, 10) == 0

    def test_hold_at_max_with_few_samples(self):
        assert self._next(3, 18, 19) == 3

    # --- HOLD when rate is None ---

    def test_hold_when_rate_is_none(self):
        # total == 0 → rate is None.
        assert self._next(1, 0, 0) == 1

    def test_hold_when_total_zero_and_at_max(self):
        assert self._next(3, 0, 0) == 3

    # --- CLIMB one rung when rate >= floor and not at max ---

    def test_climb_when_good_accuracy(self):
        assert self._next(1, 20, 20) == 2  # 1.0 >= 0.9, climb

    def test_climb_from_zero(self):
        assert self._next(0, 19, 20) == 1  # 0.95 >= 0.9, climb

    def test_climb_rate_exactly_at_floor(self):
        assert self._next(1, 18, 20) == 2  # 0.9 >= 0.9, climb

    # --- HOLD when at max_level with good accuracy ---

    def test_hold_at_max_level_good_accuracy(self):
        assert self._next(3, 20, 20) == 3

    def test_hold_at_max_level_from_below(self):
        # Climbed from 2 → 3 previously; now at 3 with good accuracy → hold.
        assert self._next(3, 18, 20) == 3  # 0.9 >= 0.9 but at max=3

    # --- BACK OFF one rung when rate < floor ---

    def test_back_off_when_bad_accuracy(self):
        assert self._next(2, 15, 20) == 1  # 0.75 < 0.9, back off

    def test_back_off_from_max(self):
        assert self._next(3, 10, 20) == 2  # 0.5 < 0.9

    def test_back_off_from_level_one(self):
        assert self._next(1, 5, 20) == 0  # 0.25 < 0.9 → floor at 0

    # --- Never returns negative level ---

    def test_never_negative_when_back_off_from_zero(self):
        assert self._next(0, 10, 20) == 0  # bad accuracy but already at 0

    def test_never_negative_when_back_off_from_zero_terrible(self):
        assert self._next(0, 0, 20) == 0

    # --- Clamp: edge cases ---

    def test_below_zero_current_clamped_up(self):
        # next_level does arithmetic on the raw current_level, then clamps the
        # result: -5 < 3 is true, so _clamp(-5+1, 3) = _clamp(-4, 3) = 0.
        assert self._next(-5, 20, 20) == 0

    def test_above_max_current_clamped_down(self):
        assert self._next(999, 18, 20) == 3  # clamped, good acc, but at max → hold

    def test_above_max_with_bad_accuracy_clamped_and_back_off(self):
        # bad accuracy → back-off from 999: _clamp(999-1, 3) = _clamp(998, 3) = 3.
        assert self._next(999, 10, 20) == 3

    # --- Custom floor / min_samples ---

    def test_custom_floor(self):
        sample = AccuracySample(passed=16, total=20)  # rate=0.8
        # floor=0.85: 0.8 < 0.85 → back off
        assert CompactionAggressivenessController.next_level(
            2, sample, floor=0.85, min_samples=20, max_level=3
        ) == 1
        # floor=0.75: 0.8 >= 0.75 → climb
        assert CompactionAggressivenessController.next_level(
            2, sample, floor=0.75, min_samples=20, max_level=3
        ) == 3

    def test_custom_min_samples(self):
        # 10 samples with min_samples=10 → enough to climb.
        sample = AccuracySample(passed=10, total=10)
        assert CompactionAggressivenessController.next_level(
            1, sample, floor=0.9, min_samples=10, max_level=3
        ) == 2
        # 9 samples with min_samples=10 → hold.
        sample2 = AccuracySample(passed=9, total=9)
        assert CompactionAggressivenessController.next_level(
            1, sample2, floor=0.9, min_samples=10, max_level=3
        ) == 1

    def test_custom_max_level(self):
        sample = AccuracySample(passed=20, total=20)
        # max_level=1: climb from 0→1, then hold.
        assert CompactionAggressivenessController.next_level(
            0, sample, floor=0.9, min_samples=20, max_level=1
        ) == 1
        assert CompactionAggressivenessController.next_level(
            1, sample, floor=0.9, min_samples=20, max_level=1
        ) == 1

    # --- Determinism ---

    def test_deterministic(self):
        sample = AccuracySample(passed=18, total=20)
        a = CompactionAggressivenessController.next_level(
            2, sample, floor=0.9, min_samples=20, max_level=3
        )
        b = CompactionAggressivenessController.next_level(
            2, sample, floor=0.9, min_samples=20, max_level=3
        )
        assert a == b


# ============================================================================ #
# 15. AccuracySample rate calculation                                          #
# ============================================================================ #


class TestAccuracySample:
    def test_rate_normal(self):
        sample = AccuracySample(passed=8, total=10)
        assert sample.rate == 0.8

    def test_rate_all_passed(self):
        sample = AccuracySample(passed=10, total=10)
        assert sample.rate == 1.0

    def test_rate_all_failed(self):
        sample = AccuracySample(passed=0, total=10)
        assert sample.rate == 0.0

    def test_rate_none_when_total_zero(self):
        sample = AccuracySample(passed=0, total=0)
        assert sample.rate is None

    def test_rate_none_when_total_negative(self):
        sample = AccuracySample(passed=0, total=-1)
        assert sample.rate is None

    def test_rate_clamped_when_passed_exceeds_total(self):
        sample = AccuracySample(passed=15, total=10)
        assert sample.rate == 1.0

    def test_rate_clamped_when_passed_negative(self):
        sample = AccuracySample(passed=-5, total=10)
        assert sample.rate == 0.0

    def test_fields_immutable(self):
        sample = AccuracySample(passed=5, total=10)
        with pytest.raises(AttributeError):
            sample.passed = 7  # type: ignore[misc]  # frozen dataclass: tests immutability


# ============================================================================ #
# 16. CompactionAggressivenessController.disable_signaled                      #
# ============================================================================ #


class TestDisableSignaled:
    def test_false_when_level_above_zero(self):
        ctrl = CompactionAggressivenessController(min_samples=10)
        sample = AccuracySample(passed=5, total=10)
        assert ctrl.disable_signaled(1, sample) is False

    def test_false_when_below_min_samples(self):
        ctrl = CompactionAggressivenessController(min_samples=10)
        sample = AccuracySample(passed=5, total=9)
        assert ctrl.disable_signaled(0, sample) is False

    def test_false_when_rate_is_none(self):
        ctrl = CompactionAggressivenessController(min_samples=10)
        sample = AccuracySample(passed=0, total=0)
        assert ctrl.disable_signaled(0, sample) is False

    def test_false_when_accuracy_good(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=10)
        sample = AccuracySample(passed=10, total=10)  # rate=1.0 >= 0.9
        assert ctrl.disable_signaled(0, sample) is False

    def test_true_when_at_zero_and_accuracy_below_floor(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=10)
        sample = AccuracySample(passed=5, total=10)  # rate=0.5 < 0.9
        assert ctrl.disable_signaled(0, sample) is True

    def test_true_with_enough_samples_and_bad_rate(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=20)
        sample = AccuracySample(passed=10, total=25)  # rate=0.4 < 0.9
        assert ctrl.disable_signaled(0, sample) is True

    def test_false_when_accuracy_exactly_at_floor(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=10)
        sample = AccuracySample(passed=9, total=10)  # rate=0.9
        assert ctrl.disable_signaled(0, sample) is False

    def test_defaults(self):
        # Default min_samples=20, floor=0.9.
        ctrl = CompactionAggressivenessController()
        assert ctrl.min_samples == 20
        assert ctrl.floor == 0.9
        assert ctrl.max_level == len(LEVELS) - 1


# ============================================================================ #
# 17. Full end-to-end flow: AccuracySample → compute → next_level              #
# ============================================================================ #


class TestFullE2EFlow:
    def test_ascend_then_hold_then_back_off(self):
        """Simulate: start aggressive, climb as accuracy holds, hold at max,
        then back off when accuracy drops."""
        ctrl = CompactionAggressivenessController(
            floor=0.9, min_samples=20, max_level=3
        )

        level = 0
        # Phase 1: good accuracy → climb to max.
        for expected in [1, 2, 3, 3]:
            sample = AccuracySample(passed=20, total=20)
            level = ctrl.compute(level, sample)
            assert level == expected

        # Phase 2: accuracy drops → back off step by step.
        bad = AccuracySample(passed=10, total=30)  # rate=0.33
        level = ctrl.compute(level, bad)
        assert level == 2
        level = ctrl.compute(level, bad)
        assert level == 1
        level = ctrl.compute(level, bad)
        assert level == 0

    def test_disable_signaled_after_full_regression(self):
        """When accuracy stays bad even at level 0, signal disable."""
        ctrl = CompactionAggressivenessController(
            floor=0.9, min_samples=20, max_level=3
        )

        # Climb to level 2 with good accuracy.
        level = 0
        good = AccuracySample(passed=20, total=20)
        level = ctrl.compute(level, good)  # 1
        level = ctrl.compute(level, good)  # 2

        # Then accuracy collapses → back off to 0.
        bad = AccuracySample(passed=5, total=30)
        level = ctrl.compute(level, bad)  # 1
        level = ctrl.compute(level, bad)  # 0

        # At level 0 with bad accuracy and enough samples → disable.
        assert ctrl.disable_signaled(level, bad) is True

    def test_never_disabled_with_good_accuracy(self):
        ctrl = CompactionAggressivenessController(
            floor=0.9, min_samples=20, max_level=3
        )

        level = 0
        for _ in range(10):
            sample = AccuracySample(passed=19, total=20)  # 0.95
            level = ctrl.compute(level, sample)
            assert ctrl.disable_signaled(level, sample) is False

    def test_recovery_after_disable(self):
        """After accuracy recovers at level 0, disable is no longer signaled
        and the controller can climb again."""
        ctrl = CompactionAggressivenessController(
            floor=0.9, min_samples=20, max_level=3
        )

        level = 0
        bad = AccuracySample(passed=5, total=30)
        assert ctrl.disable_signaled(level, bad) is True

        # Accuracy recovers — disable should become false.
        good = AccuracySample(passed=28, total=30)
        assert ctrl.disable_signaled(level, good) is False

        # And the controller can climb from 0 again.
        level = ctrl.compute(level, good)
        assert level == 1

    def test_level_always_in_bounds(self):
        """No matter the inputs, compute() never returns outside [0, max_level]."""
        ctrl = CompactionAggressivenessController(
            floor=0.95, min_samples=20, max_level=3
        )

        samples = [
            AccuracySample(passed=20, total=20),
            AccuracySample(passed=0, total=20),
            AccuracySample(passed=19, total=20),
            AccuracySample(passed=0, total=0),
            AccuracySample(passed=5, total=5),
        ]
        level = 0
        for _ in range(50):
            s = samples[_ % len(samples)]
            level = ctrl.compute(level, s)
            assert 0 <= level <= ctrl.max_level

    def test_level_always_in_bounds_with_custom_max(self):
        """compute() clamps correctly even with a custom max_level."""
        ctrl = CompactionAggressivenessController(
            floor=0.5, min_samples=5, max_level=10
        )
        level = 0
        for _i in range(30):
            s = AccuracySample(passed=10, total=10)
            level = ctrl.compute(level, s)
            assert 0 <= level <= 10

    def test_level_always_in_bounds_even_with_negative_value(self):
        """Passing a negative current_level to compute() must still return >= 0
        regardless of sample quality."""
        ctrl = CompactionAggressivenessController(
            floor=0.9, min_samples=5, max_level=3
        )

        good = AccuracySample(passed=10, total=10)
        result = ctrl.compute(-5, good)
        assert result >= 0
        assert result <= ctrl.max_level

        bad = AccuracySample(passed=0, total=10)
        result = ctrl.compute(-5, bad)
        assert result >= 0


# ============================================================================ #
# Aggressive module: LEVELS and level_at                                       #
# ============================================================================ #


class TestAggressiveLevels:
    def test_levels_are_four_rungs(self):
        assert len(LEVELS) == 4

    def test_levels_monotonically_more_aggressive(self):
        # Each successive level preserves fewer recent turns.
        for i in range(len(LEVELS) - 1):
            assert LEVELS[i].preserve_recent >= LEVELS[i + 1].preserve_recent

    def test_levels_thresholds_monotonically_lower(self):
        # Each successive level starts compacting sooner (lower threshold).
        for i in range(len(LEVELS) - 1):
            assert LEVELS[i].threshold >= LEVELS[i + 1].threshold

    def test_level_at_valid_indices(self):
        assert level_at(0) is LEVELS[0]
        assert level_at(1) is LEVELS[1]
        assert level_at(2) is LEVELS[2]
        assert level_at(3) is LEVELS[3]

    def test_level_at_out_of_range_clamps(self):
        assert level_at(-1) is LEVELS[0]
        assert level_at(-100) is LEVELS[0]
        assert level_at(999) is LEVELS[3]
        assert level_at(100) is LEVELS[3]

    def test_level_at_garbage_input_clamps(self):
        # None / string / float('inf') all clamp to the default rung (index 1).
        import math

        assert level_at("abc") is LEVELS[1]  # type: ignore[arg-type]  # test stub: deliberately wrong type to exercise clamping
        assert level_at(None) is LEVELS[1]  # type: ignore[arg-type]  # test stub: deliberately wrong type to exercise clamping
        assert level_at(math.inf) is LEVELS[1]  # type: ignore[arg-type]  # test stub: deliberately wrong type to exercise clamping

    def test_level_at_zero_preserves_most(self):
        lvl = level_at(0)
        assert lvl.preserve_recent == 8
        assert lvl.threshold == pytest.approx(0.9)

    def test_compaction_level_is_frozen(self):
        lvl = CompactionLevel(preserve_recent=4, threshold=0.8)
        with pytest.raises(AttributeError):
            lvl.preserve_recent = 2  # type: ignore[misc]  # frozen dataclass: tests immutability


# ============================================================================ #
# ContextCompactorAdapter integration edge cases                               #
# ============================================================================ #


class TestContextCompactorAdapter:
    def test_custom_name_is_reflected(self):
        adapter = ContextCompactorAdapter(
            preserve_recent_count=2, summary_fn=None, name="my_custom_adapter"
        )
        assert adapter.name == "my_custom_adapter"
        r = adapter.compact(CompactionRequest(messages=_long_context()))
        assert r.method == "my_custom_adapter"

    def test_with_summary_fn(self):
        def summary_fn(text: str) -> str:
            return "custom summary of text"

        adapter = ContextCompactorAdapter(
            preserve_recent_count=2, summary_fn=summary_fn
        )
        r = adapter.compact(CompactionRequest(messages=_long_context(), goal="test"))
        assert r.method
        assert r.compacted_tokens <= r.original_tokens


# ============================================================================ #
# CompactionMetrics field propagation                                          #
# ============================================================================ #


class TestCompactionMetrics:
    def test_all_fields_on_non_empty_corpus(self):
        metrics = evaluate(NoOpCompactor(), _corpus())
        assert metrics.compactor == "noop"
        assert metrics.samples == len(_corpus())
        assert isinstance(metrics.mean_ratio, float)
        assert isinstance(metrics.mean_fidelity, float)
        assert isinstance(metrics.mean_tokens_saved, float)
        assert isinstance(metrics.score, float)

    def test_score_identity_for_noop(self):
        # noop: fidelity=1.0, ratio=1.0 → score = 0.7*1.0 + 0.3*0.0 = 0.7
        metrics = evaluate(NoOpCompactor(), _corpus())
        assert metrics.score == pytest.approx(0.7)

    def test_score_between_zero_and_one(self):
        compactors = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
            SLMCompactor(_lossy_summarize, preserve_recent=2),
        ]
        for c in compactors:
            m = evaluate(c, _corpus())
            assert 0.0 <= m.score <= 1.0


# ============================================================================ #
# Edge cases: tiny / empty inputs across compactors                            #
# ============================================================================ #


class TestEdgeCases:
    def test_single_system_message_noop(self):
        msgs = [_msg("system", role="system", system=True)]
        r = NoOpCompactor().compact(CompactionRequest(messages=msgs))
        assert r.messages == msgs
        assert r.ratio == 1.0

    def test_single_system_message_truncate(self):
        msgs = [_msg("system", role="system", system=True)]
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=2)
        )
        assert len(r.messages) == 1
        assert r.messages[0].is_system

    def test_single_system_message_slm(self):
        msgs = [_msg("system", role="system", system=True)]
        r = SLMCompactor(_good_summarize).compact(CompactionRequest(messages=msgs))
        assert r.messages == msgs
        assert r.dropped_messages == 0

    def test_large_number_of_messages(self):
        msgs = [_msg("system", role="system", system=True)]
        for i in range(100):
            msgs.append(_msg(f"message number {i} with some extra padding text"))
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=5)
        )
        assert len(r.messages) < len(msgs)
        # System message preserved.
        assert r.messages[0].is_system
        # Recent 5 non-system messages preserved.
        recent_content = [m.content for m in r.messages if not m.is_system]
        assert len(recent_content) == 5
        assert "message number 99" in recent_content[-1]

    def test_no_non_system_messages(self):
        msgs = [
            _msg("sys_a", role="system", system=True),
            _msg("sys_b", role="system", system=True),
        ]
        for compactor in [NoOpCompactor(), TruncationCompactor(),
                          SLMCompactor(_good_summarize)]:
            r = compactor.compact(
                CompactionRequest(messages=msgs, preserve_recent=2)
            )
            assert len(r.messages) == 2


# ============================================================================ #
# Arena tie-breaking: fidelity and compression used for order                  #
# ============================================================================ #


class TestArenaTieBreaking:
    def test_ties_broken_by_fidelity_then_compression(self):
        # Construct a corpus where two compactors get the same score; arena
        # must tie-break by fidelity first, then by compression ratio.
        corpus = _corpus()
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
            SLMCompactor(_lossy_summarize, preserve_recent=2),
        ]
        result = run_arena(candidates, corpus)
        # Verify the leaderboard order: for every adjacent pair, the higher
        # entry must not be strictly worse on all three sort keys.
        for i in range(len(result.leaderboard) - 1):
            upper = result.leaderboard[i]
            lower = result.leaderboard[i + 1]
            assert upper.score >= lower.score
            if upper.score == lower.score:
                assert upper.mean_fidelity >= lower.mean_fidelity
                if upper.mean_fidelity == lower.mean_fidelity:
                    # Lower ratio = more compression = better → which means
                    # higher `-mean_ratio` in the sort key.
                    assert upper.mean_ratio <= lower.mean_ratio

    def test_deterministic_ranking(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        r1 = run_arena(candidates, _corpus())
        r2 = run_arena(candidates, _corpus())
        names1 = [m.compactor for m in r1.leaderboard]
        names2 = [m.compactor for m in r2.leaderboard]
        assert names1 == names2
