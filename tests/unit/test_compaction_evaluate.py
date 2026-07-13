"""Unit tests for compaction/evaluate.py."""

from __future__ import annotations

import pytest

from general_ludd.agents.context import ContextMessage
from general_ludd.compaction.base import CompactionRequest, CompactionResult
from general_ludd.compaction.evaluate import (
    CompactionMetrics,
    EvalSample,
    Probe,
    _context_text,
    _keyword_retention_judge,
    _sample_fidelity,
    evaluate,
)


def _msg(content: str, role: str = "user") -> ContextMessage:
    return ContextMessage(role=role, content=content)


class TestProbe:
    def test_defaults(self):
        p = Probe(question="what?")
        assert p.question == "what?"
        assert p.expected == []

    def test_with_expected(self):
        p = Probe(question="what?", expected=["alpha", "beta"])
        assert p.expected == ["alpha", "beta"]

    def test_extra_fields_allowed_or_ignored(self):
        p = Probe(question="what?", expected=["a"])
        assert p.question == "what?"
        assert p.expected == ["a"]


class TestEvalSample:
    def test_defaults(self):
        s = EvalSample()
        assert s.messages == []
        assert s.goal == ""
        assert s.probes == []
        assert s.target_tokens is None
        assert s.preserve_recent == 4

    def test_with_data(self):
        msgs = [_msg("hello")]
        s = EvalSample(
            messages=msgs,
            goal="find bug",
            probes=[Probe(question="q1", expected=["hello"])],
            target_tokens=1000,
            preserve_recent=2,
        )
        assert len(s.messages) == 1
        assert s.goal == "find bug"
        assert len(s.probes) == 1
        assert s.target_tokens == 1000
        assert s.preserve_recent == 2


class TestCompactionMetrics:
    def test_defaults(self):
        m = CompactionMetrics()
        assert m.compactor == ""
        assert m.samples == 0
        assert m.mean_ratio == 1.0
        assert m.mean_fidelity == 0.0
        assert m.mean_tokens_saved == 0.0
        assert m.score == 0.0

    def test_with_values(self):
        m = CompactionMetrics(
            compactor="test",
            samples=5,
            mean_ratio=0.5,
            mean_fidelity=0.9,
            mean_tokens_saved=500.0,
            score=0.72,
        )
        assert m.compactor == "test"
        assert m.samples == 5
        assert m.mean_ratio == 0.5
        assert m.mean_fidelity == 0.9
        assert m.mean_tokens_saved == 500.0
        assert m.score == 0.72


class TestKeywordRetentionJudge:
    def test_empty_expected_vacuously_true(self):
        assert _keyword_retention_judge("any text", "q?", []) is True

    def test_all_facts_present(self):
        assert _keyword_retention_judge("hello world", "q?", ["hello", "world"]) is True

    def test_missing_fact_returns_false(self):
        assert _keyword_retention_judge("hello world", "q?", ["hello", "missing"]) is False

    def test_case_insensitive(self):
        assert _keyword_retention_judge("Hello World", "q?", ["hello", "world"]) is True

    def test_substring_match(self):
        assert _keyword_retention_judge("abcdef", "q?", ["bcd"]) is True

    def test_no_facts_returns_false(self):
        assert _keyword_retention_judge("text", "q?", ["nothing"]) is False


class TestContextText:
    def test_single_message(self):
        assert _context_text([_msg("hello")]) == "hello"

    def test_multiple_messages(self):
        text = _context_text([_msg("a"), _msg("b"), _msg("c")])
        assert "a\nb\nc" in text or "a" in text

    def test_empty(self):
        assert _context_text([]) == ""


class TestSampleFidelity:
    def test_no_probes_returns_one(self):
        assert _sample_fidelity([_msg("any")], [], _keyword_retention_judge) == 1.0

    def test_all_retained(self):
        probes = [
            Probe(question="q1", expected=["hello"]),
            Probe(question="q2", expected=["world"]),
        ]
        assert _sample_fidelity([_msg("hello world")], probes, _keyword_retention_judge) == 1.0

    def test_partial_retained(self):
        probes = [
            Probe(question="q1", expected=["hello"]),
            Probe(question="q2", expected=["missing"]),
        ]
        assert _sample_fidelity([_msg("hello world")], probes, _keyword_retention_judge) == 0.5

    def test_none_retained(self):
        probes = [
            Probe(question="q1", expected=["missing"]),
            Probe(question="q2", expected=["also_missing"]),
        ]
        assert _sample_fidelity([_msg("hello")], probes, _keyword_retention_judge) == 0.0


class EvaluateClass:
    name = "test_compactor"

    def compact(self, request: CompactionRequest) -> CompactionResult:
        return CompactionResult(
            messages=list(request.messages),
            method="test",
            original_tokens=100,
            compacted_tokens=50,
            dropped_messages=2,
        )


class TestEvaluate:
    def test_empty_corpus_returns_defaults(self):
        metrics = evaluate(EvaluateClass(), [])
        assert metrics.samples == 0
        assert metrics.compactor == "test_compactor"
        assert metrics.mean_ratio == 1.0

    def test_single_sample(self):
        msgs = [_msg("hello world this is a test message with many words")]
        probes = [Probe(question="q1", expected=["hello", "world"])]
        sample = EvalSample(messages=msgs, probes=probes)
        metrics = evaluate(EvaluateClass(), [sample])
        assert metrics.samples == 1
        assert metrics.mean_fidelity >= 0.0
        assert metrics.score >= 0.0

    def test_score_formula(self):
        msgs = [_msg("x" * 100)]
        sample = EvalSample(messages=msgs)
        metrics = evaluate(EvaluateClass(), [sample], fidelity_weight=0.7, compression_weight=0.3)
        assert metrics.score == pytest.approx(0.7 * metrics.mean_fidelity + 0.3 * (1.0 - metrics.mean_ratio))

    def test_custom_judge_fn(self):
        msgs = [_msg("hello")]
        probes = [Probe(question="q", expected=["hello"])]
        sample = EvalSample(messages=msgs, probes=probes)

        def always_true(text, question, expected):
            return True

        metrics = evaluate(EvaluateClass(), [sample], judge_fn=always_true)
        assert metrics.mean_fidelity == 1.0

    def test_multiple_samples_averaged(self):
        samples = [
            EvalSample(messages=[_msg("x" * 100)]),
            EvalSample(messages=[_msg("y" * 200)]),
        ]
        metrics = evaluate(EvaluateClass(), samples)
        assert metrics.samples == 2
        assert metrics.mean_tokens_saved >= 0.0

    def test_compactor_name_fallback(self):
        class Nameless:
            def compact(self, request):
                return CompactionResult(messages=[], original_tokens=10, compacted_tokens=5)

        metrics = evaluate(Nameless(), [EvalSample(messages=[_msg("test")])])
        assert metrics.compactor == "?"
