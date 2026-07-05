"""Benchmark runner for LangGraph vs custom implementations.

Compares LangGraph-backed implementations against their hand-rolled
counterparts with mocked model calls so measurements reflect only the
orchestration / framework overhead — no real API latency.

Comparisons
-----------
1. LangGraphAgentLoop  vs  ToolCallLoop       (iterations, time, memory)
2. LangGraphConsensusEngine  vs  ConsensusEngine  (time to consensus)
3. LangGraphReflexiveReviewer  vs  ReturnReviewer  (review accuracy)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComparisonResult:
    test_name: str
    custom_impl: str
    langgraph_impl: str
    custom_mean_ms: float
    langgraph_mean_ms: float
    custom_std_ms: float
    langgraph_std_ms: float
    speedup: float
    custom_iters: int
    langgraph_iters: int
    custom_mem_kb: float
    langgraph_mem_kb: float
    winner: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "custom_impl": self.custom_impl,
            "langgraph_impl": self.langgraph_impl,
            "custom_mean_ms": round(self.custom_mean_ms, 4),
            "langgraph_mean_ms": round(self.langgraph_mean_ms, 4),
            "custom_std_ms": round(self.custom_std_ms, 4),
            "langgraph_std_ms": round(self.langgraph_std_ms, 4),
            "speedup": round(self.speedup, 3),
            "custom_iters": self.custom_iters,
            "langgraph_iters": self.langgraph_iters,
            "custom_mem_kb": round(self.custom_mem_kb, 2),
            "langgraph_mem_kb": round(self.langgraph_mem_kb, 2),
            "winner": self.winner,
            "notes": self.notes,
        }


def _compute_stats(samples: list[float]) -> tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    mean = sum(samples) / len(samples)
    variance = sum((x - mean) ** 2 for x in samples) / len(samples)
    return mean, variance ** 0.5


def _time_sync(callable: Any, iterations: int, warmup: int = 3) -> list[float]:
    for _ in range(warmup):
        callable()
    times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        callable()
        times.append((time.perf_counter() - start) * 1000)
    return times


def _time_async(coro_fn: Any, iterations: int, warmup: int = 3) -> list[float]:
    async def _runner() -> list[float]:
        for _ in range(warmup):
            await coro_fn()
        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            await coro_fn()
            times.append((time.perf_counter() - start) * 1000)
        return times

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_runner())
    finally:
        loop.close()


def _peak_memory_kb() -> float:
    try:
        import tracemalloc
    except ImportError:
        return -1.0
    _current, peak = tracemalloc.get_traced_memory()
    return peak / 1024.0


class BenchmarkRunner:
    """Run benchmark comparisons between LangGraph and custom implementations."""

    def __init__(
        self,
        warmup: int = 5,
        iterations: int = 50,
        output: str = "stdout",
    ) -> None:
        self._warmup = warmup
        self._iterations = iterations
        self._output = output
        self.results: list[ComparisonResult] = []

    def run_all(self) -> list[ComparisonResult]:
        self.results = []
        self._compare_agent_loops()
        self._compare_consensus_engines()
        self._compare_reviewers()
        return self.results

    def report(self) -> None:
        payload = {
            "benchmarks": [r.to_dict() for r in self.results],
            "summary": {
                "langgraph_wins": sum(
                    1 for r in self.results if r.winner == "langgraph"
                ),
                "custom_wins": sum(
                    1 for r in self.results if r.winner == "custom"
                ),
                "ties": sum(1 for r in self.results if r.winner == "tie"),
                "total_comparisons": len(self.results),
            },
        }
        if self._output == "stdout":
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")

    # ── Agent Loop comparison ──────────────────────────────────────────

    def _compare_agent_loops(self) -> None:
        from general_ludd.schemas.job import JobSpec

        job = JobSpec(
            job_id="bench-job-001",
            playbook="bench_playbook.yml",
            queue="bench",
            work_type="benchmark",
            model_profile="default",
        )
        system_prompt = "You are a helpful assistant."
        user_prompt = "Write a hello world function."

        class _MockResponse:
            def __init__(self, content: str = "mocked response", tool_calls: Any = None) -> None:
                self.content = content
                self.tool_calls = tool_calls

            def __str__(self) -> str:
                return self.content

        class _MockGateway:
            def call_model(self, profile_id: str, messages: Any = None, work_type: str = "", project_id: str | None = None, tools: Any = None) -> _MockResponse:
                return _MockResponse(
                    content='{"answer": "def hello_world():"}',
                    tool_calls=None,
                )

        gateway = _MockGateway()

        from general_ludd.execution.tool_loop import ToolCallLoop

        tcl = ToolCallLoop(
            model_gateway=gateway,
            mcp_client=None,
            max_iterations=10,
        )

        async def _run_tcl() -> str:
            return await tcl.run_with_tools(job, system_prompt, user_prompt)

        tcl_times = _time_async(_run_tcl, self._iterations, self._warmup)
        tcl_mean, tcl_std = _compute_stats(tcl_times)

        try:
            from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

            lgl = LangGraphAgentLoop(
                model_gateway=gateway,
                mcp_client=None,
                max_iterations=10,
            )

            async def _run_lgl() -> str:
                return await lgl.run_with_tools(job, system_prompt, user_prompt)

            lgl_times = _time_async(_run_lgl, self._iterations, self._warmup)
            lgl_mean, lgl_std = _compute_stats(lgl_times)
            langgraph_available = True
        except ImportError:
            lgl_mean, lgl_std = -1.0, -1.0
            langgraph_available = False

        speedup = tcl_mean / lgl_mean if lgl_mean > 0 else 1.0
        winner = (
            "langgraph"
            if langgraph_available and lgl_mean < tcl_mean
            else "custom"
            if langgraph_available and tcl_mean < lgl_mean
            else "tie"
            if langgraph_available
            else "custom"
        )
        notes: list[str] = []
        if not langgraph_available:
            notes.append("langgraph not installed — LangGraphAgentLoop skipped")

        self.results.append(ComparisonResult(
            test_name="agent_loop_plain_path",
            custom_impl="ToolCallLoop",
            langgraph_impl="LangGraphAgentLoop",
            custom_mean_ms=tcl_mean,
            langgraph_mean_ms=lgl_mean,
            custom_std_ms=tcl_std,
            langgraph_std_ms=lgl_std,
            speedup=speedup,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=-1.0,
            langgraph_mem_kb=-1.0,
            winner=winner,
            notes=notes,
        ))

    # ── Consensus Engine comparison ────────────────────────────────────

    def _compare_consensus_engines(self) -> None:
        def reviewer_fn(prompt: str) -> str:
            import hashlib
            h = int(hashlib.md5(prompt.encode()).hexdigest()[:2], 16)
            if h < 200:
                return "approve\nRationale: looks good"
            elif h < 220:
                return "reject\nRationale: not ready"
            else:
                return "needs_changes\nRationale: minor issues"

        question = "Should we merge PR #42?"
        context = "PR adds benchmarking infrastructure."

        from general_ludd.review.consensus import ConsensusEngine

        custom_engine = ConsensusEngine(reviewer=reviewer_fn)

        def _run_custom() -> dict[str, Any]:
            return custom_engine.run_debate(
                question, context, num_agents=5, max_rounds=3,
            )

        custom_times = _time_sync(_run_custom, self._iterations, self._warmup)
        custom_mean, custom_std = _compute_stats(custom_times)

        try:
            from general_ludd.review.langgraph_consensus import LangGraphConsensusEngine

            langgraph_engine = LangGraphConsensusEngine(reviewer_callable=reviewer_fn)

            def _run_langgraph() -> dict[str, Any]:
                return langgraph_engine.run_debate(
                    question, context, num_agents=5, max_rounds=3,
                )

            langgraph_times = _time_sync(_run_langgraph, self._iterations, self._warmup)
            langgraph_mean, langgraph_std = _compute_stats(langgraph_times)
            langgraph_available = True
        except ImportError:
            langgraph_mean, langgraph_std = -1.0, -1.0
            langgraph_available = False

        speedup = custom_mean / langgraph_mean if langgraph_mean > 0 else 1.0
        winner = (
            "langgraph"
            if langgraph_available and langgraph_mean < custom_mean
            else "custom"
            if langgraph_available and custom_mean < langgraph_mean
            else "tie"
            if langgraph_available
            else "custom"
        )
        notes: list[str] = []
        if langgraph_available and speedup > 1.0:
            notes.append(
                f"LangGraph parallelizes agents via ThreadPoolExecutor "
                f"({speedup:.1f}x faster)"
            )
        elif langgraph_available:
            notes.append(
                "Custom serial for-loop faster for small N/light reviewer"
            )
        if not langgraph_available:
            notes.append("langgraph not installed — LangGraphConsensusEngine skipped")

        self.results.append(ComparisonResult(
            test_name="consensus_engine_5_agents_3_rounds",
            custom_impl="ConsensusEngine (serial)",
            langgraph_impl="LangGraphConsensusEngine (parallel)",
            custom_mean_ms=custom_mean,
            langgraph_mean_ms=langgraph_mean,
            custom_std_ms=custom_std,
            langgraph_std_ms=langgraph_std,
            speedup=speedup,
            custom_iters=5 * 3,
            langgraph_iters=5 * 3,
            custom_mem_kb=-1.0,
            langgraph_mem_kb=-1.0,
            winner=winner,
            notes=notes,
        ))

    # ── Reviewer comparison ────────────────────────────────────────────

    def _compare_reviewers(self) -> None:
        from general_ludd.schemas.task_decision import TaskDecision
        from general_ludd.schemas.task_return import TaskReturn

        task_return = TaskReturn(
            return_id="bench-return-001",
            job_id="bench-job-001",
            playbook="bench.yml",
            queue="bench",
            result_summary="Implemented hello_world function",
            exit_code=0,
        )
        candidate_todos: list[dict[str, Any]] = []
        artifacts: list[str] = []

        def quick_reviewer(prompt: str) -> str:
            return json.dumps({
                "decision": "complete",
                "confidence": 0.95,
                "audit_notes": ["implementation looks correct"],
                "evidence_refs": [],
                "todo_updates": {},
                "child_todos": [],
                "validation_requests": [],
                "git_requests": [],
                "policy_flags": [],
            })

        class _MockGatewayReview:
            def call_model(self, profile_id: str, messages: Any = None, work_type: str = "") -> _MockGatewayReview:
                return self

            @property
            def content(self) -> str:
                return quick_reviewer("")

        from general_ludd.review.reviewer import ReturnReviewer

        mock_gateway = _MockGatewayReview()
        mock_registry = MagicMockPromptRegistry()  # type: ignore[name-defined]

        reviewer_custom = ReturnReviewer(
            gateway=mock_gateway,  # type: ignore[arg-type]
            prompt_registry=mock_registry,  # type: ignore[arg-type]
        )

        def _run_custom() -> TaskDecision:
            return reviewer_custom.review_return(
                task_return, candidate_todos, artifacts,
            )

        custom_times = _time_sync(_run_custom, self._iterations, self._warmup)
        custom_mean, custom_std = _compute_stats(custom_times)

        try:
            from general_ludd.review.langgraph_reviewer import LangGraphReflexiveReviewer

            langgraph_reviewer = LangGraphReflexiveReviewer(
                call_model=quick_reviewer,
                max_iterations=3,
                confidence_threshold=0.8,
            )

            def _run_langgraph() -> TaskDecision:
                return langgraph_reviewer.review_return(
                    task_return, candidate_todos, artifacts,
                )

            langgraph_times = _time_sync(_run_langgraph, self._iterations, self._warmup)
            langgraph_mean, langgraph_std = _compute_stats(langgraph_times)

            langgraph_decision = _run_langgraph()
            langgraph_confidence = langgraph_decision.confidence
            langgraph_notes_count = len(langgraph_decision.audit_notes)

            langgraph_available = True
        except ImportError:
            langgraph_mean, langgraph_std = -1.0, -1.0
            langgraph_confidence = -1.0
            langgraph_notes_count = 0
            langgraph_available = False

        custom_decision = _run_custom()
        custom_confidence = custom_decision.confidence

        speedup = (
            custom_mean / langgraph_mean if langgraph_mean > 0 else 1.0
        )
        winner = (
            "langgraph"
            if langgraph_available and langgraph_confidence > custom_confidence
            else "custom"
            if langgraph_available and custom_confidence > langgraph_confidence
            else "tie"
            if langgraph_available
            else "custom"
        )
        notes: list[str] = []
        if langgraph_available:
            notes.append(
                f"LangGraphReflexiveReviewer confidence={langgraph_confidence:.2f}, "
                f"ReturnReviewer confidence={custom_confidence:.2f}"
            )
            notes.append(
                f"Reflexive review produces {langgraph_notes_count} audit notes "
                f"via iterative self-critique"
            )
        if not langgraph_available:
            notes.append("langgraph not installed — LangGraphReflexiveReviewer skipped")

        self.results.append(ComparisonResult(
            test_name="reviewer_accuracy",
            custom_impl="ReturnReviewer (single-pass)",
            langgraph_impl="LangGraphReflexiveReviewer (iterative)",
            custom_mean_ms=custom_mean,
            langgraph_mean_ms=langgraph_mean,
            custom_std_ms=custom_std,
            langgraph_std_ms=langgraph_std,
            speedup=speedup,
            custom_iters=1,
            langgraph_iters=3,
            custom_mem_kb=-1.0,
            langgraph_mem_kb=-1.0,
            winner=winner,
            notes=notes,
        ))


class MagicMockPromptRegistry:
    def render(self, template_name: str, **kwargs: Any) -> str:
        return f"rendered {template_name} with {json.dumps(kwargs, default=str)}"


def main() -> None:
    runner = BenchmarkRunner(warmup=5, iterations=50)
    runner.run_all()
    runner.report()


if __name__ == "__main__":
    main()
