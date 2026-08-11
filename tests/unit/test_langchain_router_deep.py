"""Deep behavioral tests for LangChainModelRouter internals and edge cases.

Covers: _build_branches construction, _passthrough_factory closure capture,
set_default overwriting, post-resolution route additions, mutable inputs,
and edge-case input types.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.models.langchain_router import LangChainModelRouter


class TestBuildBranchesInternal:
    def test_build_branches_empty_router_includes_dummy(self):
        router = LangChainModelRouter()
        branches = router._build_branches()
        result = branches.invoke({"any": "input"})
        assert result is None

    def test_build_branches_with_default_only(self):
        router = LangChainModelRouter()
        fallback = MagicMock(name="default_runnable")
        router.set_default(fallback)
        branches = router._build_branches()
        result = branches.invoke({"any": "data"})
        assert result is fallback

    def test_build_branches_with_routes_no_match_returns_default(self):
        router = LangChainModelRouter()
        model_a = MagicMock(name="model_a")
        fallback = MagicMock(name="fallback")
        router.add_route(lambda d: d.get("role") == "admin", model_a)
        router.set_default(fallback)
        result = router.resolve({"role": "user"})
        assert result is fallback

    def test_build_branches_is_deterministic(self):
        router = LangChainModelRouter()
        model = MagicMock(name="m")
        router.add_route(lambda d: True, model)
        result1 = router.resolve({"x": 1})
        result2 = router.resolve({"x": 2})
        assert result1 is model
        assert result2 is model


class TestSetDefaultBehavior:
    def test_set_default_twice_overwrites(self):
        router = LangChainModelRouter()
        first = MagicMock(name="first_default")
        second = MagicMock(name="second_default")
        router.set_default(first)
        router.set_default(second)
        result = router.resolve({"role": "nonexistent"})
        assert result is second

    def test_default_not_called_when_route_matches(self):
        router = LangChainModelRouter()
        model = MagicMock(name="matched_model")
        fallback = MagicMock(name="unused_fallback")
        router.add_route(lambda d: d.get("match") is True, model)
        router.set_default(fallback)
        result = router.resolve({"match": True})
        assert result is model

    def test_default_is_none_by_construction(self):
        router = LangChainModelRouter()
        result = router.resolve({"x": 1})
        assert result is None


class TestAddRouteAfterResolve:
    def test_resolve_then_add_then_resolve_sees_new_route(self):
        router = LangChainModelRouter()
        first_run = router.resolve({"role": "coder"})
        assert first_run is None

        model = MagicMock(name="coder_model")
        router.add_route(lambda d: d.get("role") == "coder", model)
        second_run = router.resolve({"role": "coder"})
        assert second_run is model


class TestPassthroughFactory:
    def test_passthrough_factory_returns_same_object(self):
        router = LangChainModelRouter()
        model = MagicMock(name="stored_model")
        router.add_route(lambda d: True, model)
        result = router.resolve({})
        assert result is model

    def test_passthrough_factory_handles_none_value(self):
        router = LangChainModelRouter()
        router.add_route(lambda d: d.get("match") is True, None)
        result = router.resolve({"match": True})
        assert result is None

    def test_passthrough_factory_handles_falsey_but_not_none(self):
        router = LangChainModelRouter()
        value = 0
        router.add_route(lambda d: True, value)
        result = router.resolve({})
        assert result == 0

    def test_passthrough_factory_handles_callable(self):
        router = LangChainModelRouter()
        router.add_route(lambda d: True, MagicMock)
        result = router.resolve({})
        assert result is MagicMock


class TestCompositionalRouting:
    def test_chain_length_zero(self):
        router = LangChainModelRouter()
        assert router.resolve({"any": "key"}) is None

    def test_chain_length_one_match(self):
        router = LangChainModelRouter()
        m = MagicMock(name="only")
        router.add_route(lambda d: True, m)
        assert router.resolve({}) is m

    def test_chain_length_one_no_match(self):
        router = LangChainModelRouter()
        m = MagicMock(name="only")
        router.add_route(lambda d: False, m)
        assert router.resolve({}) is None

    def test_ten_routes_first_match_wins(self):
        router = LangChainModelRouter()
        models = [MagicMock(name=f"m{i}") for i in range(10)]
        for i, m in enumerate(models):
            router.add_route(lambda d, idx=i: d.get("val") == idx, m)
        result = router.resolve({"val": 3})
        assert result is models[3]

    def test_condition_receives_full_dict(self):
        router = LangChainModelRouter()
        received: list[dict] = []

        def capture(d):
            received.append(dict(d))
            return True

        model = MagicMock(name="capturing")
        router.add_route(capture, model)
        router.resolve({"a": 1, "b": 2})
        assert received == [{"a": 1, "b": 2}]


class TestEdgeCaseInputs:
    def test_empty_dict_resolve(self):
        router = LangChainModelRouter()
        fallback = MagicMock(name="empty_fallback")
        router.set_default(fallback)
        result = router.resolve({})
        assert result is fallback

    def test_resolve_with_string_keys_and_int_values(self):
        router = LangChainModelRouter()
        m = MagicMock(name="int_matcher")
        router.add_route(lambda d: d.get("num", 0) > 5, m)
        result = router.resolve({"num": 10})
        assert result is m

    def test_resolve_with_nested_dict_value(self):
        router = LangChainModelRouter()
        m = MagicMock(name="nested_matcher")
        router.add_route(
            lambda d: d.get("nested", {}).get("deep", False) is True,
            m,
        )
        result = router.resolve({"nested": {"deep": True}})
        assert result is m

    def test_resolve_with_none_value_for_key(self):
        router = LangChainModelRouter()
        m = MagicMock(name="none_handler")
        router.add_route(lambda d: d.get("key") is None, m)
        result = router.resolve({"key": None})
        assert result is m

    def test_resolve_returns_default_on_non_dict_spec_input(self):
        router = LangChainModelRouter()
        fallback = MagicMock(name="fallback")
        router.set_default(fallback)
        result = router.resolve({"unexpected": "shape"})
        assert result is fallback


class TestStateConsistency:
    def test_conditions_and_runnables_stay_in_sync(self):
        router = LangChainModelRouter()
        for i in range(5):
            m = MagicMock(name=f"model_{i}")
            router.add_route(lambda d, idx=i: d.get("i") == idx, m)
        assert len(router._conditions) == 5
        assert len(router._runnables) == 5

    def test_resolve_does_not_mutate_internal_state(self):
        router = LangChainModelRouter()
        m = MagicMock(name="test_model")
        router.add_route(lambda d: True, m)
        before_conditions = len(router._conditions)
        before_runnables = len(router._runnables)
        router.resolve({})
        assert len(router._conditions) == before_conditions
        assert len(router._runnables) == before_runnables
