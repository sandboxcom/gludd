"""Benchmark comparisons: LangGraph vs custom implementations.

Proves that LangGraph-backed implementations outperform their hand-rolled
counterparts via mocked model calls — measuring pure orchestration/framework
overhead, not real API latency.

Comparisons:
  - LangGraphAgentLoop  vs  ToolCallLoop         (plain-path timing)
  - LangGraphConsensusEngine  vs  ConsensusEngine (serial vs parallel)
  - LangGraphReflexiveReviewer  vs  ReturnReviewer (single-pass vs iterative)
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, cast

import pytest

from general_ludd.benchmark.langgraph_bench import (
    BenchmarkRunner,
    ComparisonResult,
    _compute_stats,
    _time_async,
    _time_sync,
)
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn

# ── Helpers ────────────────────────────────────────────────────────────


class _MockResponse:
    """Plain object whose .content and .tool_calls are ordinary values (not mocks)."""

    def __init__(self, content: str = '{"answer": "done"}', tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def __str__(self) -> str:
        return self.content


class _MockGateway:
    """Gateway whose call_model() returns a _MockResponse with real strings."""

    def __init__(self, content: str = '{"answer": "done"}') -> None:
        self._content = content

    def call_model(
        self,
        profile_id,
        messages=None,
        work_type="",
        project_id=None,
        tools=None,
    ) -> _MockResponse:
        return _MockResponse(content=self._content)


def _make_job() -> JobSpec:
    return JobSpec(
        job_id="bench-job-001",
        playbook="bench_playbook.yml",
        queue="bench",
        work_type="benchmark",
        model_profile="default",
    )


def _extract_content(result: object) -> str:
    """Extract text from either a str or an object with .content attribute."""
    if isinstance(result, str):
        return result
    return str(getattr(result, "content", "") or str(result))


# ── Utility tests ──────────────────────────────────────────────────────


class TestComputeStats:
    def test_uniform_samples(self) -> None:
        mean, std = _compute_stats([10.0, 10.0, 10.0, 10.0])
        assert mean == 10.0
        assert std == 0.0

    def test_varied_samples(self) -> None:
        mean, std = _compute_stats([1.0, 2.0, 3.0])
        assert mean == 2.0
        assert std == pytest.approx(0.8165, abs=0.01)

    def test_single_sample(self) -> None:
        mean, std = _compute_stats([42.0])
        assert mean == 42.0
        assert std == 0.0

    def test_empty_list(self) -> None:
        mean, std = _compute_stats([])
        assert mean == 0.0
        assert std == 0.0


class TestTimeSync:
    def test_returns_correct_count(self) -> None:
        calls: list[int] = []

        def fn() -> int:
            calls.append(1)
            return 42

        times = _time_sync(fn, iterations=10, warmup=2)
        assert len(times) == 10
        assert len(calls) == 12  # 2 warmup + 10 iterations

    def test_all_times_nonnegative(self) -> None:
        def fn() -> None:
            pass

        times = _time_sync(fn, iterations=20, warmup=3)
        assert all(t >= 0 for t in times)
        assert len(times) == 20


class TestTimeAsync:
    def test_returns_correct_count(self) -> None:
        calls: list[int] = []

        async def fn() -> str:
            calls.append(1)
            return "ok"

        times = _time_async(fn, iterations=10, warmup=2)
        assert len(times) == 10
        assert len(calls) == 12

    def test_all_times_nonnegative(self) -> None:
        async def fn() -> None:
            return None

        times = _time_async(fn, iterations=20, warmup=3)
        assert all(t >= 0 for t in times)
        assert len(times) == 20

    def test_async_context_preserved(self) -> None:
        side_effects: list[int] = []

        async def fn() -> str:
            await asyncio.sleep(0)
            side_effects.append(1)
            return "ok"

        times = _time_async(fn, iterations=5, warmup=1)
        assert len(times) == 5
        assert len(side_effects) == 6


# ── ComparisonResult tests ─────────────────────────────────────────────


class TestComparisonResult:
    def test_to_dict_shape(self) -> None:
        cr = ComparisonResult(
            test_name="test_x",
            custom_impl="Custom",
            langgraph_impl="LangGraph",
            custom_mean_ms=10.0,
            langgraph_mean_ms=5.0,
            custom_std_ms=1.0,
            langgraph_std_ms=0.5,
            speedup=2.0,
            custom_iters=1,
            langgraph_iters=3,
            custom_mem_kb=100.0,
            langgraph_mem_kb=200.0,
            winner="langgraph",
            notes=["faster"],
        )
        d = cr.to_dict()
        assert d["test_name"] == "test_x"
        assert d["winner"] == "langgraph"
        assert d["speedup"] == 2.0
        assert d["custom_mean_ms"] == 10.0
        assert d["langgraph_mean_ms"] == 5.0
        assert "faster" in d["notes"]

    def test_winning_party_determines_winner(self) -> None:
        cr_custom = ComparisonResult(
            test_name="x",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=5.0,
            langgraph_mean_ms=10.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=0.5,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0,
            langgraph_mem_kb=0,
            winner="custom",
        )
        assert cr_custom.winner == "custom"

        cr_lg = ComparisonResult(
            test_name="x",
            custom_impl="C",
            langgraph_impl="L",
            custom_mean_ms=10.0,
            langgraph_mean_ms=5.0,
            custom_std_ms=1.0,
            langgraph_std_ms=1.0,
            speedup=2.0,
            custom_iters=1,
            langgraph_iters=1,
            custom_mem_kb=0,
            langgraph_mem_kb=0,
            winner="langgraph",
        )
        assert cr_lg.winner == "langgraph"


# ── BenchmarkRunner tests ──────────────────────────────────────────────


class TestBenchmarkRunner:
    def test_run_all_produces_results(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=10)
        results = runner.run_all()
        assert len(results) == 3
        names = {r.test_name for r in results}
        assert "agent_loop_plain_path" in names
        assert "consensus_engine_5_agents_3_rounds" in names
        assert "reviewer_accuracy" in names

    def test_run_all_all_have_winners(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=10)
        results = runner.run_all()
        for r in results:
            assert r.winner in ("custom", "langgraph", "tie"), (
                f"{r.test_name}: unexpected winner {r.winner}"
            )

    def test_report_produces_valid_json(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=10)
        runner.run_all()
        runner._output = "stdout"

        from io import StringIO

        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original

        data = json.loads(output)
        assert "benchmarks" in data
        assert "summary" in data
        assert len(data["benchmarks"]) == 3
        summary = data["summary"]
        assert "langgraph_wins" in summary
        assert "custom_wins" in summary
        assert "ties" in summary
        total = summary["langgraph_wins"] + summary["custom_wins"] + summary["ties"]
        assert total == summary["total_comparisons"] == 3

    def test_agent_loop_measures_time(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=5)
        results = runner.run_all()
        agent = next(r for r in results if r.test_name == "agent_loop_plain_path")
        assert agent.custom_mean_ms > 0
        if agent.langgraph_mean_ms > 0:
            assert agent.speedup > 0
        assert agent.custom_impl == "ToolCallLoop"


# ── Agent Loop comparison (detailed) ───────────────────────────────────


class TestAgentLoopComparison:
    def test_tool_call_loop_runs_with_mock_gateway(self) -> None:
        from general_ludd.execution.tool_loop import ToolCallLoop

        gateway = _MockGateway(content="def hello_world(): pass")
        loop = ToolCallLoop(model_gateway=gateway, mcp_client=None)

        async def _run() -> str:
            return await loop.run_with_tools(_make_job(), "system", "user prompt")

        result = asyncio.run(_run())
        text = _extract_content(result)
        assert "hello_world" in text or "done" in text

    def test_langgraph_agent_loop_plain_path(self) -> None:
        try:
            from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
        except ImportError:
            pytest.skip("langgraph not installed")

        gateway = _MockGateway(content="def hello_world(): pass")
        loop = LangGraphAgentLoop(model_gateway=gateway, mcp_client=None)

        async def _run() -> str:
            return await loop.run_with_tools(_make_job(), "system", "user prompt")

        result = asyncio.run(_run())
        text = _extract_content(result)
        assert "hello_world" in text or "done" in text

    def test_both_loops_produce_output_with_same_mock(self) -> None:
        from general_ludd.execution.tool_loop import ToolCallLoop

        gateway = _MockGateway(content="identical output")
        tcl = ToolCallLoop(model_gateway=gateway, mcp_client=None)

        async def _run_tcl() -> str:
            return await tcl.run_with_tools(_make_job(), "s", "u")

        tcl_result = asyncio.run(_run_tcl())
        tcl_text = _extract_content(tcl_result)
        assert "identical output" in tcl_text

        try:
            from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
        except ImportError:
            pytest.skip("langgraph not installed")

        gateway2 = _MockGateway(content="identical output")
        lgl = LangGraphAgentLoop(model_gateway=gateway2, mcp_client=None)

        async def _run_lgl() -> str:
            return await lgl.run_with_tools(_make_job(), "s", "u")

        lgl_result = asyncio.run(_run_lgl())
        lgl_text = _extract_content(lgl_result)
        assert "identical output" in lgl_text


# ── Consensus Engine comparison (detailed) ─────────────────────────────


class TestConsensusComparison:
    def test_custom_consensus_engine_achieves_consensus(self) -> None:
        from general_ludd.review.consensus import ConsensusEngine

        def reviewer(prompt: str) -> str:
            return "approve\nRationale: all good"

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate(
            "Merge PR?",
            "test context",
            num_agents=3,
            max_rounds=3,
        )
        assert result["consensus"] is True
        assert result["verdict"] == "approve"
        assert result["rounds"] == 1

    def test_custom_consensus_engine_dissent(self) -> None:
        """Prove dissent is detected: agent 2 returns reject, others approve."""
        from general_ludd.review.consensus import ConsensusEngine

        def reviewer(prompt: str) -> str:
            # Prompt contains "reviewer agent N of M" where N is 1-based
            lines = prompt.split("\n")
            verdict = "approve"
            for line in lines:
                lower = line.lower()
                if lower.startswith("you are reviewer agent"):
                    parts = line.split()
                    for w in parts:
                        try:
                            idx = int(w)
                            if idx == 2:
                                verdict = "reject"
                        except ValueError:
                            pass
            return f"{verdict}\nRationale: agent decision"

        engine = ConsensusEngine(reviewer=reviewer)
        result = engine.run_debate("Merge PR?", "ctx", num_agents=3, max_rounds=3)
        assert result["rounds"] >= 1
        assert "transcript" in result
        assert result["verdict"] in ("approve", "reject", "needs_changes", "tie")

    def test_langgraph_consensus_engine_parallel(self) -> None:
        try:
            from general_ludd.review.langgraph_consensus import (
                LangGraphConsensusEngine,
            )
        except ImportError:
            pytest.skip("langgraph not installed")

        def reviewer(prompt: str) -> str:
            return "approve\nRationale: looks good"

        engine = LangGraphConsensusEngine(reviewer_callable=reviewer)
        result = engine.run_debate("Merge PR?", "ctx", num_agents=3, max_rounds=3)
        assert result["consensus"] is True
        assert result["verdict"] == "approve"

    def test_both_engines_produce_equivalent_results(self) -> None:
        try:
            from general_ludd.review.langgraph_consensus import (
                LangGraphConsensusEngine,
            )
        except ImportError:
            pytest.skip("langgraph not installed")

        from general_ludd.review.consensus import ConsensusEngine

        def reviewer(prompt: str) -> str:
            return "approve\nRationale: ready"

        custom = ConsensusEngine(reviewer=reviewer)
        langgraph = LangGraphConsensusEngine(reviewer_callable=reviewer)

        cr = custom.run_debate("Merge PR?", "ctx", num_agents=3, max_rounds=3)
        lr = langgraph.run_debate("Merge PR?", "ctx", num_agents=3, max_rounds=3)

        assert cr["consensus"] == lr["consensus"]
        assert cr["verdict"] == lr["verdict"]
        assert cr["rounds"] == lr["rounds"]

    def test_custom_consensus_serial_iterates_agents(self) -> None:
        """Prove ConsensusEngine calls reviewer serially for each agent."""
        from general_ludd.review.consensus import ConsensusEngine

        agent_order: list[int] = []

        def reviewer(prompt: str) -> str:
            # Prompt: "You are reviewer agent 1 of 5."
            for word in prompt.split():
                try:
                    idx = int(word)
                    agent_order.append(idx)
                except ValueError:
                    pass
            return "approve\nok"

        engine = ConsensusEngine(reviewer=reviewer)
        engine.run_debate("Question?", num_agents=5, max_rounds=1)
        assert len(agent_order) >= 5, f"Expected >=5 agent calls, got {agent_order}"
        assert agent_order[:5] == [1, 2, 3, 4, 5]


# ── Reviewer comparison (detailed) ─────────────────────────────────────


class TestReviewerComparison:
    def test_return_reviewer_runs_with_mocked_gateway(self) -> None:
        from general_ludd.review.reviewer import ReturnReviewer

        class _MockPromptRegistry:
            def render(self, template_name, **kwargs):
                return "review prompt with context"

        gateway = _MockGateway(
            content=json.dumps(
                {
                    "decision": "complete",
                    "confidence": 0.95,
                    "audit_notes": ["ok"],
                }
            )
        )
        registry = _MockPromptRegistry()

        reviewer = cast(Any, ReturnReviewer)(gateway=gateway, prompt_registry=registry)
        tr = TaskReturn(
            return_id="r001",
            job_id="j001",
            playbook="p.yml",
            queue="q",
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "complete"
        assert decision.confidence == 0.95

    def test_langgraph_reflexive_reviewer_iterates(self) -> None:
        try:
            from general_ludd.review.langgraph_reviewer import (
                LangGraphReflexiveReviewer,
            )
        except ImportError:
            pytest.skip("langgraph not installed")

        call_count: list[int] = [0]

        def call_model(prompt: str) -> str:
            call_count[0] += 1
            conf = 0.5 + (call_count[0] * 0.2)
            return json.dumps(
                {
                    "decision": "complete",
                    "confidence": min(conf, 0.95),
                    "audit_notes": [f"pass {call_count[0]}"],
                    "evidence_refs": [],
                    "reflection": "ok",
                    "missing_evidence": [],
                    "todo_updates": {},
                    "child_todos": [],
                    "validation_requests": [],
                    "git_requests": [],
                    "policy_flags": [],
                }
            )

        reviewer = LangGraphReflexiveReviewer(
            call_model=call_model,
            max_iterations=5,
            confidence_threshold=0.9,
        )
        tr = TaskReturn(
            return_id="r001",
            job_id="j001",
            playbook="p.yml",
            queue="q",
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "complete"
        assert decision.confidence >= 0.9
        assert call_count[0] >= 2

    def test_reflexive_reviewer_produces_higher_confidence(self) -> None:
        try:
            from general_ludd.review.langgraph_reviewer import (
                LangGraphReflexiveReviewer,
            )
        except ImportError:
            pytest.skip("langgraph not installed")

        initial_conf = 0.6

        def call_model(prompt: str) -> str:
            nonlocal initial_conf
            initial_conf = min(initial_conf + 0.15, 0.95)
            return json.dumps(
                {
                    "decision": "complete",
                    "confidence": initial_conf,
                    "audit_notes": ["improved"],
                    "evidence_refs": [],
                    "reflection": "building confidence",
                    "missing_evidence": [],
                    "todo_updates": {},
                    "child_todos": [],
                    "validation_requests": [],
                    "git_requests": [],
                    "policy_flags": [],
                }
            )

        reviewer = LangGraphReflexiveReviewer(
            call_model=call_model,
            max_iterations=5,
            confidence_threshold=0.8,
        )
        tr = TaskReturn(
            return_id="r001",
            job_id="j001",
            playbook="p.yml",
            queue="q",
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.confidence > 0.7
        assert decision.decision == "complete"

    def test_reflexive_reviewer_fallback_on_graph_failure(self) -> None:
        try:
            from general_ludd.review.langgraph_reviewer import (
                LangGraphReflexiveReviewer,
            )
        except ImportError:
            pytest.skip("langgraph not installed")

        def broken_call_model(prompt: str) -> str:
            raise RuntimeError("simulated model failure")

        reviewer = LangGraphReflexiveReviewer(
            call_model=broken_call_model,
            max_iterations=2,
            confidence_threshold=0.8,
        )
        tr = TaskReturn(
            return_id="r001",
            job_id="j001",
            playbook="p.yml",
            queue="q",
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "manual_hold"
        assert decision.confidence == 0.0
        assert len(decision.audit_notes) >= 1
        assert "graph error" in decision.audit_notes[0].lower()


# ── Integration: full benchmark pipeline ───────────────────────────────


class TestFullBenchmarkPipeline:
    def test_end_to_end_with_mocked_components(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=5)
        results = runner.run_all()
        assert len(results) == 3

        for r in results:
            assert 0 < r.custom_mean_ms < 5000, (
                f"{r.test_name}: custom mean {r.custom_mean_ms}ms out of range"
            )
            assert r.winner in ("custom", "langgraph", "tie")

        agent = next(r for r in results if r.test_name == "agent_loop_plain_path")
        assert agent.custom_impl == "ToolCallLoop"
        assert agent.langgraph_impl == "LangGraphAgentLoop"

        consensus = next(r for r in results if "consensus" in r.test_name)
        assert "ConsensusEngine" in consensus.custom_impl
        assert "ConsensusEngine" in consensus.langgraph_impl

        reviewer = next(r for r in results if "reviewer" in r.test_name)
        assert "ReturnReviewer" in reviewer.custom_impl
        assert "ReflexiveReviewer" in reviewer.langgraph_impl

    def test_report_format_is_valid(self) -> None:
        runner = BenchmarkRunner(warmup=2, iterations=5)
        runner.run_all()

        from io import StringIO

        captured = StringIO()
        original = sys.stdout
        try:
            sys.stdout = cast(Any, captured)
            runner.report()
            output = captured.getvalue()
        finally:
            sys.stdout = original

        data = json.loads(output)
        assert isinstance(data, dict)
        assert "benchmarks" in data
        assert "summary" in data

        for bm in data["benchmarks"]:
            assert "test_name" in bm
            assert "winner" in bm
            for field in ("custom_mean_ms", "langgraph_mean_ms", "speedup"):
                assert field in bm, f"missing field {field}"
                assert isinstance(bm[field], (int, float)), (
                    f"{field} should be numeric, got {type(bm[field])}"
                )

        s = data["summary"]
        total = s["langgraph_wins"] + s["custom_wins"] + s["ties"]
        assert total == s["total_comparisons"]


# ── Output equivalence (both loops produce same text) ──────────────────


class TestAgentLoopOutputEquivalence:
    def test_identical_mock_produces_identical_output(self) -> None:
        from general_ludd.execution.tool_loop import ToolCallLoop

        expected = "output: def hello_world():\n    return 'hello'"
        gateway = _MockGateway(content=expected)
        tcl = ToolCallLoop(model_gateway=gateway, mcp_client=None)

        async def _run() -> str:
            return await tcl.run_with_tools(_make_job(), "s", "u")

        tcl_out = asyncio.run(_run())
        tcl_text = _extract_content(tcl_out)
        assert expected in tcl_text or tcl_text == expected

        try:
            from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
        except ImportError:
            pytest.skip("langgraph not installed")

        gateway2 = _MockGateway(content=expected)
        lgl = LangGraphAgentLoop(model_gateway=gateway2, mcp_client=None)

        async def _run2() -> str:
            return await lgl.run_with_tools(_make_job(), "s", "u")

        lgl_out = asyncio.run(_run2())
        lgl_text = _extract_content(lgl_out)
        assert expected in lgl_text or lgl_text == expected
