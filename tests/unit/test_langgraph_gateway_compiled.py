"""Tests for LangGraphGateway compiled StateGraph execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.langgraph_gateway import LangGraphGateway, ReviewVerdict


def _mock_call_model_fn(review_passed: bool = True, quality_score: float = 0.85, gen_content: str = ""):
    """Create an AsyncMock call_model_fn that returns different results for generate vs review calls."""

    async def _call(profile_id, messages, **kwargs):
        system_text = ""
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                system_text = m.get("content", "")
        if "code quality reviewer" in system_text:
            verdict = ReviewVerdict(
                review_passed=review_passed,
                quality_score=quality_score,
                feedback="good" if review_passed else "needs improvement",
            )
            return MagicMock(content=verdict.model_dump_json())
        _default = "def foo():\n    import os\n    return 'hello world output that is long enough'"
        return MagicMock(content=gen_content or _default)

    return AsyncMock(side_effect=_call)


class TestCompiledGraphPassesReview:
    @pytest.mark.asyncio
    async def test_content_passes_review_no_retries(self):
        mock_fn = _mock_call_model_fn(review_passed=True, quality_score=0.85)
        gw = LangGraphGateway(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.6,
            max_retries=2,
        )
        result = await gw.call([{"role": "user", "content": "write a function"}])
        assert result["content"] != ""
        assert result["retries"] == 0
        assert result["quality_score"] == 0.85

    @pytest.mark.asyncio
    async def test_compiled_graph_all_nodes_execute(self):
        node_order = []

        class NodeTracker(LangGraphGateway):
            async def _classify_node(self, state):
                node_order.append("classify")
                return await super()._classify_node(state)

            async def _select_model_node(self, state):
                node_order.append("select_model")
                return await super()._select_model_node(state)

            async def _generate_node(self, state):
                node_order.append("generate")
                return await super()._generate_node(state)

            async def _review_node(self, state):
                node_order.append("review")
                return await super()._review_node(state)

        mock_fn = _mock_call_model_fn(review_passed=True, quality_score=0.9)
        gw = NodeTracker(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.6,
            max_retries=2,
        )
        await gw.call([{"role": "user", "content": "test"}])
        assert node_order == ["classify", "select_model", "generate", "review"]


class TestCompiledGraphRetries:
    @pytest.mark.asyncio
    async def test_content_fails_review_retries_then_passes(self):
        call_count = {"generate": 0}

        async def _call(profile_id, messages, **kwargs):
            system_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    system_text = m.get("content", "")
            if "code quality reviewer" in system_text:
                call_count["generate"] += 1
                if call_count["generate"] <= 2:
                    return MagicMock(content=ReviewVerdict(
                        review_passed=False, quality_score=0.3, feedback="bad"
                    ).model_dump_json())
                return MagicMock(content=ReviewVerdict(
                    review_passed=True, quality_score=0.85, feedback="good"
                ).model_dump_json())
            return MagicMock(content="generated output")

        mock_fn = AsyncMock(side_effect=_call)
        gw = LangGraphGateway(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.6,
            max_retries=3,
        )
        result = await gw.call([{"role": "user", "content": "write code"}])
        assert result["content"] != ""
        assert result["retries"] == 2
        assert result["quality_score"] == 0.85

    @pytest.mark.asyncio
    async def test_max_retry_exit_when_never_passes(self):
        async def _call(profile_id, messages, **kwargs):
            system_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    system_text = m.get("content", "")
            if "code quality reviewer" in system_text:
                return MagicMock(content=ReviewVerdict(
                    review_passed=False, quality_score=0.2, feedback="bad"
                ).model_dump_json())
            return MagicMock(content="generated output")

        mock_fn = AsyncMock(side_effect=_call)
        gw = LangGraphGateway(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.6,
            max_retries=2,
        )
        result = await gw.call([{"role": "user", "content": "write code"}])
        assert result["retries"] == 2
        assert result["quality_score"] == 0.2
        assert any("Max retries reached" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_heuristic_fallback_when_structured_review_fails(self):
        async def _call(profile_id, messages, **kwargs):
            system_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    system_text = m.get("content", "")
            if "code quality reviewer" in system_text:
                return MagicMock(content="not valid json {{{")
            return MagicMock(
                content="def foo():\n    import os\n    return 'hello world output that is long enough'"
            )

        mock_fn = AsyncMock(side_effect=_call)
        gw = LangGraphGateway(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.5,
            max_retries=2,
        )
        result = await gw.call([{"role": "user", "content": "write a function"}])
        assert result["content"] != ""
        assert result["retries"] == 0


class TestCompiledGraphStateTracking:
    @pytest.mark.asyncio
    async def test_state_fields_present_in_initial_and_final(self):
        """Verify state flow: initial state carries through to final result."""
        captured_states = []

        class StateTracker(LangGraphGateway):
            async def _classify_node(self, state):
                captured_states.append(("classify", dict(state)))
                result = await super()._classify_node(state)
                captured_states.append(("classify_done", dict(result)))
                return result

            async def _generate_node(self, state):
                captured_states.append(("generate", dict(state)))
                result = await super()._generate_node(state)
                captured_states.append(("generate_done", dict(result)))
                return result

            async def _review_node(self, state):
                captured_states.append(("review", dict(state)))
                result = await super()._review_node(state)
                captured_states.append(("review_done", dict(result)))
                return result

        mock_fn = _mock_call_model_fn(review_passed=True, quality_score=0.85)
        gw = StateTracker(
            call_model_fn=mock_fn,
            enable_graph=True,
            quality_threshold=0.6,
            max_retries=2,
        )
        result = await gw.call([{"role": "user", "content": "hi"}])

        assert len(captured_states) >= 6
        classify_in = next(s for name, s in captured_states if name == "classify")
        assert "messages" in classify_in
        assert classify_in.get("retry_count") == 0
        assert classify_in.get("selected_model") == "default"

        generate_done = next(s for name, s in captured_states if name == "generate_done")
        assert generate_done.get("generated_content") is not None
        assert generate_done.get("generated_content") != ""

        review_done = next(s for name, s in captured_states if name == "review_done")
        assert review_done.get("review_passed") is True
        assert review_done.get("quality_score") == 0.85

        assert result["content"] != ""
        assert result["retries"] == 0
        assert result["quality_score"] == 0.85


class TestCompiledGraphSingleShotFallback:
    @pytest.mark.asyncio
    async def test_enable_graph_false_uses_single_shot(self):
        call_fn = _mock_call_model_fn()
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
        )
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] != ""
        assert result["retries"] == 0

    @pytest.mark.asyncio
    async def test_compiled_graph_import_error_falls_back(self):
        call_fn = _mock_call_model_fn()
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=True,
        )
        gw._graph = None
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] != ""

    @pytest.mark.asyncio
    async def test_compiled_graph_invoke_error_falls_back(self):
        call_fn = _mock_call_model_fn()
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=True,
        )
        gw._graph = MagicMock()
        gw._graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph error"))
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] != ""
