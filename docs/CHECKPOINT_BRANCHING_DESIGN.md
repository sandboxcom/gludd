# AG.8: Checkpoint Branching — A/B Execution Paths from LangGraph Checkpoints

**Status:** Draft
**Target:** gludd v0.2.0
**Depends on:** Existing `TickCheckpointer` (`execution/graph_checkpointer.py`),
`DispatchCheckpoint` (`agents/dispatch_checkpoint.py`), `HumanGate` (`execution/human_gate.py`)


## 1. Problem

gludd's LangGraph execution today is **linear**: each graph invocation follows one
path from start to finish, producing one outcome. When a strategy fails (a code
generation produces broken output, a bug fix doesn't pass tests, a prompt variant
performs poorly), the only recourse is to start over — re-running the entire graph
from scratch with different inputs. There is no way to:

- **Fork** execution from a meaningful intermediate checkpoint (after the
  "understand the problem" step but before the "generate solution" step, for
  instance) and try an alternative strategy.
- **Compare** multiple branches side-by-side against the *same input state* and
  pick the best result by measurable criteria.
- **Roll back** to a prior checkpoint when a downstream failure reveals a
  poor earlier choice, without re-executing the entire prefix.

LangGraph's checkpoint system natively stores the full execution history of every
graph invocation. Its `Command(resume=...)` and `go_to` primitives already support
resuming from a checkpoint with modified state. This is structurally the same as
branching — gludd currently does not exploit it. AG.8 adds a first-class branching
layer on top of LangGraph's checkpoint infrastructure.

### Why not just run two separate graphs?

Two separate graph runs from the same initial state:
- Re-execute the common prefix (wasted compute and model tokens).
- Cannot share the same LangGraph thread/checkpoint lineage, so there is no
  structural record that "branch A and branch B both descend from checkpoint C3."
- Cannot be compared within the same execution trace or surfaced through the
  existing observable-facts pipeline (`/api/facts`).


## 2. Design

### 2.1 Core Primitives

#### `checkpoint_branch(checkpoint_id, strategy_fn) → BranchHandle`

Fork execution from a named checkpoint with an alternative strategy.

```python
class BranchHandle(TypedDict):
    branch_id: str           # UUID, unique per fork
    parent_checkpoint_id: str
    strategy_name: str       # human-readable label (e.g. "rust-implementation")
    thread_id: str           # LangGraph thread (shared parent, new child)
    config: dict[str, Any]   # LangGraph config for this branch
    started_at: float
```

Implementation sketch:

```python
def checkpoint_branch(
    checkpointer: TickCheckpointer,
    parent_tick_id: str,
    parent_checkpoint_id: str,
    strategy_fn: StrategyFn,
    strategy_name: str = "",
) -> BranchHandle:
    # 1. Retrieve the parent checkpoint via LangGraph get_tuple
    config = _tick_config(parent_tick_id)
    tuple_snapshot = checkpointer._saver.get_tuple(config)

    # 2. Clone the state at that checkpoint
    parent_state = _extract_state(tuple_snapshot)

    # 3. Create a child thread that shares the parent lineage
    child_thread_id = f"{parent_tick_id}:branch:{uuid.uuid4().hex[:8]}"
    child_config = {"configurable": {"thread_id": child_thread_id}}

    # 4. Write the parent checkpoint as the child's starting state
    copy_checkpoint_to_thread(checkpointer._saver, config, child_config)

    # 5. Apply the strategy transformation
    branched_state = strategy_fn(parent_state.deep_copy())

    # 6. Push the branched state as a new checkpoint on the child thread
    checkpointer._saver.aupdate_state(child_config, branched_state)

    # 7. Resume execution from that checkpoint
    ...
```

#### `compare_branches(branches, selector) → BranchResult`

Given multiple completed branch results, apply a selector that picks the best
and returns the winner + metrics:

```python
class BranchResult(TypedDict):
    branch_id: str
    strategy_name: str
    outcome: Outcome          # PASS | FAIL | TIMEOUT | ERROR
    metrics: dict[str, float] # test_pass_count, coverage_pct, latency_ms, ...
    artifacts: dict[str, str] # file_path -> content hash (for the generated code)
    winner: bool

class BranchSelector(Protocol):
    def select(self, results: list[BranchResult]) -> str:
        """Return the branch_id of the winner."""
        ...
```

#### `branches` Collection (Graph State Extension)

Every graph state dict that participates in checkpoint branching carries an
optional `branches` key:

```python
# In the TypedDict / Pydantic model backing the LangGraph StateGraph:
"branches": [
    {
        "branch_id": "br-abc123",
        "parent_checkpoint_id": "ckpt-3",
        "strategy_name": "iterative-fix",
        "status": "completed",
        "outcome": "PASS",
        "metrics": {"tests_passed": 14, "coverage_pct": 87.2},
        "artifacts": {"src/fix.py": "sha256:def456"},
        "started_at": 1718390000.0,
        "completed_at": 1718390120.0,
    }
]
```

This makes the branching history **observable**: every branch, its strategy,
its outcome, and its metrics appear in the graph state, are surfaced through
`/api/facts`, and are queryable in the audit log. A branch that nobody recorded
did not happen (per the `Nothing-Dropped Guardrail` in `AGENTS.md`).

### 2.2 A/B Comparison Flow

```text
                    ┌─────────────┐
                    │  Checkpoint  │
                    │   "plan"     │
                    └──────┬──────┘
              ┌────────────┴────────────┐
              ▼                         ▼
   ┌──────────────────┐     ┌──────────────────┐
   │ Strategy A       │     │ Strategy B       │
   │ "direct fix"     │     │ "refactor first" │
   └────────┬─────────┘     └────────┬─────────┘
            ▼                        ▼
   ┌──────────────────┐     ┌──────────────────┐
   │ Run tests        │     │ Run tests        │
   │ Result: 12/14    │     │ Result: 14/14    │
   └────────┬─────────┘     └────────┬─────────┘
            │                        │
            └──────────┬─────────────┘
                       ▼
              ┌──────────────────┐
              │ BranchSelector   │
              │ picks B          │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │ Merge B's result │
              │ into main state  │
              └──────────────────┘
```

### 2.3 Branch Selection Strategies

| Selector | Rule | Use Case |
|---|---|---|
| `MaxMetric(test_pass_count)` | Highest test pass count | Code generation, bug fixing |
| `MaxMetric(coverage_pct)` | Highest coverage percentage | Bug fixing with coverage gate |
| `MinMetric(latency_ms)` | Lowest latency | Prompt optimization (cheapest win) |
| `FirstPass` | First branch that passes all tests | Code generation (fast feedback) |
| `WeightedComposite(weights)` | Weighted sum of multiple metrics | Multi-objective optimization |
| `HumanChoice` | Pause and ask human via `HumanGate` | High-stakes decisions |

### 2.4 Strategy Function Contract

A strategy function receives the graph state at a checkpoint and returns a
*modified* state that steers execution down a different path:

```python
StrategyFn = Callable[[dict[str, Any]], dict[str, Any]]
```

Example strategies:

```python
def rust_implementation(state: dict[str, Any]) -> dict[str, Any]:
    """Override model_profile to favor Rust code generation."""
    state["model_profile"] = "codegen-rust"
    state["language_hint"] = "rust"
    return state

def aggressive_fix(state: dict[str, Any]) -> dict[str, Any]:
    """Use higher temperature, broader scope for the fix attempt."""
    state["generation_params"] = {"temperature": 0.9, "max_tokens": 4096}
    return state

def prompt_variant_b(state: dict[str, Any]) -> dict[str, Any]:
    """Swap in an alternative system prompt."""
    state["system_prompt"] = VARIANTS["aggressive"]
    return state
```


## 3. Integration with Existing Infrastructure

### 3.1 LangGraph Checkpoint Layer

AG.8 builds on two existing checkpoint surfaces:

| Component | File | Role |
|---|---|---|
| `TickCheckpointer` | `execution/graph_checkpointer.py` | Wraps `InMemorySaver` / `SqliteSaver` for tick-state persistence. AG.8 adds `fork_thread(parent_config, child_config)` and `list_checkpoints(tick_id)`. |
| `DispatchCheckpoint` | `agents/dispatch_checkpoint.py` | Snapshot/integrity layer for crash recovery. AG.8 reuses its `DispatchState` as the state payload carried across branch checkpoints. |
| `HumanGate` | `execution/human_gate.py` | Uses `langgraph.interrupt()` + `Command(resume=...)`. AG.8's `HumanChoice` selector Pauses and invokes HumanGate — the same pattern, extended to branch selection. |

### 3.2 LangGraph APIs Used

| LangGraph API | AG.8 Usage |
|---|---|
| `checkpointer.get_tuple(config)` | Retrieve state at a specific checkpoint. |
| `checkpointer.put(config, checkpoint, metadata, new_versions)` | Write initial-state checkpoint for a new branch child thread. |
| `graph.astream_events(..., config=child_config)` | Resume execution from the branch checkpoint with a modified state — this is the actual "fork and run" step. |
| `graph.aupdate_state(config, values)` | *(Future)* LangGraph 0.3+ supports direct state mutation at checkpoints. AG.8 uses this to inject the strategy-modified state before resuming, avoiding the need for a separate `put` + `astream` two-step. When not available, the two-step `put` + `astream` path is the fallback. |

### 3.3 Wiring into the Event Loop

The event loop (`event_loop/loop.py`) already handles checkpoints at three
boundaries (pre-model, per-tool-iter, clear-on-persist). AG.8 adds a fourth
boundary: **post-checkpoint-branch**.

```markdown
# In event_loop/loop.py — `_dispatch_execute_job`:
if _todo.branch_config:
    checkpointer = self._checkpointer
    branches = run_branched_execution(checkpointer, _todo, state)
    winner = select_branch(branches, _todo.selector)
    state = winner.state
    _todo.outcome = winner.outcome
    # Continue with winner's state as the main execution path
```

### 3.4 Configuration Surface

```python
# In config/user_config.py — new section:
class BranchingConfig(BaseModel):
    enabled: bool = False
    max_branches: int = 3              # safety cap
    max_total_tokens_per_branch: int = 100_000
    default_selector: str = "FirstPass"
    fork_at: list[str] = []            # checkpoint names where forking is allowed
    human_choice_threshold: float = 0.6 # confidence below which HumanGate fires
    branch_timeout_seconds: int = 300   # per-branch timeout (5 min default)
```

TODO config (`schemas/todo.py`) gains optional fields:

```python
branch_config: dict[str, Any] | None  # per-todo branching override
selector: str | None                   # per-todo selector override
```


## 4. Use Cases

### 4.1 Code Generation: Two Implementations, Keep the Better One

```text
Todo: "Implement a rate limiter for the API gateway"

Checkpoint "spec-understood" → [
    Branch "token-bucket"    → generate token-bucket impl → run tests → 14/14 PASS
    Branch "sliding-window"  → generate sliding-window impl → run tests → 11/14 PASS
]

Selector: MaxMetric(test_pass_count) → "token-bucket" wins
Result: token-bucket code committed, sliding-window archived for audit.
```

### 4.2 Bug Fixing: Three Fixes, Keep the Best-Coverage One

```text
Todo: "Fix null-pointer in request parser"

Checkpoint "bug-localized" → [
    Branch "null-guard"     → add null guard → tests 13/14 → coverage 72%
    Branch "early-return"   → restructure flow → tests 14/14 → coverage 85%
    Branch "validator-first"→ validate input upstream → tests 14/14 → coverage 91%
]

Selector: MaxMetric(coverage_pct) → "validator-first" wins
Result: validator-first merged. Other two branches logged for learning.
```

### 4.3 Prompt Optimization: A/B Test Prompt Variants

```text
Todo: "Optimize the code-review prompt"

Checkpoint "review-context-built" → [
    Branch "prompt-A" (concise)  → run review → avg_issues_found=3.2, latency=1.2s
    Branch "prompt-B" (detailed) → run review → avg_issues_found=5.8, latency=1.8s
]

Selector: WeightedComposite(issues_found=0.7, latency=0.3) → "prompt-B" wins
Result: prompt-B becomes the default; prompt-A kept as a "fast mode" option.
```

### 4.4 Multi-Model Ensemble (stretch)

Branch from a checkpoint with different model profiles, run the same
generation on each model, and use the consensus reviewer
(`review/langgraph_consensus.py`) to reconcile outputs — combining checkpoint
branching with the existing multi-agent consensus engine.


## 5. Implementation Plan

### Phase 1: Fork Primitive (AG.8a)

- Add `fork_thread()` to `TickCheckpointer` — copies a checkpoint from a
  parent thread to a new child thread.
- Add `list_checkpoints(tick_id)` to enumerate all checkpoints in a thread
  (already partially implemented in `TickCheckpointer.list()`).
- Unit test: fork + verify child thread has parent's state.
- File: `execution/graph_checkpointer.py` (extend existing class).

### Phase 2: Branch Execution (AG.8b)

- `CheckpointBranchExecutor` class in `execution/checkpoint_branch.py`:
  `branch()`, `run_branch()`, `await_branches()`.
- Integrate with `DispatchCheckpoint` for crash-safe branch state.
- Unit tests: single branch, multi-branch, timeout per branch, partial failure.
- File: `execution/checkpoint_branch.py` (new).

### Phase 3: Branch Selector (AG.8c)

- `BranchSelector` hierarchy: `MaxMetricSelector`, `FirstPassSelector`,
  `WeightedCompositeSelector`, `HumanChoiceSelector`.
- `compare_branches()` function.
- Unit tests: each selector type, tie-breaking, empty result set.
- File: `execution/branch_selector.py` (new).

### Phase 4: Event Loop Wiring (AG.8d)

- Wire `CheckpointBranchExecutor` into `_dispatch_execute_job` in
  `event_loop/loop.py` under a `branch_config` feature flag.
- Emit `BranchStarted` / `BranchCompleted` / `BranchSelected` events
  through the existing `EventBus`.
- Add `/api/facts?kind=branch` query support.
- Integration test: end-to-end branch execution through the daemon.

### Phase 5: Observability (AG.8e)

- `branches` key in graph state (described in §2.1).
- Daemon endpoint `GET /admin/execution/branches?todo_id=<id>` returns
  branch history.
- `make check-branch-orphans` — finds completed branches whose results
  were never selected or merged (Nothing-Dropped guardrail extension).


## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Branch explosion (max_branches=3 → 3^N with nested forks) | Hard-cap `max_branches` per level; total-depth limit of 2. Nested forks are disabled by default and require explicit opt-in with `allow_nested: true`. |
| Token cost multiplication (N branches × M tokens each) | Per-branch token budget via `max_total_tokens_per_branch`. Budget exceeded → branch killed + recorded as TIMEOUT. |
| Non-deterministic branch outcomes (same strategy, different model call) | Branches are for *strategy* comparison, not model non-determinism testing. If non-determinism matters, use the consensus reviewer instead. |
| State corruption from concurrent branch writes | Branches run on separate LangGraph threads; each has its own checkpoint lineage. No shared mutable state. |
| Orphan branches (completed but never selected) | `make check-branch-orphans` + automated pruning after 24h. |
| Deadlock if all branches timeout | `FirstPass` selector falls back to "first branch to produce any output" even if it didn't fully pass. System never hangs waiting for branches. |


## 7. Related Documents

- `docs/ORCHESTRATION.md` — subagent dispatch model; branch execution uses the same
  worktree isolation pattern for file-editing branches.
- `docs/MULTITASKING_POLICY.md` — branch execution counts toward the agent floor and
  is subject to the same disk-discipline constraints.
- `docs/LANGCHAIN_LANGGRAPH_ANSIBLE_FEATURE_PLAN_2026-06-25.md` — the broader
  LangGraph feature plan this extends.
- `src/general_ludd/execution/graph_checkpointer.py` — TickCheckpointer (existing).
- `src/general_ludd/agents/dispatch_checkpoint.py` — DispatchCheckpoint (existing).
- `src/general_ludd/execution/human_gate.py` — HumanGate interrupt/resume (existing).
- `src/general_ludd/execution/map_reduce_executor.py` — AG.11 map-reduce (sibling).
- `src/general_ludd/review/langgraph_consensus.py` — multi-agent consensus (used in §4.4).
