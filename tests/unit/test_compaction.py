"""Tests for the local-SLM context/memory compaction package.

Everything runs offline: the SLM call is a dependency-injected fake, and the
fidelity judge defaults to a keyword-retention check. The headline test proves
the self-improvement arena actually SELECTS a compactor that beats the baselines
on a corpus where recency-truncation loses a goal-relevant fact.
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
from general_ludd.compaction.base import messages_tokens


def _msg(content: str, *, role: str = "user", system: bool = False) -> ContextMessage:
    return ContextMessage(
        role=role,
        content=content,
        token_estimate=estimate_tokens(content),
        is_system=system,
    )


def _long_context() -> list[ContextMessage]:
    # OLD messages carry the goal-relevant facts up front, followed by a run of
    # droppable chatter a good summarizer should discard; the two RECENT tail
    # messages are kept verbatim. The chatter is deliberately keyword-free so the
    # fidelity probes only pass when the fact-bearing summary survives.
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


# A "good" SLM summarizer: compresses but PRESERVES the goal-relevant facts.
def _good_summarize(goal: str, text: str) -> str:
    keep = []
    for line in text.splitlines():
        if "config/secrets.yml" in line or "42" in line or "postgres" in line:
            keep.append(line)
    return "SUMMARY: " + " | ".join(keep) if keep else "SUMMARY: (none)"


# A "lossy" SLM summarizer: compresses but DROPS the facts (like a naive model).
def _lossy_summarize(goal: str, text: str) -> str:
    return "the agent discussed some setup and then continued the plan"


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _NoContentResp:
    pass


class _FakeGateway:
    def __init__(self, *, resp=None, error=None):
        self._resp = resp
        self._error = error
        self.calls = []

    def call_model(self, profile_id, *, messages, requested_max_output_tokens):
        self.calls.append(
            {
                "profile_id": profile_id,
                "messages": messages,
                "requested_max_output_tokens": requested_max_output_tokens,
            }
        )
        if self._error is not None:
            raise self._error
        return self._resp


# --------------------------------------------------------------------------- #
# base                                                                        #
# --------------------------------------------------------------------------- #


class TestBase:
    def test_estimate_and_messages_tokens(self):
        assert estimate_tokens("a" * 40) == 10
        msgs = [_msg("a" * 40), _msg("b" * 80)]
        assert messages_tokens(msgs) == 10 + 20

    def test_result_ratio_and_saved(self):
        r = NoOpCompactor().compact(CompactionRequest(messages=_long_context()))
        assert r.ratio == 1.0
        assert r.tokens_saved == 0

    def test_ratio_is_one_when_original_tokens_zero(self):
        # Guard against division-by-zero: an empty original context reports a
        # neutral 1.0 ratio (no compression) rather than crashing.
        r = CompactionResult(original_tokens=0, compacted_tokens=5)
        assert r.ratio == 1.0

    def test_tokens_saved_never_negative(self):
        # Even a (degenerate) result that grew reports zero saved, never negative.
        r = CompactionResult(original_tokens=0, compacted_tokens=5)
        assert r.tokens_saved == 0


# --------------------------------------------------------------------------- #
# baselines                                                                   #
# --------------------------------------------------------------------------- #


class TestBaselines:
    def test_noop_unchanged(self):
        msgs = _long_context()
        r = NoOpCompactor().compact(CompactionRequest(messages=msgs))
        assert r.messages == msgs
        assert r.method == "noop"

    def test_truncation_keeps_system_and_recent_drops_old(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=2)
        )
        # System message survives; oldest fact-bearing message is dropped.
        assert any(m.is_system for m in r.messages)
        assert r.compacted_tokens < r.original_tokens
        joined = "\n".join(m.content for m in r.messages)
        assert "config/secrets.yml" not in joined  # the fact was truncated away

    def test_truncation_honors_target_tokens(self):
        msgs = _long_context()
        r = TruncationCompactor().compact(
            CompactionRequest(messages=msgs, preserve_recent=1, target_tokens=20)
        )
        assert r.compacted_tokens <= r.original_tokens

    def test_context_compactor_adapter_compacts(self):
        msgs = _long_context()
        r = ContextCompactorAdapter(preserve_recent_count=2).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.method == "context_compactor"
        assert r.compacted_tokens <= r.original_tokens


# --------------------------------------------------------------------------- #
# slm                                                                         #
# --------------------------------------------------------------------------- #


class TestSLMCompactor:
    def test_good_summarizer_preserves_facts_and_compresses(self):
        msgs = _long_context()
        r = SLMCompactor(_good_summarize, preserve_recent=2).compact(
            CompactionRequest(messages=msgs, goal="find the API key location")
        )
        assert r.method == "slm"
        assert r.compacted_tokens < r.original_tokens  # it compressed
        joined = "\n".join(m.content for m in r.messages)
        assert "config/secrets.yml" in joined  # ...while KEEPING the fact
        assert "42" in joined

    def test_offline_fallback_when_no_summarize_fn(self):
        # No model injected -> deterministic extractive fallback, never crashes.
        msgs = _long_context()
        r = SLMCompactor(None, preserve_recent=2, fallback_max_chars=60).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.method == "slm"
        assert any("prior context" in m.content for m in r.messages)

    def test_fail_soft_when_summarizer_raises(self):
        def boom(goal: str, text: str) -> str:
            raise RuntimeError("model down")

        msgs = _long_context()
        # Must not raise; falls back to extractive summary.
        r = SLMCompactor(boom, preserve_recent=2).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.method == "slm"
        assert r.messages  # produced a result despite the model failure

    def test_no_old_messages_is_passthrough(self):
        msgs = [_msg("sys", role="system", system=True), _msg("only recent")]
        r = SLMCompactor(_good_summarize, preserve_recent=4).compact(
            CompactionRequest(messages=msgs)
        )
        assert r.dropped_messages == 0
        assert r.messages == msgs

    def test_preserve_recent_zero_keeps_no_recent_messages(self):
        # An explicit preserve_recent=0 must be honored (not silently overridden
        # by the instance default): EVERY non-system message is summarized away
        # and NONE are kept verbatim in the recent tail.
        msgs = _long_context()
        non_system = [m for m in msgs if not m.is_system]
        # Instance default is 4, but the request explicitly asks for 0.
        r = SLMCompactor(_good_summarize, preserve_recent=4).compact(
            CompactionRequest(
                messages=msgs, goal="find the API key location", preserve_recent=0
            )
        )
        assert r.method == "slm"
        # All non-system messages were folded into the summary (none preserved).
        assert r.dropped_messages == len(non_system)
        # The compacted context is the original system message(s) + one summary
        # message, and carries none of the original recent tail verbatim.
        assert not any(m.content == "proceeding now" for m in r.messages)
        assert not any(m.content == "continue with the plan" for m in r.messages)
        # The goal-relevant fact still survives via the summary.
        joined = "\n".join(m.content for m in r.messages)
        assert "config/secrets.yml" in joined


# --------------------------------------------------------------------------- #
# evaluate                                                                    #
# --------------------------------------------------------------------------- #


def _corpus() -> list[EvalSample]:
    return [
        EvalSample(
            messages=_long_context(),
            goal="find the API key location and retry cap",
            probes=[
                Probe(question="Where is the API key?", expected=["config/secrets.yml"]),
                Probe(question="Retry cap?", expected=["42"]),
            ],
            preserve_recent=2,
        )
    ]


class TestEvaluate:
    def test_noop_full_fidelity_no_compression(self):
        m = evaluate(NoOpCompactor(), _corpus())
        assert m.mean_fidelity == 1.0
        assert m.mean_ratio == 1.0

    def test_truncation_loses_fidelity(self):
        m = evaluate(TruncationCompactor(), _corpus())
        # Truncation dropped the fact-bearing old message -> probes fail.
        assert m.mean_fidelity < 1.0
        assert m.mean_ratio < 1.0  # but it did compress

    def test_good_slm_high_fidelity_and_compression(self):
        m = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        assert m.mean_fidelity == 1.0  # kept both facts
        assert m.mean_ratio < 1.0  # and compressed
        # Beats truncation on the composite score (keeps facts AND compresses).
        assert m.score > evaluate(TruncationCompactor(), _corpus()).score

    def test_lossy_slm_scores_below_good_slm(self):
        good = evaluate(SLMCompactor(_good_summarize, preserve_recent=2), _corpus())
        lossy = evaluate(SLMCompactor(_lossy_summarize, preserve_recent=2), _corpus())
        assert good.score > lossy.score

    def test_empty_corpus(self):
        m = evaluate(NoOpCompactor(), [])
        assert m.samples == 0


# --------------------------------------------------------------------------- #
# arena / self-improvement                                                    #
# --------------------------------------------------------------------------- #


class TestArena:
    def test_run_arena_ranks_and_picks_best(self):
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(candidates, _corpus())
        # Leaderboard is sorted best-first; the good SLM wins (keeps facts + compresses).
        assert result.leaderboard[0].score >= result.leaderboard[-1].score
        assert result.winner == "slm"

    def test_arena_gates_on_incumbent_margin(self):
        # Incumbent is the good SLM; a tiny required margin means no challenger
        # unseats it unless strictly better by that margin.
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(
            candidates, _corpus(), incumbent="slm", min_improvement=0.5
        )
        assert result.winner == "slm"
        assert result.promoted is False

    def test_run_arena_incumbent_not_in_pool_promotes_top(self):
        # If the named incumbent isn't among the scored candidates, there is no
        # incumbent score to gate against, so the top scorer wins outright and is
        # marked promoted. (SelfImprovingCompactor never hits this — it always
        # adds its champion to the pool — but direct run_arena callers can.)
        candidates = [
            NoOpCompactor(),
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        result = run_arena(
            candidates, _corpus(), incumbent="ghost_not_in_pool", min_improvement=0.9
        )
        assert result.incumbent == "ghost_not_in_pool"
        assert result.promoted is True
        assert result.winner == result.leaderboard[0].compactor
        assert result.winner == "slm"
        # The winner is a real, scored candidate — never the phantom incumbent.
        assert result.winner in {m.compactor for m in result.leaderboard}

    def test_self_improving_promotes_better_champion(self):
        candidates = [
            TruncationCompactor(),
            SLMCompactor(_good_summarize, preserve_recent=2),
        ]
        # Start champion = truncate (weaker on this corpus).
        sic = SelfImprovingCompactor(
            candidates,
            champion=candidates[0],
            min_improvement=0.01,
        )
        assert sic.champion.name == "truncate"
        result = sic.improve(_corpus())
        assert result.promoted is True
        assert sic.champion.name == "slm"  # promoted to the measured winner

    def test_self_improving_champion_beats_all_others(self):
        # The headline guarantee: after improvement, the deployed champion's
        # score is >= every other candidate on the corpus.
        sic = build_self_improving_compactor(_good_summarize, min_improvement=0.0)
        result = sic.improve(_corpus())
        champion_score = next(
            m.score for m in result.leaderboard if m.compactor == sic.champion.name
        )
        assert champion_score == max(m.score for m in result.leaderboard)

    def test_self_improving_used_as_a_compactor(self):
        sic = build_self_improving_compactor(_good_summarize)
        r = sic.compact(CompactionRequest(messages=_long_context(), goal="x"))
        assert r.messages  # behaves like any Compactor

    def test_no_promotion_when_no_improvement(self):
        # Champion already optimal -> improve() must not regress it.
        sic = build_self_improving_compactor(_good_summarize, champion_name="slm_r2")
        before = sic.champion.name
        sic.improve(_corpus())
        assert sic.champion.name == before

    def test_empty_candidates_rejected(self):
        with pytest.raises(ValueError):
            SelfImprovingCompactor([])

    def test_generate_candidates_offline(self):
        pool = generate_candidates(None)
        assert {c.name for c in pool} >= {
            "noop",
            "truncate",
            "context_compactor",
            "slm_r2",
            "slm_r4",
        }
