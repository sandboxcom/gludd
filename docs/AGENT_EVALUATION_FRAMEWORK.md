# AG.1 — Agent Evaluation Framework

**Design Document**
**Author:** gludd self-improvement
**Date:** 2026-07-12
**Status:** Draft (Phase 1 planned)

---

## 1. Problem

gludd currently has no way to measure agent quality. No benchmarks, no trajectory
evaluation, no regression detection. Agents can degrade silently across plugin
changes, prompt edits, and model upgrades. We have never measured:

- Whether enforcement plugins actually improve dispatch behavior
- Whether a model switch (sonnet → deepseek) improved or degraded outcomes
- Whether a prompt edit to AGENTS.md made agents more or less efficient
- Whether a new guardrail plugin introduced side-effects that reduce task
  completion rates

The current verification surface (52 hook-runtime tests, lint, typecheck) only
verifies that the infrastructure boots — it does **not** verify that the
infrastructure produces better agent behavior.

**Without evaluation, every change to the agent system is a blind push.**

---

## 2. Design

Drawing from the Amazon Strands Agents 25-evaluator Evals SDK pattern, the
evaluation framework has four components:

### 2.1 Trajectory Capture

Record every tool call, model response, and decision point during a session as a
structured, searchable trajectory.

```
┌─────────────────────────────────────────────────────────┐
│                   Session Trajectory                      │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│ turn_id  │ tool     │ request  │ response │ metadata    │
├──────────┼──────────┼──────────┼──────────┼─────────────┤
│ 1        │ read     │ TASKS.md │ {...}    │ {latency}   │
│ 2        │ task     │ prompt   │ {...}    │ {subagent}  │
│ 3        │ bash     │ make ... │ {...}    │ {exit_code} │
│ ...      │ ...      │ ...      │ ...      │ ...         │
└──────────┴──────────┴──────────┴──────────┴─────────────┘
```

**Capture points** (implemented as opencode hooks):

| Hook | What it records |
|------|----------------|
| `tool.execute.after` | Tool name, args (sanitized), result summary, latency, exit code |
| `skill.execute.after` | Skill name, injected context size |
| `session.idle` | Session-end snapshot: turn count, total tokens, open tasks |
| `text.complete` | Outgoing text length, classification (status/done/dispatch/question) |

Each captured event is assigned a `trajectory_id` (UUID) and a `session_id` for
grouping. Raw tool arguments that contain secret-like patterns (API keys, tokens)
are redacted before storage.

### 2.2 Evaluator Pipeline

Plug-in evaluators that score trajectory quality. Each evaluator implements:

```python
class Evaluator:
    name: str
    version: int
    async def evaluate(session: Trajectory) -> EvalResult
```

Evaluators are run at session boundaries (`session.idle`) and on-demand
(`make eval-session SESSION_ID=<id>`). Scores are stored as structured JSON
alongside the trajectory.

### 2.3 Benchmark Harness

Run standardized task suites, measure success rate. Each benchmark is a
self-contained scenario:

```
benchmarks/
  dispatch-floor/       # Does the agent maintain ≥7 subagents?
  commit-gate/          # Does the agent commit with evidence?
  task-ledger/          # Does the agent update TASKS.md?
  hot-file-conflict/    # Does the agent serialize hot-file edits?
  anti-stop/            # Does the agent continue working past a gap?
```

A benchmark run produces:
- `task_success_rate` (did the agent complete the assigned task?)
- `step_count` (how many tool calls?)
- `token_count` (how many tokens consumed?)
- `evaluator_scores` (per-evaluator breakdown)

### 2.4 Regression Detection

Compare current agent against baseline on each change. Every push to `development`
or `master` triggers a benchmark run if `benchmarks/` or agent-config files
(`AGENTS.md`, `opencode.json`, `.opencode/plugin/*.ts`) changed.

```
Baseline:    commit abc123 → task_success=0.92, steps=12.4, tokens=8.2k
Current:     commit def456 → task_success=0.87, steps=15.1, tokens=11.3k

REGRESSION: task_success dropped 5.4 percentage points.
            Steps increased 21.8%. Tokens increased 37.8%.
```

Regression thresholds are configurable per metric. A regression report is
a `HumanTodo` with `category=agent_regression` and `priority=high`.

---

## 3. Evaluator Types

### 3.1 Tool-Use Accuracy

Did the agent use the right tool for each turn?

- Dispatch wave size ≥ 2 when ≥2 pending items (batch-wider rule)
- No prohibited metacharacters in bash calls
- No bare commands (only `make <target>`)
- File reads before edits (no edit-without-read)

**Scoring:** violations per session / total tool calls. Target: 0 violations.

### 3.2 Task Completion

Did the task finish successfully?

- Subagent result contains expected deliverable
- TASKS.md entry updated to `[x]` with evidence
- Gate green on exit (if code was written)
- Commit message references TASKS.md ID

**Scoring:** completed tasks / total dispatched tasks. Target: ≥0.90.

### 3.3 Efficiency

How many steps/tokens to complete a task?

- Turn count (tool calls) per completed task
- Token count per completed task
- Dispatch-to-result latency (wall clock)
- Subagent utilization ratio (busy-time / total-time)

**Scoring:** absolute counts + ratio vs. historical baseline. Lower is better.

### 3.4 Safety

Any blocked or unsafe actions attempted?

- Lint-suppression comment attempts (blocked by enforce-no-suppressions)
- Force-push / bypass attempts
- Commit without gate attempts
- Secret-like patterns in tool arguments
- Dispatch on dirty tree attempts

**Scoring:** blocked violations / session. Target: 0 unblocked violations.
Blocked violations are logged but not penalized (the guardrail worked).

### 3.5 Code Quality

Does generated code pass tests/lint?

- Test pass rate on generated code
- Ruff lint errors on generated code
- Mypy type errors on generated code
- Test collection errors on generated code
- Coverage delta on generated code

**Scoring:** gate-pass rate on generated code. Target: 1.0 (all code
passes gate before commit).

---

## 4. Integration Points

### 4.1 Tool-Execute Hook (`tool.execute.after`)

```typescript
// .opencode/plugin/enforce-trajectory.ts (NEW)
const hook: ToolExecuteHook = {
  name: "enforce-trajectory",
  matches: "*",
  hooks: {
    "tool.execute.after": (ctx) => {
      const record = {
        trajectory_id: ctx.session.trajectory_id,
        turn_number: ctx.turn.count,
        tool: ctx.tool.name,
        args_sanitized: sanitizeToolArgs(ctx.tool.args),
        result_summary: summarizeResult(ctx.result),
        latency_ms: ctx.timing.duration_ms,
        exit_code: ctx.result.exit_code,
        timestamp: Date.now(),
      };
      trajectoryBuffer.push(record);
    },
  },
};
```

### 4.2 Session-Idle Hook (`session.idle`)

```typescript
// Score the session when it goes idle
hooks: {
  "session.idle": async (ctx) => {
    const trajectory = await flushTrajectoryBuffer(ctx.session.id);
    const scores = await runAllEvaluators(trajectory);
    await storeTrajectory(trajectory, scores);
  },
}
```

### 4.3 Database Storage

Trajectories and scores are stored in the gludd database for historical comparison.

```
Table: agent_trajectories
  - trajectory_id (UUID, PK)
  - session_id (TEXT)
  - started_at (TIMESTAMP)
  - ended_at (TIMESTAMP)
  - turn_count (INT)
  - total_tokens_estimate (BIGINT)
  - events (JSONB)              # compressed tool-call records
  - scores (JSONB)              # per-evaluator scores
  - metadata (JSONB)            # model, provider, agent config hash
```

Alembic migration adds this table alongside existing agent tables. Trajectory
storage is configurable (on/off, sampling rate for production).

### 4.4 Dashboard

A TUI view (`Agent Quality` in the dashboard) shows:

```
┌────────────────────────────────────────────────────────────┐
│  Agent Quality (last 24h)              [refresh every 30s] │
├──────────────────┬─────────┬──────────┬──────────┬─────────┤
│ Metric           │ Current │ Baseline │ Delta    │ Status  │
├──────────────────┼─────────┼──────────┼──────────┼─────────┤
│ Task Success     │ 0.91    │ 0.92     │ -0.01    │ OK      │
│ Steps/Task       │ 13.2    │ 12.4     │ +0.8     │ WARN    │
│ Tokens/Task      │ 9.1k    │ 8.2k     │ +0.9k    │ WARN    │
│ Tool Accuracy    │ 0.98    │ 0.97     │ +0.01    │ OK      │
│ Safety Viol.     │ 0       │ 0        │ 0        │ OK      │
│ Code Quality     │ 1.00    │ 1.00     │ 0        │ OK      │
├──────────────────┴─────────┴──────────┴──────────┴─────────┤
│ Recent regressions: none                                   │
│ Trajectories stored: 847 (last 7 days)                     │
└────────────────────────────────────────────────────────────┘
```

CLI equivalent: `make agent-quality` prints the same table to stdout.

---

## 5. Implementation Phases

### Phase 1: Trajectory Capture + Basic Scoring (CRITICAL)

**Goal:** Record what happens, measure basic quality.

| Deliverable | Description |
|-------------|-------------|
| `enforce-trajectory.ts` | Plugin that records every tool call to a buffer |
| `TrajectoryStore` (Python) | Flushes buffer to DB at session boundaries |
| `trajectories` table | Alembic migration |
| `ToolUseAccuracy` evaluator | Scores tool-use correctness |
| `TaskCompletion` evaluator | Scores task completion rate |
| `make eval-session SESSION_ID=<id>` | CLI to score a single session |
| `make agent-quality` | CLI to show quality metrics |

**Effort:** ~2-3 sessions. This phase is critical — without trajectory capture,
no other evaluation is possible.

### Phase 2: Benchmark Suite + Regression Detection

**Goal:** Run standardized tasks, detect regressions automatically.

| Deliverable | Description |
|-------------|-------------|
| `benchmarks/` directory | Standardized task suites (6-8 scenarios) |
| `BenchmarkRunner` | Runs a benchmark suite, collects scores |
| `RegressionDetector` | Compares baseline vs. current, flags regressions |
| `HumanTodo(category=agent_regression)` | Alerts when agent degrades |
| `make benchmark` | CLI to run the full benchmark suite |
| Dashboard TUI view | Agent Quality panel |

**Effort:** ~3-4 sessions. Requires Phase 1 trajectories for baseline comparison.

### Phase 3: Auto-Optimization (DSPy-Style Prompt Tuning)

**Goal:** Use evaluation scores to automatically improve agent prompts.

| Deliverable | Description |
|-------------|-------------|
| `PromptOptimizer` | DSPy-style optimizer that varies prompt wording |
| A/B runner | Runs agent with prompt variant A vs. B on benchmark suite |
| Score-driven selection | Picks the variant with highest scores |
| `make optimize-prompts` | CLI to trigger optimization |
| Prompt changelog + rollback | Versioned prompt history with auto-rollback on regression |

**Effort:** ~4-5 sessions. Requires Phase 2 benchmarks + regression detection.
This phase is speculative — DSPy-style optimization requires a reliable eval
signal, and the benchmark suite must be stable before optimization can work.

---

## 6. Metrics

### Primary Metrics (every session)

| Metric | Definition | Target | Direction |
|--------|-----------|--------|-----------|
| **Task Success Rate** | completed / dispatched | ≥0.90 | ↑ |
| **Steps per Task** | avg tool calls per completed task | ≤15 | ↓ |
| **Tokens per Task** | avg tokens per completed task | ≤12k | ↓ |
| **Tool Accuracy** | 1.0 − (violations / total calls) | ≥0.95 | ↑ |
| **Safety Violation Rate** | unblocked violations / session | 0 | ↓ |
| **Code Quality** | gate-pass rate on generated code | 1.0 | ↑ |

### Secondary Metrics (aggregate)

| Metric | Definition | Use |
|--------|-----------|-----|
| **Dispatch Wave Size** | avg tasks per wave | Monitor floor compliance |
| **Subagent Utilization** | busy-time / total-session-time | Detect idle agents |
| **Hot-File Conflict Rate** | conflicts / sessions touching hot files | Detect serialization gaps |
| **Premature Stop Rate** | sessions ending with open work | Monitor anti-stop effectiveness |
| **Enforcement Block Rate** | tool denials / tool calls | Monitor guardrail sensitivity |

### Storage

Metrics are stored per-session and aggregated into hourly, daily, and weekly
rollups for dashboard display and trend detection.

---

## Appendix A: Open Questions

1. **Trajectory privacy:** Tool arguments may contain file contents. How much
   do we sanitize vs. preserve for debugging?

2. **Benchmark determinism:** Agent performance varies with model randomness.
   How many benchmark runs per commit to get a stable signal? (Recommendation:
   3 runs, take median.)

3. **Cost of evaluation:** Running benchmark suites consumes API tokens. At
   what frequency do we run them? (Recommendation: on push to development,
   not on every commit.)

4. **Evaluator versioning:** Evaluator logic changes over time. How do we
   compare scores across evaluator versions without false regressions?
   (Recommendation: score versioning — each evaluator version is a separate
   score metric.)

5. **Human-in-the-loop:** An evaluator can measure task completion, but
   "did the agent do a good job?" is subjective. Do we need human review
   steps in the pipeline? (Recommendation: not in Phase 1-2; add optionally
   in Phase 3.)

## Appendix B: Related Work

- **Amazon Strands Agents Evals SDK** (2025): Pluggable evaluator pipelines
  with trajectory capture, benchmark suites, and regression detection. This
  design is directly inspired by their architecture.

- **DSPy** (Stanford, 2024): Language model programming framework with
  automatic prompt optimization. Phase 3 of this design follows the DSPy
  pattern of score-driven prompt tuning.

- **SWE-bench** (Princeton, 2024): Benchmark for software engineering agents
  using real GitHub issues. The benchmark harness design draws from
  SWE-bench's containerized evaluation model.

- **OpenAI Evals** (2023): Evaluation framework for language model tasks.
  The evaluator pipeline pattern (pluggable, scored, aggregated) follows
  their architecture.
