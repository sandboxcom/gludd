from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.models.langchain_router import LangChainModelRouter


class TestLangChainModelRouter:
    """Tests for LangChainModelRouter — RunnableBranch-backed model selection."""

    # ------------------------------------------------------------------
    # Route selection by role
    # ------------------------------------------------------------------

    def test_route_by_role(self):
        router = LangChainModelRouter()
        reviewer_runnable = MagicMock(name="reviewer_model")
        coder_runnable = MagicMock(name="coder_model")

        router.add_route(
            lambda d: d.get("role") == "reviewer",
            reviewer_runnable,
        )
        router.add_route(
            lambda d: d.get("role") == "coder",
            coder_runnable,
        )

        assert router.resolve({"role": "reviewer"}) is reviewer_runnable
        assert router.resolve({"role": "coder"}) is coder_runnable

    def test_route_by_role_with_extra_keys(self):
        router = LangChainModelRouter()
        runnable = MagicMock(name="reviewer_model")
        router.add_route(lambda d: d.get("role") == "reviewer", runnable)

        result = router.resolve({
            "role": "reviewer",
            "quality_class": "high",
            "extra": 42,
        })
        assert result is runnable

    # ------------------------------------------------------------------
    # Route selection by quality_class
    # ------------------------------------------------------------------

    def test_route_by_quality_class(self):
        router = LangChainModelRouter()
        high_quality = MagicMock(name="high_quality_model")
        low_quality = MagicMock(name="low_quality_model")

        router.add_route(
            lambda d: d.get("quality_class") == "high",
            high_quality,
        )
        router.add_route(
            lambda d: d.get("quality_class") == "low",
            low_quality,
        )

        assert router.resolve({"quality_class": "high"}) is high_quality
        assert router.resolve({"quality_class": "low"}) is low_quality

    def test_route_by_latency_class(self):
        router = LangChainModelRouter()
        fast_model = MagicMock(name="fast_model")
        cheap_model = MagicMock(name="cheap_model")

        router.add_route(
            lambda d: d.get("latency_class") == "fast",
            fast_model,
        )
        router.add_route(
            lambda d: d.get("latency_class") == "cheap",
            cheap_model,
        )

        assert router.resolve({"latency_class": "fast"}) is fast_model
        assert router.resolve({"latency_class": "cheap"}) is cheap_model

    # ------------------------------------------------------------------
    # Default route when no conditions match
    # ------------------------------------------------------------------

    def test_default_route_no_match(self):
        router = LangChainModelRouter()
        fallback = MagicMock(name="fallback_model")
        router.set_default(fallback)

        result = router.resolve({"role": "unknown", "random": "data"})
        assert result is fallback

    def test_default_route_no_routes_at_all(self):
        router = LangChainModelRouter()
        fallback = MagicMock(name="only_model")
        router.set_default(fallback)

        result = router.resolve({})
        assert result is fallback

    def test_no_match_and_no_default_returns_none(self):
        router = LangChainModelRouter()
        specialist = MagicMock(name="specialist_model")
        router.add_route(
            lambda d: d.get("role") == "specialist",
            specialist,
        )

        result = router.resolve({"role": "generalist"})
        assert result is None

    def test_empty_router_returns_none(self):
        router = LangChainModelRouter()
        result = router.resolve({"role": "anything"})
        assert result is None

    # ------------------------------------------------------------------
    # Multiple conditions with priority ordering
    # ------------------------------------------------------------------

    def test_first_match_wins_for_overlapping_conditions(self):
        router = LangChainModelRouter()
        first = MagicMock(name="first_model")
        second = MagicMock(name="second_model")

        router.add_route(lambda d: "role" in d, first)
        router.add_route(lambda d: d.get("role") == "coder", second)

        result = router.resolve({"role": "coder"})
        assert result is first

    def test_priority_ordering_multiple_roles(self):
        router = LangChainModelRouter()
        high_pri = MagicMock(name="high_priority")
        mid_pri = MagicMock(name="mid_priority")
        low_pri = MagicMock(name="low_priority")

        router.add_route(
            lambda d: d.get("quality_class") == "high" and d.get("role") == "reviewer",
            high_pri,
        )
        router.add_route(
            lambda d: d.get("quality_class") == "high",
            mid_pri,
        )
        router.add_route(
            lambda d: d.get("role") == "reviewer",
            low_pri,
        )

        assert router.resolve({
            "quality_class": "high",
            "role": "reviewer",
        }) is high_pri

        assert router.resolve({
            "quality_class": "high",
            "role": "coder",
        }) is mid_pri

        assert router.resolve({
            "quality_class": "low",
            "role": "reviewer",
        }) is low_pri
