"""LangGraph-based multi-step model invocation gateway.

Wraps existing single-shot call_model in a compiled langgraph StateGraph that supports:
1. Task classification → selects best model+prompt
2. Generate → call model with selected profile
3. Review → score output quality via LLM-as-judge with Pydantic structured output
4. Retry or return based on quality threshold
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict, cast

from pydantic import BaseModel, Field

from general_ludd.schemas.benchmark import TaskType

log = logging.getLogger(__name__)


class ReviewVerdict(BaseModel):
    """Structured output from the LLM-as-judge review step."""

    review_passed: bool = Field(default=False, description="Whether the generated output meets quality standards")
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Quality score between 0.0 and 1.0")
    feedback: str = Field(default="", description="Brief feedback on the output quality")


class GraphState(TypedDict, total=False):
    """Mutable state shared across langgraph pipeline steps."""

    messages: list[object]
    task_context: dict[str, object]
    classification: str | None
    selected_model: str | None
    selected_prompt: str | None
    generated_output: str | None
    quality_score: float | None
    retry_count: int
    final_output: str | None
    warnings: list[str]
    # Fields for the compiled langgraph graph
    generated_content: str | None
    review_passed: bool


class LangGraphGateway:
    """Multi-step model gateway using langgraph StateGraph.

    Falls back to single-shot invocation when langgraph is not installed
    or when enable_graph is False.
    """

    _REVIEW_SYSTEM_PROMPT = (
        "You are a code quality reviewer. Evaluate the generated output for correctness, "
        "completeness, and adherence to the request. Respond ONLY with a JSON object "
        'matching: {"review_passed": bool, "quality_score": float (0.0-1.0), "feedback": str}'
    )

    def __init__(
        self,
        call_model_fn: Any = None,
        adaptive_router: Any = None,
        scoring_engine: Any = None,
        max_retries: int = 2,
        quality_threshold: float = 0.6,
        enable_graph: bool = True,
    ) -> None:
        """Initialize the gateway with optional model-call and routing hooks."""
        self._call_model: Any = call_model_fn
        self._router: Any = adaptive_router
        self._scoring: Any = scoring_engine
        self._max_retries = max_retries
        self._quality_threshold = quality_threshold
        self._enable_graph = enable_graph
        self._graph: Any = None
        self._has_langgraph = False
        try:
            import importlib.util
            import sys

            # sys.modules-first: a partially loaded (or test-injected) langgraph
            # counts as available, matching what a real import would observe.
            self._has_langgraph = (
                "langgraph.graph" in sys.modules or importlib.util.find_spec("langgraph.graph") is not None
            )
        except (ImportError, ValueError, ModuleNotFoundError):
            self._has_langgraph = False

        if self._enable_graph and self._has_langgraph:
            try:
                self._graph = self._build_graph()
            except Exception:
                self._has_langgraph = False
                self._graph = None

    def _build_graph(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(GraphState)

        builder.add_node("classify", self._classify_node)
        builder.add_node("select_model", self._select_model_node)
        builder.add_node("generate", self._generate_node)
        builder.add_node("review", self._review_node)

        builder.add_edge(START, "classify")
        builder.add_edge("classify", "select_model")
        builder.add_edge("select_model", "generate")
        builder.add_edge("generate", "review")

        builder.add_conditional_edges(
            "review",
            self._should_continue,
            {
                "generate": "generate",
                "__end__": END,
            },
        )

        return builder.compile()

    def _should_continue(self, state: GraphState) -> str:
        if state.get("review_passed"):
            return "__end__"
        return "generate"

    async def _classify_node(self, state: GraphState) -> GraphState:
        state["classification"] = state.get("classification", "feature")
        return state

    async def _select_model_node(self, state: GraphState) -> GraphState:
        if self._router is not None:
            try:
                ctx = state.get("task_context", {})
                wt = cast(str, ctx.get("work_type", "feature"))
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

    async def _generate_node(self, state: GraphState) -> GraphState:
        if self._call_model is None:
            state["generated_output"] = ""
            state["generated_content"] = ""
            state["warnings"] = [*list(state.get("warnings", [])), "no call_model_fn configured"]
            return state
        try:
            model = state.get("selected_model", "default")
            result = await self._call_model(
                profile_id=model,
                messages=state["messages"],
                work_type=state.get("task_context", {}).get("work_type") or "feature",
            )
            content = result.content if hasattr(result, "content") else str(result)
            state["generated_output"] = content
            state["generated_content"] = content
        except Exception as exc:
            state["generated_output"] = ""
            state["generated_content"] = ""
            state["warnings"] = [*list(state.get("warnings", [])), f"Generation failed: {exc}"]
        return state

    async def _review_node(self, state: GraphState) -> GraphState:
        content = state.get("generated_content") or state.get("generated_output", "")
        if not content:
            state["quality_score"] = 0.0
            state["review_passed"] = True
            return state

        passed = False
        quality: float = 0.0

        if self._call_model is not None:
            try:
                review_messages = [
                    {"role": "system", "content": self._REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Review this generated output:\n\n{content}"},
                ]
                result = await self._call_model(
                    profile_id=state.get("selected_model", "default"),
                    messages=review_messages,
                    work_type="review",
                )
                response_text = result.content if hasattr(result, "content") else str(result)
                verdict = _parse_review_response(response_text)
                quality = verdict.quality_score
                passed = verdict.review_passed and quality >= self._quality_threshold
            except Exception as exc:
                log.debug("Structured review failed, falling back to heuristic: %s", exc)
                quality = _heuristic_score(content)
                passed = quality >= self._quality_threshold
        else:
            quality = _heuristic_score(content)
            passed = quality >= self._quality_threshold

        state["quality_score"] = quality

        retry_count = state.get("retry_count", 0)
        if not passed and retry_count < self._max_retries:
            state["retry_count"] = retry_count + 1
            state["warnings"] = [
                *list(state.get("warnings", [])),
                "Retry {}/{}: quality {:.2f} < threshold {}".format(
                    state["retry_count"], self._max_retries, quality, self._quality_threshold
                ),
            ]
        else:
            if not passed:
                state["warnings"] = [
                    *list(state.get("warnings", [])),
                    f"Max retries reached ({self._max_retries}), returning best output",
                ]
            passed = True
            state["final_output"] = content

        state["review_passed"] = passed
        return state

    async def call(
        self,
        messages: list[object],
        task_context: dict[str, object] | None = None,
        profile_id: str = "default",
    ) -> dict[str, object]:
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
            "generated_content": None,
            "review_passed": False,
        }
        result = await self._run_graph(state)
        return result

    async def _run_graph(self, state: GraphState) -> dict[str, object]:
        """Execute the multi-step graph using compiled StateGraph or fallback."""
        if self._graph is not None:
            try:
                final_state = await self._graph.ainvoke(state)
                return self._format_result(final_state)
            except Exception as exc:
                log.warning("Compiled graph execution failed: %s, falling back to linear", exc)
        try:
            return await self._execute_graph_steps(state)
        except Exception as exc:
            log.warning("Graph execution failed: %s, falling back to single shot", exc)
            return await self._call_single_shot(
                state["messages"],
                state["task_context"],
                state.get("selected_model") or "default",
            )

    def _format_result(self, state: GraphState) -> dict[str, object]:
        return {
            "content": state.get("final_output", ""),
            "model": state.get("selected_model", "default"),
            "prompt": state.get("selected_prompt"),
            "quality_score": state.get("quality_score"),
            "retries": state["retry_count"],
            "warnings": list(state.get("warnings", [])),
        }

    async def _execute_graph_steps(self, state: GraphState) -> dict[str, object]:
        max_retries = self._max_retries

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
                    state["warnings"] = [
                        *list(state.get("warnings", [])),
                        "Retry {}/{}: quality {:.2f} < threshold {}".format(
                            state["retry_count"], max_retries, (quality or 0.0), self._quality_threshold
                        ),
                    ]
                else:
                    state["final_output"] = state["generated_output"]
                    state["warnings"] = [
                        *list(state.get("warnings", [])),
                        f"Max retries reached ({max_retries}), returning best output with quality {quality:.2f}",
                    ]
                    break

        return {
            "content": state.get("final_output", ""),
            "model": state.get("selected_model", "default"),
            "prompt": state.get("selected_prompt"),
            "quality_score": state.get("quality_score"),
            "retries": state["retry_count"],
            "warnings": list(state.get("warnings", [])),
        }

    async def _classify_step(self, state: GraphState) -> GraphState:
        return state

    async def _select_step(self, state: GraphState) -> GraphState:
        if self._router is not None:
            try:
                ctx = state.get("task_context", {})
                wt = cast(str, ctx.get("work_type", "feature"))
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
                work_type=state.get("task_context", {}).get("work_type") or "feature",
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

        if self._call_model is not None and self._scoring is not None:
            try:
                review_messages = [
                    {"role": "system", "content": self._REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Review this generated output:\n\n{output}"},
                ]
                result = await self._call_model(
                    profile_id=state.get("selected_model", "default"),
                    messages=review_messages,
                    work_type="review",
                )
                response_text = result.content if hasattr(result, "content") else str(result)
                verdict = _parse_review_response(response_text)
                state["quality_score"] = verdict.quality_score
                return state
            except Exception as exc:
                log.debug("Structured review failed, falling back to heuristic: %s", exc)

        state["quality_score"] = _heuristic_score(output)
        return state

    async def _call_single_shot(
        self,
        messages: list[object],
        task_context: dict[str, object],
        profile_id: str,
    ) -> dict[str, object]:
        if self._call_model is None:
            return {"content": "", "model": profile_id, "warnings": ["no call_model_fn"]}
        try:
            result = await self._call_model(
                profile_id=profile_id,
                messages=messages,
                work_type=task_context.get("work_type") or "feature",
            )
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


def _heuristic_score(content: str) -> float:
    """Heuristic quality scorer — fallback when structured review is unavailable."""
    score = 0.5
    if "def " in content or "class " in content:
        score += 0.15
    if "import " in content:
        score += 0.1
    if "return " in content:
        score += 0.1
    if len(content) > 50:
        score += 0.05
    if "test" in content.lower() or "assert" in content.lower():
        score += 0.1
    return min(score, 1.0)


def _parse_review_response(text: str) -> ReviewVerdict:
    """Parse LLM response into a ReviewVerdict.

    Tries to extract JSON from the response, stripping markdown fences.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].rstrip().endswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return ReviewVerdict.model_validate_json(text)
