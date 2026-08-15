# AG.1 — Agent Evaluation Framework

**Design Document | 2026-07-13 | Status: Draft**

---

## 1. Problem

gludd has no way to measure agent quality. No benchmarks, no trajectory
evaluation, no regression detection. The current verification surface (52
hook-runtime tests, lint, typecheck) only checks infrastructure boot — not
whether the infrastructure produces better agent behavior.

**Without evaluation, every agent change is a blind push.**

---

## 2. How Agent Performance Is Measured

### 2.1 Primary Metrics (per session)

| Metric | Definition | Target |
|--------|-----------|--------|
| **Task Completion Rate** | tasks completed / tasks dispatched | >= 0.90 |
| **Steps per Task** | avg tool calls per completed task | <= 15 |
| **Tokens per Task** | avg tokens consumed per completed task | <= 12k |
| **Time-to-Completion** | wall-clock seconds dispatch -> result | <= 300s |
| **Tool Accuracy** | 1.0 - (policy violations / total tool calls) | >= 0.95 |
| **Safety Violation Rate** | unblocked security violations / session | 0 |
| **Code Quality Rate** | gate-pass rate on generated code | 1.0 |

### 2.2 Secondary Metrics

| Metric | Use |
|--------|-----|
| **Dispatch Wave Size** | Monitor floor compliance |
| **Subagent Utilization** | Detect idle agents |
| **Hot-File Conflict Rate** | Detect serialization gaps |
| **Premature Stop Rate** | Monitor anti-stop effectiveness |
| **Result Codification Rate** | Detect nothing-dropped incidents |

---

## 3. Benchmark Metrics: FAIL_TO_PASS / PASS_TO_PASS

SWE-bench classification applied to gludd's self-evaluation scenarios.

### 3.1 Categories

```text
                BEFORE agent    AFTER agent
                ────────────    ───────────
FAIL_TO_PASS      FAIL           PASS         <-- agent FIXED this
PASS_TO_PASS      PASS           PASS         <-- no regression
FAIL_TO_FAIL      FAIL           FAIL         <-- agent didn't fix this
PASS_TO_FAIL      PASS           FAIL         <-- agent BROKE this (regression)
```

A successful agent **maximizes FAIL_TO_PASS, maximizes PASS_TO_PASS, eliminates PASS_TO_FAIL**.

### 3.2 Scoring

```text
resolution_rate = FAIL_TO_PASS / (FAIL_TO_PASS + FAIL_TO_FAIL + PASS_TO_FAIL)
regression_rate = PASS_TO_FAIL / total_tests
```

### 3.3 Application to gludd

Each benchmark scenario defines a test suite (pytest or behavioral assertions).
The harness runs tests before and after the agent's work:

```text
1. checkout baseline_commit
2. run test suite -> capture BEFORE state (pass/fail per test)
3. apply agent's change (or checkout agent_commit)
4. run test suite -> capture AFTER state
5. classify each test into the 2x2 matrix
6. compute resolution_rate + regression_rate
```

Benchmark scenarios (`benchmarks/`):

| Scenario | What it measures |
|----------|-----------------|
| `dispatch-floor/` | Does agent maintain >=7 subagents after a user message? |
| `commit-gate/` | Does agent commit with evidence (hash + test count)? |
| `task-ledger/` | Does agent update TASKS.md after each result? |
| `hot-file-conflict/` | Does agent serialize edits to shared files? |
| `anti-stop/` | Does agent continue working past a gap report? |
| `enhancement-ratio/` | Does >=50% of dispatches target enhancements? |

### 3.4 Scenario Format

```yaml
# benchmarks/dispatch-floor/scenario.yaml
name: "dispatch-floor"
version: 1
setup:
  - action: write_file
    path: TASKS.md
    content: "- [ ] task-1\n- [ ] task-2"
steps:
  - user_message: "Continue working on the tasks."
  - wait_for_idle: 180
assertions:
  - type: behavioral
    description: "First response wave has >=7 dispatches"
    check: trajectory.waves[0].dispatch_count >= 7
  - type: test_suite
    name: "dispatch-floor-tests"
    command: "pytest tests/unit/test_dispatch_floor.py -q"
```

---

## 4. Self-Evaluation Harness

Runs gludd sessions against standardized tasks and measures outcomes.

### 4.1 Architecture

```text
Scenario Loader --> Orchestrator --> Evaluator Pipeline --> Reporter
   (yaml)         (runs agent)      (scores trajectory)    (json + md)
```

### 4.2 Core Components

```python
# src/general_ludd/eval/harness.py

class ScenarioRunner:
    """Loads scenario, launches gludd session, collects trajectory."""
    async def run(self, scenario: Scenario) -> Trajectory:
        setup_env(scenario.setup)
        session = await launch_agent_session(model=scenario.model)
        for step in scenario.steps:
            await session.send_message(step.user_message)
            await session.wait_for_idle(timeout=step.wait_for_idle)
        trajectory = await session.collect_trajectory()
        return trajectory

class EvaluatorPipeline:
    """Runs registered evaluators, produces scores."""
    def evaluate(self, trajectory: Trajectory) -> dict[str, EvalResult]:
        return {e.name: e.evaluate(trajectory) for e in self.evaluators}

class Reporter:
    """Produces JSON + markdown reports with FAIL_TO_PASS breakdown."""
    def report(self, scenario, results) -> Report: ...
```

### 4.3 Trajectory Capture

Record every tool call via opencode hooks:

| Hook | What it records |
|------|----------------|
| `tool.execute.after` | Tool name, sanitized args, latency, exit code |
| `skill.execute.after` | Skill name, context size |
| `session.idle` | Turn count, total tokens, open tasks |
| `text.complete` | Outgoing text classification (status/done/dispatch/question) |

### 4.4 Storage

```sql
Table: agent_trajectories
  trajectory_id UUID PK
  session_id TEXT
  started_at / ended_at TIMESTAMP
  turn_count INT
  total_tokens_estimate BIGINT
  events JSONB          -- compressed tool-call records
  scores JSONB          -- per-evaluator scores
  metadata JSONB        -- model, provider, agent config hash
```

---

## 5. Integration with Test Infrastructure

### 5.1 Evaluation as Pytest Tests

```python
# tests/eval/test_dispatch_floor.py

def test_dispatch_floor_scenario():
    trajectory = ScenarioRunner().run(Scenario.load("benchmarks/dispatch-floor"))
    assert trajectory.resolution_rate >= 0.90
    assert trajectory.regression_rate == 0

def test_commit_gate_scenario():
    trajectory = ScenarioRunner().run(Scenario.load("benchmarks/commit-gate"))
    assert trajectory.regression_rate == 0
```

### 5.2 Make Targets

| Target | Purpose |
|--------|---------|
| `make eval-scenario SCENARIO=<name>` | Run a single benchmark scenario |
| `make eval-all` | Run all scenarios, produce summary report |
| `make eval-diff OLD=<sha> NEW=<sha>` | Compare two commits on benchmark suite |
| `make eval-baseline` | Record current commit as quality baseline |
| `make eval-regression-check` | Compare current vs. baseline; fail on regression |
| `make test-eval` | Run evaluation scenario tests (pytest shard) |

### 5.3 Gate Integration

```text
make gate
  ├── lint
  ├── typecheck
  ├── collect-check
  ├── test-unit
  ├── test-integration
  ├── test-eval        <-- NEW: evaluation scenario tests
  └── test-e2e
```

`test-eval` is a separate shard — it runs real agent sessions and is slower than
unit tests. CI runs it as a separate job, opt-in via `config/eval.yml`:

```yaml
# config/eval.yml
ci_enabled: false       # set true to run eval in CI
ci_frequency: "weekly"  # per-commit | per-push | weekly
```

### 5.4 CI Gating Policy

Evaluation regressions are **advisory** in CI — they do NOT block the release
pipeline. Eval is for trend detection, not a ship blocker. A separate
`HumanTodo(category=agent_regression)` is filed when metrics degrade.

---

## 6. Extension Points

### 6.1 Evaluator Interface

```python
# src/general_ludd/eval/evaluator.py

class Evaluator(ABC):
    name: str
    version: int
    description: str

    @abstractmethod
    async def evaluate(self, trajectory: Trajectory) -> EvalResult: ...
    @abstractmethod
    def requires(self) -> list[str]: ...

@dataclass
class EvalResult:
    evaluator_name: str
    evaluator_version: int
    score: float            # 0.0 to 1.0
    summary: str
    details: dict           # per-check breakdown
    warnings: list[str]

@dataclass
class Trajectory:
    session_id: str
    model: str
    commit_sha: str
    tool_calls: list[ToolCallRecord]
    task_results: list[TaskResult]
    commit_log: list[CommitRecord]
    metadata: dict
```

### 6.2 Registration Mechanisms

1. **Entry points** (`pyproject.toml`):
   ```toml
   [project.entry-points."gludd.evaluators"]
   tool_accuracy = "general_ludd.eval.evaluators:ToolUseAccuracy"
   task_completion = "general_ludd.eval.evaluators:TaskCompletion"
   ```

2. **Config file** (`config/evaluators.yml`):
   ```yaml
   evaluators:
     - name: "custom_lint_checker"
       module: "my_project.eval.lint_evaluator"
       class: "LintChecker"
       enabled: true
   ```

### 6.3 Custom Evaluator Example

```python
class LintChecker(Evaluator):
    name = "lint_checker"
    version = 1

    def requires(self) -> list[str]:
        return ["tool_calls"]

    async def evaluate(self, trajectory: Trajectory) -> EvalResult:
        python_edits = [tc for tc in trajectory.tool_calls
                        if tc.tool in ("edit", "write")
                        and any(f.endswith(".py") for f in tc.files_touched)]
        lint_runs = [tc for tc in trajectory.tool_calls
                     if tc.tool == "bash" and "make lint" in tc.command]
        lint_passes = all(r.exit_code == 0 for r in lint_runs)
        return EvalResult(
            evaluator_name=self.name,
            evaluator_version=self.version,
            score=1.0 if lint_passes else 0.0,
            summary=f"edits: {len(python_edits)}, lint OK: {lint_passes}",
            details={"python_edits": len(python_edits), "lint_passed": lint_passes},
            warnings=[],
        )
```

### 6.4 Extension Point Summary

| Extension Point | Mechanism |
|-----------------|-----------|
| New evaluator | Subclass `Evaluator`, register via entry point or config |
| New benchmark scenario | Add `benchmarks/<name>/scenario.yaml` |
| New metric | Add to `EvalResult.details`; dashboard auto-renders |
| New assertion type | Add to `assertions` list in scenario YAML |
| Custom reporter | Implement `Reporter` protocol (Slack, Datadog, etc.) |
| Trajectory filter | Subclass `TrajectoryFilter` |

---

## 7. Implementation Phases

| Phase | Scope | Effort |
|-------|-------|--------|
| **Phase 1** | Trajectory capture plugin, storage table, Evaluator base class, 3 evaluators (ToolAccuracy, TaskCompletion, Efficiency), ScenarioRunner, `make eval-scenario`, `tests/eval/` | 2-3 sessions |
| **Phase 2** | 6 benchmark scenarios, RegressionDetector with FAIL_TO_PASS/PASS_TO_FAIL, `make eval-diff/baseline/regression-check`, dashboard TUI panel | 3-4 sessions |
| **Phase 3** | DSPy-style prompt optimizer driven by eval scores, A/B runner, prompt changelog + auto-rollback | 4-5 sessions (speculative) |

---

## 8. Open Questions

1. **Trajectory privacy**: Tool args may contain file contents. Sanitize by default; `trajectory_detail=full` for debug sessions.
2. **Benchmark determinism**: Agent randomness means single-run scores are noisy. Run each benchmark 3 times, take median.
3. **Cost**: Each benchmark run consumes API tokens. Run on push to development, not on every commit.
4. **Evaluator versioning**: Each evaluator version is a separate metric to avoid false regressions across evaluator updates.
5. **CI gating**: Eval regressions are advisory, not blocking. The release pipeline is not gated on eval scores.

---

## Appendix: Related Work

- **SWE-bench** (Princeton, 2024): FAIL_TO_PASS/PASS_TO_PASS classification model.
- **Amazon Strands Agents Evals SDK** (2025): Pluggable evaluator pipelines with trajectory capture.
- **DSPy** (Stanford, 2024): Score-driven prompt optimization — Phase 3 follows this pattern.
- **OpenAI Evals** (2023): Pluggable evaluator architecture for LM tasks.
