# AG.16: External Benchmarks — SWE-bench, GAIA, WebArena Integration

**Status:** Draft
**Target:** gludd v0.2.0
**Depends on:** AG.1 (agent evaluation framework)


## 1. Problem

Without standardized external benchmarks, gludd cannot measure agent progress
objectively. Hand-crafted eval suites only test what we think matters; they miss
regressions on real-world tasks. SWE-bench, GAIA, and WebArena are the three
most widely-used coding-agent benchmarks — integrating them lets us track
capability improvement across releases and compare against published baselines.

## 2. Benchmarks covered

| Benchmark | Domain | Task type | Metric |
|---|---|---|---|
| SWE-bench Verified | GitHub issue resolution | Patch generation | % resolved (300 tasks) |
| GAIA | Multi-step reasoning | QA with tool use | Pass@1 (466 questions) |
| WebArena | Web navigation | Task completion | Success rate (812 tasks) |

## 3. Architecture

```text
ag15_benchmarks/
├── __init__.py
├── benchmark_harness.py   # BenchmarkSuite, BenchmarkResult, aggregation
├── swe_bench.py           # SWE-bench loader, runner, resolver
└── gaia.py                # GAIA loader, runner, scorer
```

Each benchmark module exports:
- `load_tasks() -> list[BenchmarkTask]` — parse the benchmark dataset
- `run_task(agent, task) -> TaskResult` — execute one task
- `score_result(result, expected) -> float` — compute 0–1 score

## 4. Data flow

1. Dataset loaded from local cache (`~/.cache/gludd/benchmarks/<bench>/`)
2. Each task run through the agent under test (single model call or full agent loop)
3. Results scored against reference output
4. Aggregated across all tasks → summary JSON + Markdown report

## 5. SWE-bench integration

SWE-bench Verified subset (300 instances). Each instance:
- A GitHub repo at a specific commit
- An issue description
- A patch that fixes the issue (hidden during eval)
- A test (`FAIL_TO_PASS`) that the patch must make pass

Agent produces a patch file → harness applies it → tests run → pass → resolved.

## 6. GAIA integration

GAIA Level 1 validation set (53 questions). Each question:
- A natural language question requiring multi-step reasoning
- Annotated steps and final answer

Agent answers → answer compared to ground truth (exact/normalized match).

## 7. WebArena integration

WebArena subset (100 tasks). Each task:
- A goal (e.g., "buy the cheapest red chair")
- A set of expected states/assertions

Agent navigates a Dockerized web environment → assertions checked → scored.

## 8. BenchmarkResult schema

```python
@dataclass
class BenchmarkResult:
    benchmark: str           # swe-bench, gaia, webarena
    task_id: str
    score: float             # 0.0–1.0
    agent_name: str
    duration_ms: float
    attempts: int
    resolved: bool
    error: str | None
    metadata: dict
```

## 9. Usage

```bash
python -m general_ludd.ag15_benchmarks.benchmark_harness \
    --benchmark swe-bench --max-tasks 10 --agent default
```

Output: JSON report at `reports/benchmarks/swe-bench-<ts>.json`

## References

- SWE-bench: https://www.swebench.com/
- GAIA: https://huggingface.co/datasets/gaia-benchmark/GAIA
- WebArena: https://webarena.dev/
