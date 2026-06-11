"""Tests for LangGraphGateway covering graph execution and fallback paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.models.langgraph_gateway import GraphState, LangGraphGateway


class TestInit:
    def test_with_langgraph_installed(self):
        import importlib.machinery
        mock_spec = importlib.machinery.ModuleSpec("langgraph.graph", None)
        mock_module = MagicMock(__spec__=mock_spec)
        with patch.dict("sys.modules", {"langgraph.graph": mock_module}):
            gw = LangGraphGateway(enable_graph=True)
            assert gw._has_langgraph is True

    def test_without_langgraph_installed(self):
        with patch("importlib.util.find_spec", return_value=None):
            gw = LangGraphGateway(enable_graph=True)
            assert gw._has_langgraph is False


class TestCall:
    @pytest.mark.asyncio
    async def test_enable_graph_true_with_langgraph(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="graph result")
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=True)
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] == "graph result"

    @pytest.mark.asyncio
    async def test_enable_graph_false_goes_single_shot(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="single")
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] == "single"

    @pytest.mark.asyncio
    async def test_langgraph_not_installed_goes_single_shot(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="fallback")
        with patch.dict("sys.modules", {}, clear=False):
            gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=True)
        result = await gw.call([{"role": "user", "content": "hi"}])
        assert result["content"] == "fallback"


class TestRunGraph:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="generated output with def and class stuff here")
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=True, quality_threshold=0.6)
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
        assert "content" in result
        assert result["retries"] >= 0

    @pytest.mark.asyncio
    async def test_exception_falls_back_to_single_shot(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="recovered")
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=True)
        with patch.object(gw, "_execute_graph_steps", side_effect=RuntimeError("graph broke")):
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
            assert result["content"] == "recovered"


class TestExecuteGraphSteps:
    @pytest.mark.asyncio
    async def test_quality_above_threshold(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(
            content="def foo():\n    import os\n    return 'hello world output that is long enough'"
        )
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(
                call_model_fn=call_fn,
                enable_graph=True,
                quality_threshold=0.5,
                max_retries=2,
            )
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
        result = await gw._execute_graph_steps(state)
        assert result["content"] != ""
        assert result["retries"] == 0

    @pytest.mark.asyncio
    async def test_quality_below_threshold_retries_then_max(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="short")
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(
                call_model_fn=call_fn,
                enable_graph=True,
                quality_threshold=0.9,
                max_retries=1,
            )
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
        result = await gw._execute_graph_steps(state)
        assert result["retries"] >= 1
        assert any("Max retries" in w for w in result["warnings"])


class TestClassifyStep:
    @pytest.mark.asyncio
    async def test_passthrough(self):
        gw = LangGraphGateway(enable_graph=False)
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
        result = await gw._classify_step(state)
        assert result is state


class TestSelectStep:
    @pytest.mark.asyncio
    async def test_router_returns_decision(self):
        decision = MagicMock()
        decision.fallback = False
        decision.selected_model_profile_id = "model-a"
        decision.selected_prompt_profile_id = "prompt-b"

        router = AsyncMock()
        router.route.return_value = decision

        gw = LangGraphGateway(adaptive_router=router, enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {"work_type": "feature"},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._select_step(state)
        assert result["selected_model"] == "model-a"
        assert result["selected_prompt"] == "prompt-b"

    @pytest.mark.asyncio
    async def test_router_raises_exception(self):
        router = AsyncMock()
        router.route.side_effect = RuntimeError("routing failed")

        gw = LangGraphGateway(adaptive_router=router, enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {"work_type": "feature"},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._select_step(state)
        assert result["selected_model"] == "default"

    @pytest.mark.asyncio
    async def test_no_router(self):
        gw = LangGraphGateway(enable_graph=False)
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
        result = await gw._select_step(state)
        assert result["selected_model"] == "default"


class TestGenerateStep:
    @pytest.mark.asyncio
    async def test_with_call_model_fn(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="generated text")

        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
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
        result = await gw._generate_step(state)
        assert result["generated_output"] == "generated text"

    @pytest.mark.asyncio
    async def test_without_call_model_fn(self):
        gw = LangGraphGateway(enable_graph=False)
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
        result = await gw._generate_step(state)
        assert result["generated_output"] == ""
        assert any("no call_model_fn" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_with_exception(self):
        call_fn = AsyncMock(side_effect=RuntimeError("model error"))
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
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
        result = await gw._generate_step(state)
        assert result["generated_output"] == ""
        assert any("Generation failed" in w for w in result["warnings"])


class TestReviewStep:
    @pytest.mark.asyncio
    async def test_code_with_def_class_import_return(self):
        gw = LangGraphGateway(enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "def foo():\n    import os\n    return 'x'",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] is not None
        assert result["quality_score"] >= 0.85

    @pytest.mark.asyncio
    async def test_short_output(self):
        gw = LangGraphGateway(enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "ok",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] == 0.5

    @pytest.mark.asyncio
    async def test_test_related_output(self):
        gw = LangGraphGateway(enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "test something with assert True",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] >= 0.6

    @pytest.mark.asyncio
    async def test_empty_output_scores_zero(self):
        gw = LangGraphGateway(enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] == 0.0


class TestCallSingleShot:
    @pytest.mark.asyncio
    async def test_with_call_model_fn(self):
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="hello world")
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw._call_single_shot(
            messages=[{"role": "user", "content": "hi"}],
            task_context={},
            profile_id="test-profile",
        )
        assert result["content"] == "hello world"
        assert result["model"] == "test-profile"
        assert result["retries"] == 0

    @pytest.mark.asyncio
    async def test_without_call_model_fn(self):
        gw = LangGraphGateway(enable_graph=False)
        result = await gw._call_single_shot(
            messages=[],
            task_context={},
            profile_id="default",
        )
        assert result["content"] == ""
        assert any("no call_model_fn" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_with_exception(self):
        call_fn = AsyncMock(side_effect=RuntimeError("model down"))
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw._call_single_shot(
            messages=[],
            task_context={},
            profile_id="default",
        )
        assert result["content"] == ""
        assert any("model down" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_result_without_content_attr(self):
        call_fn = AsyncMock()
        call_fn.return_value = "plain string result"
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw._call_single_shot(
            messages=[],
            task_context={},
            profile_id="default",
        )
        assert result["content"] == "plain string result"
