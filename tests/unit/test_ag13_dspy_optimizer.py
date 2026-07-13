"""Unit tests for ag13_dspy/optimizer.py — PromptOptimizer, mutation strategies, metrics."""

from __future__ import annotations

import math

import pytest

from general_ludd.ag13_dspy.optimizer import (
    MUTATION_STRATEGIES,
    PromptOptimizer,
    _contains_all,
    _exact_match,
    _mutate_reorder,
    _mutate_reword,
    _mutate_trim,
    _resolve_metric,
    _semantic_similarity,
)
from general_ludd.ag13_dspy.registry import PromptSpec, PromptTemplate


class TestMutateReorder:
    def test_swaps_lines(self):
        result = _mutate_reorder("line1\nline2\nline3")
        assert result.count("\n") == 2
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_single_line_unchanged(self):
        text = "only one line"
        assert _mutate_reorder(text) == text

    def test_empty_unchanged(self):
        assert _mutate_reorder("") == ""

    def test_two_lines(self):
        import random
        random.seed(42)
        result = _mutate_reorder("a\nb")
        assert result in ("a\nb", "b\na")


class TestMutateReword:
    def test_replaces_known_words(self):
        result = _mutate_reword("Classify the text and summarize it")
        assert "Categorize" in result
        assert "condense" in result

    def test_no_match_unchanged(self):
        text = "do something unique"
        assert _mutate_reword(text) == text

    def test_empty_unchanged(self):
        assert _mutate_reword("") == ""


class TestMutateTrim:
    def test_removes_one_line(self):
        result = _mutate_trim("line1\nline2\nline3")
        assert result.count("\n") == 1

    def test_single_line_unchanged(self):
        text = "only one line"
        assert _mutate_trim(text) == text

    def test_empty_unchanged(self):
        assert _mutate_trim("") == ""


class TestExactMatch:
    def test_match(self):
        assert _exact_match("", "hello", "hello") == 1.0

    def test_no_match(self):
        assert _exact_match("", "hello", "world") == 0.0

    def test_whitespace_ignored(self):
        assert _exact_match("", "  hello  ", "hello") == 1.0


class TestContainsAll:
    def test_all_tokens_present(self):
        assert _contains_all("", "hello world", "hello world") == 1.0

    def test_partial_tokens(self):
        assert _contains_all("", "hello world", "hello") == 0.5

    def test_no_tokens(self):
        assert _contains_all("", "hello world", "xyz") == 0.0

    def test_empty_expected(self):
        assert _contains_all("", "", "anything") == 1.0


class TestSemanticSimilarity:
    def test_full_overlap(self):
        assert _semantic_similarity("", "hello world", "hello world") == 1.0

    def test_partial_overlap(self):
        assert _semantic_similarity("", "hello world", "hello") == 0.5

    def test_no_overlap(self):
        assert _semantic_similarity("", "hello world", "xyz abc") == 0.0

    def test_empty_expected(self):
        assert _semantic_similarity("", "", "anything") == 1.0


class TestResolveMetric:
    def test_by_name(self):
        fn = _resolve_metric("exact_match")
        assert fn is _exact_match

    def test_by_name_contains_all(self):
        fn = _resolve_metric("contains_all")
        assert fn is _contains_all

    def test_by_name_semantic_similarity(self):
        fn = _resolve_metric("semantic_similarity")
        assert fn is _semantic_similarity

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            _resolve_metric("nonexistent")

    def test_passes_through_callable(self):
        def fn(t, e, a):
            return 0.5
        assert _resolve_metric(fn) is fn


class TestPromptOptimizer:
    def _spec(self) -> PromptSpec:
        return PromptSpec(name="test", inputs={"text": str}, output=str)

    def test_init_defaults(self):
        spec = self._spec()
        opt = PromptOptimizer(spec=spec, base_template="{{ text }}")
        assert opt.max_rounds == 5
        assert opt.candidates_per_round == 3
        assert opt.strategies == list(MUTATION_STRATEGIES)
        assert opt.best_template is None
        assert opt.best_score == -math.inf

    def test_score_one_empty_train_set(self):
        spec = self._spec()
        opt = PromptOptimizer(spec=spec, base_template="{{ text }}")
        tmpl = PromptTemplate(spec=spec, template="{{ text }}")
        assert opt.score_one(tmpl) == -math.inf

    def test_score_one_with_train_set(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric="exact_match",
            train_set=[("hello", "hello")],
        )
        tmpl = PromptTemplate(spec=spec, template="{{ text }}")
        assert opt.score_one(tmpl) == 1.0

    def test_score_one_averages(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric="exact_match",
            train_set=[("hello", "hello"), ("world", "wrong")],
        )
        tmpl = PromptTemplate(spec=spec, template="{{ text }}")
        assert opt.score_one(tmpl) == 0.5

    def test_optimize_finds_template(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric="exact_match",
            train_set=[("hello", "hello")],
            max_rounds=2,
            candidates_per_round=2,
            seed=42,
        )
        result = opt.optimize()
        assert isinstance(result, PromptTemplate)
        assert result.spec is spec

    def test_optimize_sets_best_template_and_score(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric="exact_match",
            train_set=[("hello", "hello")],
            max_rounds=1,
            candidates_per_round=1,
            seed=42,
        )
        opt.optimize()
        assert opt.best_template is not None
        assert opt.best_score >= 0.0

    def test_optimize_deterministic_with_seed(self):
        spec = self._spec()
        opt1 = PromptOptimizer(
            spec=spec, base_template="a\nb\nc", metric="exact_match",
            train_set=[("hello", "hello")], seed=42,
        )
        result1 = opt1.optimize()
        opt2 = PromptOptimizer(
            spec=spec, base_template="a\nb\nc", metric="exact_match",
            train_set=[("hello", "hello")], seed=42,
        )
        result2 = opt2.optimize()
        assert result1.template == result2.template

    def test_init_with_custom_strategies(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            strategies=["reword"],
        )
        assert opt.strategies == ["reword"]

    def test_init_with_callable_metric(self):
        spec = self._spec()
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric=lambda t, e, a: 1.0,
        )
        assert opt._metric_fn("", "", "") == 1.0
