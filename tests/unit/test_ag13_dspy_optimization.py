"""Unit tests for AG.13: DSPy-style prompt optimization registry and optimizer."""

from __future__ import annotations

from general_ludd.ag13_dspy import (
    PromptOptimizer,
    PromptRegistry,
    PromptSpec,
    PromptTemplate,
)


class TestPromptSpec:
    def test_minimal_construction(self):
        spec = PromptSpec(name="classify")
        assert spec.name == "classify"
        assert spec.inputs == {}
        assert spec.output is str
        assert spec.description == ""

    def test_full_construction(self):
        spec = PromptSpec(
            name="summarize",
            inputs={"document": str, "length": int},
            output=str,
            description="Summarize a document to the given word count.",
        )
        assert spec.name == "summarize"
        assert spec.inputs == {"document": str, "length": int}
        assert spec.output is str
        assert "Summarize" in spec.description

    def test_spec_is_frozen(self):
        spec = PromptSpec(name="classify")
        with pytest.raises(AttributeError):
            spec.name = "other"  # type: ignore[misc]

    def test_equality(self):
        a = PromptSpec(name="x")
        b = PromptSpec(name="x")
        assert a == b


class TestPromptTemplate:
    def test_call_renders_jinja2(self):
        spec = PromptSpec(name="greet", inputs={"name": str}, output=str)
        tmpl = PromptTemplate(spec=spec, template="Hello, {{ name }}!")
        result = tmpl.call(name="World")
        assert result == "Hello, World!"

    def test_call_default_inputs(self):
        spec = PromptSpec(name="echo", inputs={"text": str}, output=str)
        tmpl = PromptTemplate(spec=spec, template="{{ text }}")
        assert tmpl.call(text="foo") == "foo"

    def test_version_default(self):
        spec = PromptSpec(name="t")
        tmpl = PromptTemplate(spec=spec, template="x")
        assert tmpl.version == 1

    def test_score_default_none(self):
        spec = PromptSpec(name="t")
        tmpl = PromptTemplate(spec=spec, template="x")
        assert tmpl.score is None


class TestPromptRegistry:
    def test_put_and_get(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="classify")
        tmpl = PromptTemplate(spec=spec, template="Classify: {{ text }}")
        reg.put("classify", 1, tmpl)
        assert reg.get("classify", 1) is tmpl

    def test_get_missing(self):
        reg = PromptRegistry()
        assert reg.get("nonexistent", 1) is None

    def test_latest_returns_highest_version(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        reg.put("classify", 1, PromptTemplate(spec=spec, template="v1"))
        reg.put("classify", 3, PromptTemplate(spec=spec, template="v3"))
        reg.put("classify", 2, PromptTemplate(spec=spec, template="v2"))
        latest = reg.latest("classify")
        assert latest is not None
        assert latest.version == 3

    def test_latest_empty(self):
        reg = PromptRegistry()
        assert reg.latest("nope") is None

    def test_get_best_highest_score(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        reg.put("s", 1, PromptTemplate(spec=spec, template="a"), score=0.3)
        reg.put("s", 2, PromptTemplate(spec=spec, template="b"), score=0.9)
        reg.put("s", 3, PromptTemplate(spec=spec, template="c"), score=0.5)
        best = reg.get_best("s")
        assert best is not None
        assert best.score == 0.9

    def test_get_best_no_score_defaults_to_minus_one(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        reg.put("x", 1, PromptTemplate(spec=spec, template="t"))
        reg.put("x", 2, PromptTemplate(spec=spec, template="t2"), score=0.1)
        best = reg.get_best("x")
        assert best is not None
        assert best.score == 0.1

    def test_get_best_empty(self):
        reg = PromptRegistry()
        assert reg.get_best("nope") is None

    def test_list_versions(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        for v in [3, 1, 2]:
            reg.put("classify", v, PromptTemplate(spec=spec, template=f"v{v}"))
        assert reg.list_versions("classify") == [1, 2, 3]

    def test_list_names(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        reg.put("a", 1, PromptTemplate(spec=spec, template="x"))
        reg.put("b", 1, PromptTemplate(spec=spec, template="x"))
        assert reg.list_names() == ["a", "b"]

    def test_remove(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        reg.put("x", 1, PromptTemplate(spec=spec, template="x"))
        reg.remove("x", 1)
        assert reg.get("x", 1) is None

    def test_len(self):
        reg = PromptRegistry()
        spec = PromptSpec(name="t")
        assert len(reg) == 0
        reg.put("a", 1, PromptTemplate(spec=spec, template="x"))
        reg.put("b", 1, PromptTemplate(spec=spec, template="x"))
        assert len(reg) == 2

    def test_thread_safety_no_deadlock(self):
        import threading

        reg = PromptRegistry()
        spec = PromptSpec(name="t")

        def writer():
            for i in range(100):
                reg.put("t", i, PromptTemplate(spec=spec, template=f"v{i}"), score=i * 0.01)

        def reader():
            for _ in range(100):
                reg.latest("t")
                reg.get_best("t")
                reg.list_versions("t")

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(reg) == 100


class TestPromptOptimizer:
    def test_optimize_returns_template(self):
        spec = PromptSpec(name="greet", inputs={"name": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="Hello, {{ name }}!",
            train_set=[("World", "Hello, World!")],
            max_rounds=2,
            seed=42,
        )
        best = opt.optimize()
        assert isinstance(best, PromptTemplate)
        assert isinstance(opt.best_score, float)

    def test_optimize_converges_on_exact_match(self):
        spec = PromptSpec(name="echo", inputs={"text": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric="exact_match",
            train_set=[("hello", "hello")],
            max_rounds=2,
            seed=42,
        )
        best = opt.optimize()
        result = best.call(text="hello")
        assert result == "hello"

    def test_optimize_empty_train_set_returns_base(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            max_rounds=1,
        )
        best = opt.optimize()
        assert best.template == "{{ text }}"

    def test_builtin_metric_contains_all(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="Classify: {{ text }}",
            metric="contains_all",
            train_set=[("buy now", "category: spam")],
            max_rounds=1,
            seed=42,
        )
        opt.optimize()
        assert 0.0 <= opt.best_score <= 1.0

    def test_builtin_metric_semantic_similarity(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="Summarize: {{ text }}",
            metric="semantic_similarity",
            train_set=[("hello world", "greeting hello world")],
            max_rounds=1,
            seed=42,
        )
        opt.optimize()
        assert 0.0 <= opt.best_score <= 1.0

    def test_unknown_metric_raises(self):
        spec = PromptSpec(name="t")
        with pytest.raises(ValueError, match="Unknown metric"):
            PromptOptimizer(spec=spec, base_template="x", metric="unknown")

    def test_custom_metric_fn(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)

        def always_half(candidate: str, expected: str, actual: str) -> float:
            return 0.5

        opt = PromptOptimizer(
            spec=spec,
            base_template="{{ text }}",
            metric=always_half,
            train_set=[("a", "b")],
            max_rounds=1,
        )
        opt.optimize()
        assert opt.best_score == 0.5

    def test_strategies_produce_different_outputs(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)
        opt = PromptOptimizer(
            spec=spec,
            base_template="Classify this text: {{ text }}",
            metric="exact_match",
            train_set=[("hello", "greeting")],
            max_rounds=2,
            candidates_per_round=3,
            seed=123,
        )
        opt.optimize()
        assert isinstance(opt.best_template, PromptTemplate)

    def test_reproducible_with_seed(self):
        spec = PromptSpec(name="t", inputs={"text": str}, output=str)

        def make_opt():
            return PromptOptimizer(
                spec=spec,
                base_template="Hello {{ text }}",
                train_set=[("a", "b")],
                max_rounds=2,
                candidates_per_round=3,
                seed=99,
            )

        a = make_opt().optimize()
        b = make_opt().optimize()
        assert a.template == b.template
        assert a.score == b.score


import pytest  # noqa: E402
