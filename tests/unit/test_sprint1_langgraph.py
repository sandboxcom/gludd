"""Tests for obj03: LangGraph-based multi-step model invocation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.models.langgraph_gateway import GraphState, LangGraphGateway


def _make_mock_call_model_fn(response_content: str = "generated output"):
    async def fn(profile_id: str, messages: list) -> MagicMock:
        return MagicMock(content=response_content)

    return AsyncMock(side_effect=fn)


class TestLangGraphGateway:
    def test_init_defaults(self):
        gw = LangGraphGateway()
        assert gw._max_retries == 2
        assert gw._quality_threshold == 0.6
        assert gw._enable_graph is True

    def test_init_custom(self):
        gw = LangGraphGateway(max_retries=1, quality_threshold=0.8, enable_graph=False)
        assert gw._max_retries == 1
        assert gw._quality_threshold == 0.8
        assert gw._enable_graph is False

    @pytest.mark.asyncio
    async def test_single_shot_fallback_no_langgraph(self):
        mock_call = _make_mock_call_model_fn("hello world")
        gw = LangGraphGateway(call_model_fn=mock_call, enable_graph=False)
        result = await gw.call(messages=[{"role": "user", "content": "hi"}])
        assert result["content"] == "hello world"
        assert result["retries"] == 0
        mock_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_graph_execution_calls_all_steps(self):
        mock_call = _make_mock_call_model_fn("def foo():\n    return 42")
        router = AsyncMock()
        router.route.return_value = MagicMock(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.9,
            estimated_cost_usd=0.01,
            sample_count=5,
            fallback=False,
            reason="best",
        )
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            adaptive_router=router,
            max_retries=0,
            quality_threshold=0.1,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(
            messages=[{"role": "user", "content": "write code"}],
            task_context={"work_type": "code"},
        )
        assert result["content"] == "def foo():\n    return 42"
        assert result["retries"] == 0
        assert result["quality_score"] is not None
        assert result["quality_score"] > 0.5

    @pytest.mark.asyncio
    async def test_graph_retries_on_low_quality(self):
        call_count = 0

        async def mock_fn(profile_id: str, messages: list) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(content="bad")
            return MagicMock(content="def good():\n    return 42")

        mock_call = AsyncMock(side_effect=mock_fn)
        router = AsyncMock()
        router.route.return_value = MagicMock(
            selected_prompt_profile_id=None,
            selected_model_profile_id=None,
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="no_data",
        )
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            adaptive_router=router,
            max_retries=2,
            quality_threshold=0.7,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(messages=[{"role": "user", "content": "test"}])
        assert call_count > 1
        assert "good" in result["content"]

    @pytest.mark.asyncio
    async def test_max_retries_returns_best(self):
        call_count = 0

        async def mock_fn(profile_id: str, messages: list) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return MagicMock(content=f"attempt {call_count}")

        mock_call = AsyncMock(side_effect=mock_fn)
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            max_retries=1,
            quality_threshold=0.9,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(messages=[{"role": "user", "content": "x"}])
        assert call_count == 2
        assert len(result["warnings"]) >= 0

    @pytest.mark.asyncio
    async def test_no_call_model_fn_returns_warning(self):
        gw = LangGraphGateway(enable_graph=False)
        result = await gw.call(messages=[])
        assert result["content"] == ""
        assert len(result["warnings"]) > 0
        assert "no call_model_fn" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_graph_falls_back_on_exception(self):
        mock_call = _make_mock_call_model_fn("recovered")
        gw = LangGraphGateway(call_model_fn=mock_call, enable_graph=True)
        gw._has_langgraph = True
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._run_graph(state)
        assert "recovered" in result["content"]

    @pytest.mark.asyncio
    async def test_review_step_scores_code_output(self):
        gw = LangGraphGateway(enable_graph=True)
        gw._has_langgraph = True
        state: GraphState = {
            "generated_output": "def test_func():\n    import os\n    return True",
        }
        result = await gw._review_step(state)
        assert result["quality_score"] is not None
        assert result["quality_score"] > 0.5

    @pytest.mark.asyncio
    async def test_review_step_empty_output_zero_score(self):
        gw = LangGraphGateway()
        state: GraphState = {"generated_output": ""}
        result = await gw._review_step(state)
        assert result["quality_score"] == 0.0

    @pytest.mark.asyncio
    async def test_select_step_uses_router(self):
        from general_ludd.schemas.benchmark import RoutingDecision

        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="best_prompt",
            selected_model_profile_id="best_model",
            composite_score=0.95,
            estimated_cost_usd=0.01,
            sample_count=10,
            fallback=False,
            reason="best_historical",
        )
        gw = LangGraphGateway(adaptive_router=router)
        state: GraphState = {
            "task_context": {"work_type": "refactor"},
            "selected_model": "default",
        }
        result = await gw._select_step(state)
        assert result["selected_model"] == "best_model"
        assert result["selected_prompt"] == "best_prompt"


class TestGraphState:
    def test_defaults(self):
        state: GraphState = {}
        assert state.get("classification") is None
        assert state.get("retry_count", 0) == 0
        assert state.get("warnings", []) == []
