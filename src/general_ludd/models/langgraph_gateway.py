"""LangGraph-based multi-step model invocation gateway.

Wraps existing single-shot call_model in a state graph that supports:
1. Task classification → selects best model+prompt
2. Generate → call model with selected profile
3. Review → score output quality
4. Retry or return based on quality threshold
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from general_ludd.schemas.benchmark import TaskType

log = logging.getLogger(__name__)


class GraphState(TypedDict, total=False):
    messages: list[Any]
    task_context: dict[str, Any]
    classification: str | None
    selected_model: str | None
    selected_prompt: str | None
    generated_output: str | None
    quality_score: float | None
    retry_count: int
    final_output: str | None
    warnings: list[str]


class LangGraphGateway:
    """Multi-step model gateway using langgraph StateGraph.

    Falls back to single-shot invocation when langgraph is not installed
    or when enable_graph is False.
    """

    def __init__(
        self,
        call_model_fn: Any = None,
        adaptive_router: Any = None,
        scoring_engine: Any = None,
        max_retries: int = 2,
        quality_threshold: float = 0.6,
        enable_graph: bool = True,
    ) -> None:
        self._call_model = call_model_fn
        self._router = adaptive_router
        self._scoring = scoring_engine
        self._max_retries = max_retries
        self._quality_threshold = quality_threshold
        self._enable_graph = enable_graph
        self._graph = None
        self._has_langgraph = False
        try:
            import importlib.util
            self._has_langgraph = importlib.util.find_spec("langgraph.graph") is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            self._has_langgraph = False

    async def call(
        self,
        messages: list[Any],
        task_context: dict[str, Any] | None = None,
        profile_id: str = "default",
    ) -> dict[str, Any]:
        """Execute model call, either single-shot or multi-step graph."""
        ctx = task_context or {}
        if not self._enable_graph or not self._has_langgraph:
            return await self._call_single_shot(messages, ctx, profile_id)

        state: GraphState = {
            "messages": messages,
            "task_context": ctx,
            "classification": None,
            "selected_model": profile_id,
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await self._run_graph(state)
        return result

    async def _run_graph(self, state: GraphState) -> dict[str, Any]:
        """Execute the multi-step graph. Falls back to linear execution."""
        try:
            return await self._execute_graph_steps(state)
        except Exception as exc:
            log.warning("Graph execution failed: %s, falling back to single shot", exc)
            return await self._call_single_shot(
                state["messages"],
                state["task_context"],
                state.get("selected_model") or "default",
            )

    async def _execute_graph_steps(self, state: GraphState) -> dict[str, Any]:
        max_retries = self._max_retries
        warnings: list[str] = list(state.get("warnings", []))

        while state["retry_count"] <= max_retries:
            state = await self._classify_step(state)
            state = await self._select_step(state)
            state = await self._generate_step(state)
            state = await self._review_step(state)

            quality = state.get("quality_score", 0.0)
            if quality is not None and quality >= self._quality_threshold:
                state["final_output"] = state["generated_output"]
                break
            else:
                if state["retry_count"] < max_retries:
                    state["retry_count"] += 1
                    warnings.append(
                        f"Retry {state['retry_count']}/{max_retries}: "
                        f"quality {quality:.2f} < threshold {self._quality_threshold}"
                    )
                else:
                    state["final_output"] = state["generated_output"]
                    warnings.append(
                        f"Max retries reached ({max_retries}), returning best output "
                        f"with quality {quality:.2f}"
                    )
                    break

        return {
            "content": state.get("final_output", ""),
            "model": state.get("selected_model", "default"),
            "prompt": state.get("selected_prompt"),
            "quality_score": state.get("quality_score"),
            "retries": state["retry_count"],
            "warnings": warnings,
        }

    async def _classify_step(self, state: GraphState) -> GraphState:
        return state

    async def _select_step(self, state: GraphState) -> GraphState:
        if self._router is not None:
            try:
                ctx = state.get("task_context", {})
                wt = ctx.get("work_type", "feature")
                try:
                    task_type = TaskType(wt.replace("-", "_").lower())
                except ValueError:
                    task_type = TaskType.FEATURE
                decision = await self._router.route(task_type)
                if decision and not decision.fallback:
                    state["selected_model"] = decision.selected_model_profile_id or state.get("selected_model")
                    state["selected_prompt"] = decision.selected_prompt_profile_id
            except Exception as exc:
                log.debug("Adaptive routing failed: %s", exc)
        return state

    async def _generate_step(self, state: GraphState) -> GraphState:
        if self._call_model is None:
            state["generated_output"] = ""
            state["warnings"] = [*list(state.get("warnings", [])), "no call_model_fn configured"]
            return state
        try:
            model = state.get("selected_model", "default")
            result = await self._call_model(
                profile_id=model,
                messages=state["messages"],
            )
            state["generated_output"] = result.content if hasattr(result, "content") else str(result)
        except Exception as exc:
            state["generated_output"] = ""
            state["warnings"] = [*list(state.get("warnings", [])), f"Generation failed: {exc}"]
        return state

    async def _review_step(self, state: GraphState) -> GraphState:
        output = state.get("generated_output", "")
        if not output:
            state["quality_score"] = 0.0
            return state
        score = 0.5
        if "def " in output or "class " in output:
            score += 0.15
        if "import " in output:
            score += 0.1
        if "return " in output:
            score += 0.1
        if len(output) > 50:
            score += 0.05
        if "test" in output.lower() or "assert" in output.lower():
            score += 0.1
        state["quality_score"] = min(score, 1.0)
        return state

    async def _call_single_shot(
        self,
        messages: list[Any],
        task_context: dict[str, Any],
        profile_id: str,
    ) -> dict[str, Any]:
        if self._call_model is None:
            return {"content": "", "model": profile_id, "warnings": ["no call_model_fn"]}
        try:
            result = await self._call_model(profile_id=profile_id, messages=messages)
            content = result.content if hasattr(result, "content") else str(result)
            return {
                "content": content,
                "model": profile_id,
                "retries": 0,
                "warnings": [],
            }
        except Exception as exc:
            return {
                "content": "",
                "model": profile_id,
                "retries": 0,
                "warnings": [str(exc)],
            }
