"""External benchmark harness for SWE-bench, GAIA, WebArena.

Entry point for running standardized coding-agent benchmarks.
Collects results, computes aggregate scores, and emits JSON reports.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/.cache/gludd/benchmarks"))


@dataclass
class BenchmarkTask:
    task_id: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    benchmark: str
    task_id: str
    score: float
    agent_name: str
    duration_ms: float
    attempts: int
    resolved: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkSummary:
    benchmark: str
    agent_name: str
    total_tasks: int
    resolved_count: int
    mean_score: float
    total_duration_ms: float
    results: list[BenchmarkResult] = field(default_factory=list)

    def resolution_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.resolved_count / self.total_tasks


class BenchmarkSuite:
    def __init__(self, agent_name: str = "default") -> None:
        self.agent_name = agent_name
        self.results: list[BenchmarkResult] = []

    def run_benchmark(
        self,
        benchmark: str,
        tasks: list[BenchmarkTask],
        scorer: Any,
        runner: Any,
    ) -> BenchmarkSummary:
        resolved = 0
        start = time.perf_counter()
        for task in tasks:
            result = self._run_single(benchmark, task, runner, scorer)
            self.results.append(result)
            if result.resolved:
                resolved += 1
        elapsed = (time.perf_counter() - start) * 1000
        scores = [r.score for r in self.results if r.score >= 0]
        return BenchmarkSummary(
            benchmark=benchmark,
            agent_name=self.agent_name,
            total_tasks=len(tasks),
            resolved_count=resolved,
            mean_score=sum(scores) / len(scores) if scores else 0.0,
            total_duration_ms=elapsed,
            results=list(self.results),
        )

    def _run_single(
        self,
        benchmark: str,
        task: BenchmarkTask,
        runner: Any,
        scorer: Any,
    ) -> BenchmarkResult:
        t0 = time.perf_counter()
        try:
            output = runner(task)
            score = scorer(task, output)
            resolved = score >= 1.0
            error = None
        except Exception as exc:
            output = None
            score = 0.0
            resolved = False
            error = str(exc)
        duration = (time.perf_counter() - t0) * 1000
        return BenchmarkResult(
            benchmark=benchmark,
            task_id=task.task_id,
            score=score,
            agent_name=self.agent_name,
            duration_ms=duration,
            attempts=1,
            resolved=resolved,
            error=error,
            metadata={"output": output},
        )

    def report(self, output_path: Path | None = None) -> dict[str, Any]:
        # Results can be supplied directly by callers (rather than through
        # ``run_benchmark``), so preserve their agent identity when this suite
        # still has its default name.  A named suite remains authoritative.
        report_agent = self.agent_name
        if report_agent == "default" and self.results:
            result_agents = {result.agent_name for result in self.results}
            if len(result_agents) == 1:
                report_agent = next(iter(result_agents))
        by_benchmark: dict[str, list[BenchmarkResult]] = {}
        for r in self.results:
            by_benchmark.setdefault(r.benchmark, []).append(r)
        summaries: dict[str, dict[str, Any]] = {}
        for name, items in by_benchmark.items():
            resolved = sum(1 for r in items if r.resolved)
            scores = [r.score for r in items if r.score >= 0]
            summaries[name] = {
                "total_tasks": len(items),
                "resolved_count": resolved,
                "resolution_rate": resolved / len(items) if items else 0.0,
                "mean_score": sum(scores) / len(scores) if scores else 0.0,
                "duration_ms": sum(r.duration_ms for r in items),
            }
        report = {"agent": report_agent, "benchmarks": summaries}
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, indent=2))
        return report
