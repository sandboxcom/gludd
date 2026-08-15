# PipelineController design (#77)

Status: DESIGN ONLY — implementation-ready spec for a follow-up build agent. No
code/tests are written here. Every primitive referenced below is an existing,
committed module; file/line references are to the tree this doc was written
against.

## 1. Problem & framing

gludd's daemon already ticks an `EventLoop` over a project's todo backlog and
dispatches role work, but the **3-lane multitask + merge pipeline** (keep N
agents busy → continuously integrate their worktrees → debounced gate on a
coherent snapshot) is not assembled as a first-class component. The pieces all
exist independently:

| Lane concern              | Existing primitive (grounded)                                                                 |
|---------------------------|-----------------------------------------------------------------------------------------------|
| keep-N-busy / backfill    | `SaturationController` — `src/general_ludd/controllers/saturation.py` (#42)                    |
| concurrency-safe batching | `Scheduler.plan()` / `WorkItem` — `src/general_ludd/scheduling/scheduler.py` (#23)            |
| per-agent concurrency     | `AgentDispatcher` (per-agent `asyncio.Semaphore`) — `src/general_ludd/agents/dispatcher.py`   |
| git serialization         | `git_repo_lock` / `async_git_repo_lock` — `src/general_ludd/git_automation/locking.py` (#63)  |
| anti-clobber merge        | `safe_merge` / `safe_merge_file` — `src/general_ludd/integration/safe_merge.py` (#70)         |
| worktree create/list/rm   | `GitAutomation` — `src/general_ludd/git_automation/repo.py`                                    |
| worktree reclamation      | `WorktreeScanner._reclaim_worktree_dir` — `src/general_ludd/worktree/core.py` (#62 disk)       |
| the gate                  | `make gate` / `run_preflight` — `src/general_ludd/quality/preflight.py` (used in daemon today) |
| tick host                 | `EventLoop.run_forever` + FastAPI lifespan — `src/general_ludd/event_loop/loop.py`, `daemon.py`|

The `PipelineController` ports the external multitask+merge pipeline INTO gludd
so the daemon runs the same `dispatch → integrate → gate` pipeline over **its
own agent worktrees**.

### Why a separate controller (not more EventLoop phases)

`EventLoop.tick()` is a **synchronous phase sweep** (`PHASE_ORDER`,
`loop.py:38`): every phase runs to completion in order, once per tick. That is
the wrong shape for the pipeline — the three lanes must run **continuously and
independently**: integration must keep folding in completed worktrees while
dispatch keeps launching new ones, and the gate must debounce across many
completions rather than fire once per tick. So `PipelineController` runs the
lanes as **three long-lived `asyncio.Task`s**, started from the same lifespan
as the EventLoop, sharing the loop's executor and event bus but not its
serial phase cadence.

The EventLoop remains the owner of the **todo/decision lifecycle** (claim,
review, reconcile, push). PipelineController is the owner of the **worktree
lifecycle** (create → produce → integrate → gate → reclaim). They are wired
side-by-side in lifespan (Section 3); they do not call into each other's
internals.

---

## 2. The three lanes as concrete components

All three lanes operate over a shared in-process state model (Section 2.4) and
emit heartbeats on every loop turn (Section 4.5). Each lane is an
`async def run(self)` coroutine that loops on a short interval and returns only
on `stop()`.

### 2.1 DispatchLane — keep N role-agents busy on disjoint work

Responsibility: hold the number of *running* role-agent worktrees at `target`
(never below `floor` while backlog and capacity allow), backfill on completion,
never double-assign the same work.

Algorithm, per turn:

1. **Reconcile running count** from authoritative state — `len(state.running)`
   (the set of `WorktreeJob`s in status `RUNNING`). This is passed to the
   saturation controller as the `running` arg; the lane never keeps a drifting
   counter. This mirrors `SaturationController`'s core contract ("Reconcile,
   never drift", `saturation.py:9`).
2. **Build the backlog** as `list[WorkItem]` from claimable todos (the same
   `WorkItem` frozen dataclass the Scheduler consumes — `scheduler.py:31`). Each
   item's `resources` is `frozenset({f"todo:{todo_id}"})` so two agents never
   touch the same work; greenfield/new-file items may set `is_greenfield=True`
   so they never force-serialize (`scheduler.py:44-49`). This is exactly the
   resource convention `EventLoop._dispatch_jobs_via_scheduler` already builds
   (`loop.py:718-731`), so the backlog is interoperable.
3. **Compute backfill:**
   `plan = SaturationController().plan_backfill(target, running, backlog, per_source_caps)`
   (`saturation.py:102`). `per_source_caps` carries model/GPU headroom when
   present (built from `UtilizationTracker.list_endpoints()` →
   `SourceCapacity(source_id, capacity=max_concurrent, running=current_load)`,
   `infra/utilization.py:21-44`). Backfill returns at most `target - running`
   items, drained in priority order — zero idle when backlog is non-empty,
   nothing pulled when saturated.
4. **Order within the backfill batch** via `Scheduler().plan(plan_items)`
   (`scheduler.py:89`) so anything sharing an exclusive resource serializes into
   later batches. The lane dispatches **only the first batch's** items this turn
   (later batches re-enter on subsequent turns as slots free).
5. **Floor guarantee:** if `running < floor` and the backfill yielded fewer than
   `floor - running` items because of a *transient* refusal (back-pressure,
   Section 2.5), the lane logs a `floor_underrun` heartbeat and retries next
   turn rather than silently sitting below floor. The floor is never violated by
   the lane's own choice — only by genuine backlog exhaustion or hard
   back-pressure, both of which are surfaced.
6. **Launch each chosen item as a worktree job:**
   - `branch = GitAutomation.generate_branch_name(todo_id, slug)`
     (`repo.py:502`).
   - `GitAutomation(project_repo).create_worktree(project_repo, branch, wt_path)`
     (`repo.py:285`) — already hardened (leading-dash + `..`-escape rejection)
     and serialized through `git_repo_lock` via `_run_git`'s choke point. Note
     `create_worktree` uses raw `subprocess.run` (not `_run_git`,
     `repo.py:297`); the lane MUST wrap that call in `async_git_repo_lock(project_repo)`
     itself so worktree creation serializes with concurrent integration/commits
     on the same repo (locking.py docstring explicitly lists this obligation,
     `locking.py:31-34`).
   - Dispatch the agent into the worktree via `AgentDispatcher.dispatch_one`
     (`dispatcher.py:66`). The dispatcher's per-agent semaphore is a *second*
     concurrency bound below `target` (a role with `max_concurrent=1` can only
     run one at a time even if target has headroom). The lane respects both:
     `target` bounds total worktrees, the semaphore bounds per-role.
   - Record a `WorktreeJob(status=RUNNING, …)` in `state.running`.

DispatchLane never blocks on agent completion — it fires the dispatch coroutine
and tracks it; completion is observed in step 1 of a later turn (the agent
coroutine flips the job to `COMPLETED`/`FAILED` in a done-callback).

### 2.2 IntegrateLane — continuously merge completed worktrees, never clobber

Responsibility: fold each `COMPLETED` worktree back into the project repo
without reverting concurrent base changes, under the per-repo lock, then reclaim
the worktree's disk.

Per turn, for each `WorktreeJob` in `state.completed_pending_integration`:

1. **Acquire the repo lock for the whole integration of one worktree:**
   `cm = await async_git_repo_lock(project_repo); with cm:` (`locking.py:283`).
   This holds off the event loop while waiting (run_in_executor) so a contended
   repo never stalls the tick, and serializes against DispatchLane's
   `create_worktree`, the EventLoop's commit/push, and any other git caller —
   exactly the cross-cutting race `git_repo_lock` exists to close
   (`locking.py:5-11`).
2. **Three-way, anti-clobber merge — the wt-sync clobber-guard analogue.** For
   each file the worktree changed, resolve the common ancestor (the base commit
   the worktree branched from), and call
   `safe_merge(base_text, ours=base_current, theirs=worktree_text)`
   (`safe_merge.py:84`). Semantics we rely on:
   - only the worktree changed → take it (`source="theirs"`);
   - base changed, worktree didn't → keep base (no clobber);
   - both changed identically → convergent, take it;
   - both changed differently but disjoint regions → CLEAN merged result
     containing BOTH edits (`source="merged"`);
   - both changed the same region → `conflict=True` with git-style markers.
   A whole-file copy (the data-loss bug `safe_merge` was written to prevent,
   `safe_merge.py:3-9`) is NEVER used. For the file-level path use
   `safe_merge_file(base, ours, theirs, dest)` which **refuses to write `dest`
   on conflict** (`safe_merge.py:280-300`) — a conflict can never be silently
   materialized as a resolved file.
   - Implementation note: prefer letting git do the index-level merge first
     (`GitAutomation.merge_branch(repo, source=branch, target=base, strategy="no-ff")`,
     `repo.py:397`) and only fall back to `safe_merge` per-file for the files git
     flags as conflicted, so we get git's rename/binary handling for the easy
     majority and the anti-clobber primitive for the contested files. Either way
     a conflict ends in the escalation path (Section 4.2), never a blind
     overwrite.
3. **On clean integration:** mark the job `INTEGRATED`, then **reclaim the
   worktree** so disk does not leak (#62). Use
   `GitAutomation.remove_worktree(project_repo, wt_path)` (`repo.py:346`) inside
   the same lock; if that fails, fall back to the hardened
   `WorktreeScanner._reclaim_worktree_dir`-style `git worktree remove --force` +
   `git worktree prune` sequence (`worktree/core.py:429-471`). Reclamation is
   best-effort but always logged — a leaked worktree is a heartbeat warning, not
   a silent loss (disk-discipline invariant: ~15 leaked venvs filled the disk in
   prod).
4. **On conflict:** do NOT integrate. Move the job to `state.conflicts` and
   escalate (Section 4.2). The worktree is *kept* (not reclaimed) so the
   conflict can be inspected/resolved.
5. **Signal the gate:** every successful integration bumps
   `state.integration_epoch` and pokes `GateLane` (Section 2.3 debounce). The
   gate runs on the *resulting* coherent snapshot, not mid-merge.

IntegrateLane processes worktrees **one at a time** (the repo lock makes
parallel integration into one repo pointless and unsafe); multiple *projects*
each get their own lock keyed by realpath (`locking.py:86-96`), so cross-project
integration is naturally concurrent.

### 2.3 GateLane — debounced single gate on a coherent snapshot, commit-on-green

Responsibility: run exactly one gate/validation at a time over a *settled*
snapshot, never two at once, and commit when green — while lanes 1 & 2 keep
producing.

- **Debounce / quiet-window:** the gate does not fire on every integration. It
  fires when `state.integration_epoch` has advanced AND no new integration has
  landed for `gate_debounce_seconds` (a quiet window). This batches a burst of
  N integrations into ONE gate run on the coherent post-burst snapshot. Concrete
  rule each turn:
  ```text
  if epoch > last_gated_epoch and (now - last_integration_at) >= gate_debounce_seconds
     and not gate_in_flight:
        run_gate()
  ```
- **Single-flight:** a `gate_in_flight` flag (and an `asyncio.Lock`) guarantees
  only one gate runs at a time. This is the hard invariant from the gate
  concurrency-hygiene memory: never run two gates/pytest at once (collisions on
  pytest tmp rotation manifest as phantom "208 errors / 0 failures"). The lane
  also refuses to start a gate while one is in flight even across a daemon that
  has multiple projects — gate runs are globally serialized by a single
  module-level lock (the gate shells out to the whole-repo `make gate`).
- **Snapshot coherence:** the gate captures `epoch = state.integration_epoch` at
  start; if integrations land *during* the run, `last_gated_epoch` is set to the
  captured epoch (not the current one) so a follow-up gate is scheduled for the
  newer work. The gate result is therefore always attributable to a specific,
  named integration epoch.
- **Execution:** run the gate off the event loop —
  `await asyncio.to_thread(run_preflight)` (the daemon already does this in
  `_init_preflight`, `daemon.py:697-708`) or shell `make gate` via
  `asyncio.create_subprocess_exec`. Blocking the loop is forbidden
  (`loop.py:930-938` sets the precedent: `run_playbook` is wrapped in
  `to_thread`).
- **Commit-on-green:** on a green gate, commit the integrated snapshot to the
  project's integration branch via `GitAutomation.commit(msg)` + `push`
  (`repo.py:192,206`) **inside `git_repo_lock`** (commit() already locks via
  `_run_git`; the surrounding integrate/gate sequence must not have released the
  lock between merge and commit, or a concurrent change could slip in — see
  Section 4.3). On a red gate, the lane does NOT commit; it records the failing
  result, increments a flake/quiet-window counter (Section 4.4), and surfaces it
  on the heartbeat. Lanes 1 & 2 keep running throughout — a red gate pauses
  *commits*, not *production*.

### 2.4 Shared state model

```python
class JobStatus(Enum):
    RUNNING; COMPLETED; FAILED; INTEGRATING; INTEGRATED; CONFLICT; RECLAIMED

@dataclass
class WorktreeJob:
    job_id: str
    todo_id: str
    project_repo: str          # realpath of the project repo (lock key)
    branch: str
    worktree_path: str
    base_commit: str           # ancestor for the 3-way merge
    status: JobStatus
    agent_name: str
    started_at: float
    finished_at: float | None = None
    error: str | None = None
    attempts: int = 0          # redispatch counter (Section 4.1)

@dataclass
class PipelineState:
    running: dict[str, WorktreeJob]                 # status RUNNING
    completed_pending_integration: deque[WorktreeJob]
    conflicts: dict[str, WorktreeJob]
    integration_epoch: int = 0
    last_integration_at: float = 0.0
    last_gated_epoch: int = 0
    gate_in_flight: bool = False
    last_gate_result: dict | None = None
    floor: int; target: int; max_worktrees: int
```

State is mutated only by the lanes, each from the single daemon event loop
thread (asyncio), so no cross-thread lock is needed for the dict/deque; the only
true concurrency is the gate subprocess and git subprocesses, which are bounded
by `git_repo_lock` and the gate single-flight lock respectively. (Counts handed
to the SaturationController are read snapshots — consistent with its
"authoritative running count passed in each tick" contract, `saturation.py:43-49`.)

### 2.5 Back-pressure (disk / concurrency limits)

Back-pressure is evaluated by DispatchLane *before* launching; IntegrateLane is
unaffected (draining always helps). Inputs, all already available in-process:

- **Worktree cap:** if `len(state.running) + len(pending) >= max_worktrees`, pull
  zero new work this turn (a hard ceiling independent of `target`, so a stuck
  IntegrateLane can't let DispatchLane open unbounded worktrees).
- **Disk:** reuse the disk signal the EventLoop already computes —
  `psutil.disk_usage("/")` → `disk_free_percent` (`loop.py:599-600`). Below a
  configurable `min_disk_free_pct` (default e.g. 15% — the disk-discipline
  memory records ~320MB/worktree and a 100% ENOSPC deadlock), DispatchLane pulls
  zero new work and emits a `backpressure:disk` heartbeat; IntegrateLane is
  *prioritized* because integrating + reclaiming frees disk.
- **Per-source caps:** already folded into `plan_backfill_by_source` via
  `SourceCapacity.headroom` (`saturation.py:159-180`) so a saturated model/GPU
  endpoint naturally caps backfill.
- **Spend:** dispatch executors are already wrapped by
  `make_spend_guarded_executor` (`daemon_wiring.py:195`); a deferred dispatch
  returns the `"deferred:spend_limit_exceeded"` sentinel, which the lane treats
  as "slot not actually filled" and retries next turn (no false RUNNING entry).

Back-pressure NEVER silently stalls: each refusal path emits a distinct
heartbeat reason so an operator can see *why* the floor is underrun.

---

## 3. PipelineController API & daemon wiring

### 3.1 Class

```python
class PipelineController:
    def __init__(
        self,
        *,
        project_repos: list[str],          # realpath repos to run the pipeline over
        agent_dispatcher: AgentDispatcher, # reused (dispatcher.py)
        git_factory: Callable[[str], GitAutomation] = GitAutomation,
        saturation: SaturationController | None = None,
        scheduler: Scheduler | None = None,
        utilization: UtilizationTracker | None = None,
        event_bus: Any | None = None,      # heartbeats / observability
        config: PipelineConfig,
        backlog_provider: Callable[[str], Awaitable[list[WorkItem]]],
        worktree_base: str,                # allowed_base for confine (core.py)
        gate_runner: Callable[[], Awaitable[dict]] | None = None,  # default: to_thread(run_preflight)
    ) -> None: ...

    async def start(self) -> None:
        # create the three asyncio.Tasks; idempotent; attaches done-callbacks
        # mirroring daemon._on_event_loop_done so a dead lane is logged loudly.

    async def stop(self) -> None:
        # set self._running = False; cancel the three tasks; await with
        # contextlib.suppress(CancelledError) (same teardown shape as the
        # EventLoop task in daemon._lifespan, daemon.py:729-734).

    def snapshot(self) -> dict:   # for /readyz, /admin and the dashboard
```

Internally it owns `DispatchLane`, `IntegrateLane`, `GateLane`, each holding a
ref to the shared `PipelineState`. `run_forever`-style loops:

```text
async def _run_lane(self, lane):
    while self._running:
        try:
            await lane.tick()
        except Exception as exc:
            logger.error("Pipeline lane %s tick raised: %s", lane.name, exc)
            self._emit_heartbeat(lane.name, status="error", error=str(exc))
        await asyncio.sleep(lane.interval)
```

This matches `EventLoop.run_forever` (`loop.py:388-398`): a per-iteration
try/except so one bad tick never kills the lane, and a loud terminal log if the
loop ever exits. The three lanes run as independent tasks, so a slow gate
(GateLane awaiting `to_thread`) never blocks DispatchLane or IntegrateLane —
that non-blocking independence is the whole point of using three tasks instead
of three EventLoop phases.

### 3.2 Config keys

A new `pipeline:` block in `general-ludd.yml`, loaded onto `UserConfig`
(`config/user_config.py:46`) as a `pipeline: dict[str, Any] = {}` field (same
pattern as `budget`, `self_improve`, `queues`). Keys (with defaults):

```yaml
pipeline:
  enabled: false           # opt-in, like self_improve.interval==0 disables (daemon.py:539-549)
  floor: 1                 # never below this many running agents while work+capacity exist
  target: 4                # keep-N-busy target (SaturationController target)
  max_worktrees: 8         # hard ceiling on concurrent worktrees (disk back-pressure)
  gate_debounce_seconds: 20
  min_disk_free_pct: 15
  dispatch_interval: 1.0
  integrate_interval: 1.0
  gate_interval: 2.0
  project_repos: []        # explicit repos; default = active projects' workspace repos
  base_branch: main        # integration target
  commit_on_green: true
  open_pr: false           # reuse PRDelivery path (loop.py:1180) when true
```

`floor`/`target`/`max_worktrees`/`gate_debounce` are the four keys the prompt
calls out; the rest are tuning knobs with safe defaults. Env override comes for
free via `UserConfig`'s `GLUDD_PIPELINE` prefix mechanism.

### 3.3 Wiring point in lifespan

Constructed in `daemon._lifespan` (`daemon.py:430`), **after** the
`AgentDispatcher` and `WorktreeMonitor` are built (`daemon.py:608-688`) and
after the `EventLoop` task is started (`daemon.py:583`). Sketch, inserted right
after `app.state._agent_dispatcher = AgentDispatcher(...)`:

```python
from general_ludd.pipeline.controller import PipelineController, PipelineConfig

pipeline_cfg = (getattr(uc, "pipeline", None) or {}) if uc else {}
if pipeline_cfg.get("enabled"):
    repos = pipeline_cfg.get("project_repos") or [
        ws.repo_path for ws in _init_project_workspaces(ext["projects"]).values()
    ]
    pipeline = PipelineController(
        project_repos=[os.path.realpath(r) for r in repos],
        agent_dispatcher=app.state._agent_dispatcher,
        utilization=ext["utilization"],
        event_bus=subsys["bus"],
        config=PipelineConfig(**pipeline_cfg),
        backlog_provider=_make_pipeline_backlog_provider(session_factory),
        worktree_base=config_dir or os.getcwd(),
        gate_runner=lambda: asyncio.to_thread(run_preflight),
    )
    await pipeline.start()
    app.state._pipeline = pipeline           # for /admin + /readyz
    logger.info("PipelineController started: %d repo(s), target=%s floor=%s",
                len(repos), pipeline_cfg.get("target"), pipeline_cfg.get("floor"))
```

Teardown in the lifespan's shutdown half (after `event_loop.stop()`,
`daemon.py:729`): `if pipeline: await pipeline.stop()`.

`_make_pipeline_backlog_provider` is a small factory (sibling of the
`make_*_handler` factories in `daemon_wiring.py`) that, given the
`session_factory`, returns `async (repo) -> list[WorkItem]` by querying
claimable todos for that project and mapping each to a `WorkItem` with
`resources=frozenset({f"todo:{id}"})` — the identical convention
`EventLoop._dispatch_jobs_via_scheduler` uses (`loop.py:718-731`). Keeping this
in `daemon_wiring.py` keeps it unit-testable without a FastAPI app (that module's
stated purpose, `daemon_wiring.py:3-6`).

### 3.4 Interaction with the existing EventLoop

The two coexist without coupling:

- **EventLoop** owns todo state transitions, return review, reconcile, and the
  existing single-project commit/push path (`loop.py:1153`). It continues to
  tick on `GLUDD_TICK_INTERVAL`.
- **PipelineController** owns worktree create→integrate→gate→reclaim. It reads
  claimable todos (read-only backlog) and, on green, commits the integrated
  result to the integration branch.
- **No double-commit:** to avoid both systems pushing the same work, the
  pipeline marks a todo (e.g. `pipeline_owned=true` / a dedicated queue) so the
  EventLoop's `_attempt_completed_push` (`loop.py:1117`) skips pipeline-owned
  todos, and vice-versa. The cleanest split for the first build: run the
  pipeline over a dedicated queue/project set so the two never contend for the
  same todo. Both push paths already go through `git_repo_lock`, so even an
  accidental overlap cannot corrupt the index — it would at worst double-attempt
  a push, which `_pushed_work` dedup (`loop.py:1126`) already guards on the
  EventLoop side.
- **Shared executor & bus:** both use the same `AgentDispatcher` instance (so
  the per-role semaphores bound *total* agent concurrency across both systems)
  and the same `EventBus` for observability.

---

## 4. Failure handling

### 4.1 Agent death / redispatch

- An agent coroutine that raises is already turned into
  `AgentTaskResult(status="failed", …)` by `AgentDispatcher.dispatch_one`
  (`dispatcher.py:92-101`) — it never escapes. The lane's done-callback flips the
  `WorktreeJob` to `FAILED` and records `error`.
- **Liveness, not wall-clock:** a `RUNNING` job is only considered dead when its
  dispatch task is `done()` with no result, or it exceeds
  `agent_timeout_seconds`. This mirrors the EventLoop's reaper invariant —
  `updated_at` alone is NOT a liveness clock; a live lease/running task must
  never be reaped (`loop.py:281-331`). The pipeline uses the live `asyncio.Task`
  as its liveness signal (strictly better than a heartbeat lease).
- **Redispatch:** on `FAILED`, increment `attempts`; if `attempts < max_retries`
  (reuse the EventLoop's `_max_retries=3`, `loop.py:164`) the job's worktree is
  reclaimed and the todo returns to the backlog for a fresh dispatch next
  DispatchLane turn. After `max_retries` the job is parked in `state.conflicts`
  (escalation bucket) with a terminal heartbeat — never an infinite redispatch
  loop.

### 4.2 Merge-conflict escalation

- A `safe_merge` result with `conflict=True` (`safe_merge.py:55-57`) or a git
  `merge_branch` returning `success=False` with conflicts (`repo.py:427-431`)
  moves the job to `state.conflicts`, leaves the worktree intact, and emits a
  `conflict` heartbeat + an `AuditEvent` (the EventLoop already records audit
  events through `AuditEventRepository`, `loop.py:1102-1113`; the pipeline uses
  the same repo).
- The integration branch is NOT advanced and the gate is NOT triggered for a
  conflicted job — only clean integrations bump the epoch (Section 2.3), so a
  conflict can never poison the gated snapshot.
- Escalation is terminal-until-human: a conflicted job stays in `state.conflicts`
  (surfaced via `/admin`) until resolved or explicitly dropped. We do NOT
  auto-resolve by picking a side — that would re-introduce the exact clobber
  `safe_merge` exists to prevent (`safe_merge.py:3-9`).

### 4.3 Lock-window correctness (integrate→commit atomicity)

The merge, the gate's commit, and the reclaim for one worktree must be reasoned
about as a unit. The merge and reclaim hold `git_repo_lock` (Section 2.2). The
gate runs *outside* the lock (it's a long subprocess; holding the repo lock
across a multi-minute gate would block all git). To keep commit-on-green
coherent: GateLane re-acquires `git_repo_lock`, re-checks that
`state.integration_epoch` still equals the epoch it gated, and only then commits
(`repo.commit`/`push` self-lock via `_run_git`). If the epoch advanced during
the gate, the commit is deferred and a fresh gate is scheduled for the newer
epoch — the stale-snapshot guard. This is the same compare-before-apply pattern
the EventLoop reconcile uses against version races (`loop.py:1084-1092`).

### 4.4 Gate-flake handling (quiet-window gating)

- Gate flakiness (esp. pytest tmp-rotation collisions, the "208 errors / 0
  failures" signature in the gate-concurrency memory) is mitigated structurally
  by **single-flight** (Section 2.3): only one gate ever runs, so collisions
  cannot happen between two pipeline gate runs. The lane MUST also coordinate
  with any out-of-band gate — i.e. acquire a process-level gate lock so it never
  races a developer's `make gate`.
- **Quiet-window:** the debounce window (`gate_debounce_seconds`) is the primary
  flake-suppressor — it lets a burst settle before gating, so the gate sees a
  stable tree, not a half-written merge.
- **Retry-on-flake, bounded:** a red gate result is classified: a result whose
  failure signature looks like a known flake (collection-error count > 0 with
  failures == 0 — the tmp-rotation tell) is retried once after a short backoff
  rather than treated as a real red; a genuine red (real failures) is recorded,
  blocks commit, and is surfaced. The retry count is bounded
  (`gate_flake_retries`, default 1) so a truly broken tree is not masked. This
  honors the no-unquantified-status-claims rule: the heartbeat carries the
  measured counts (passed/total from `run_preflight`, `daemon.py:701-706`), never
  a bare "green".

### 4.5 Observability — never a silent lane

The observability invariant ("unseen events aren't events"; AGENTS.md rule 9,
test_observability_guardrails.py) is binding here. Each lane emits a heartbeat on
**every** turn via the `EventBus`, carrying at minimum:

```json
{ "lane": "dispatch|integrate|gate",
  "turn": n, "running": k, "pending": p, "conflicts": c,
  "epoch": e, "gate_in_flight": bool,
  "status": "ok|backpressure:disk|backpressure:worktree_cap|floor_underrun|error|conflict",
  "detail": "...", "ts": ... }
```

- A backfill that pulls zero work always says *why* (`backpressure:*` /
  `floor_underrun` / `backlog_empty`) — a quiet lane sitting below floor with a
  non-empty backlog is a bug, and the heartbeat makes it visible (stall-detection
  memory: flat output is a STALL signal).
- Lane done-callbacks log loudly on unexpected exit (mirroring
  `daemon._on_event_loop_done`, `daemon.py:419-427`).
- `snapshot()` feeds `/readyz` (degrade to 503 if a lane task is `done()`
  unexpectedly, exactly as the EventLoop task gates readiness today,
  `daemon.py:994-1001`) and the dashboard provider.
- The gate result heartbeat always includes the measured passed/total counts and
  the gated epoch, so "green" is always backed by a measurement.

---

## 5. Test strategy (descriptions only — no tests written here)

Unit (fast, no daemon, no real git where avoidable):

1. **DispatchLane backfill math** — feed a stub `PipelineState` with N running
   and a backlog; assert `plan_backfill` is called with the *authoritative*
   running count and that exactly `target - running` items launch; assert
   `running >= floor` is maintained and that a zero-backfill turn emits a
   reasoned heartbeat. (Reuses the deterministic `SaturationController`, so the
   math itself is already covered; this tests the *wiring*.)
2. **Scheduler integration** — backlog with shared `resources` must serialize
   into later batches; only the first batch launches per turn. Assert via the
   pure `Scheduler.plan()` output (`scheduler.py`).
3. **IntegrateLane anti-clobber** — base + worktree edits to (a) disjoint
   regions → clean merged, (b) same region → `conflict`, job lands in
   `state.conflicts`, worktree NOT reclaimed, audit event emitted. Drive
   `safe_merge`/`safe_merge_file` directly with in-memory triples; assert
   `safe_merge_file` does not write `dest` on conflict.
4. **Lock discipline** — patch `async_git_repo_lock` with a recording fake;
   assert worktree create, merge, and commit all occur inside the lock and that
   the integrate→commit epoch re-check defers a stale commit (Section 4.3).
5. **Back-pressure** — disk-free below threshold / worktree cap reached / spend
   "deferred" sentinel each yield zero launches and the correct
   `backpressure:*` heartbeat; no phantom RUNNING entry on a deferred dispatch.
6. **GateLane debounce & single-flight** — a burst of M integrations within the
   quiet window triggers exactly ONE gate; a second gate cannot start while one
   is in flight; a stale-epoch commit is deferred. Use a fake clock and a
   controllable `gate_runner`.
7. **Gate-flake classification** — a flake-shaped red (collection errors, zero
   failures) retries once then (if still red) records red without committing; a
   genuine red blocks commit immediately; both carry measured counts on the
   heartbeat.
8. **Redispatch bound** — a job failing `max_retries` times is parked in
   `conflicts`, never redispatched again; liveness uses the task `done()` state,
   not wall-clock.
9. **Config plumbing** — `PipelineConfig(**pipeline_block)` parses the documented
   keys with defaults; `enabled: false` constructs nothing.

Integration (real asyncio tasks, a temp git repo, fakes for agents/gate):

10. **End-to-end one project** — seed a temp repo + a backlog of K disjoint
    new-file todos; run the controller with a fake agent executor that writes a
    file in its worktree and a fake green gate; assert all K integrate cleanly,
    the integration branch advances once per gated epoch, every worktree is
    reclaimed (no leaked `git worktree list` entries — assert via
    `GitAutomation.list_worktrees`, `repo.py:360`), and disk does not grow
    unbounded.
11. **Concurrent lanes don't block** — a deliberately slow `gate_runner`
    (sleeps) must not stop DispatchLane from launching or IntegrateLane from
    integrating during the gate (assert progress timestamps interleave).
12. **Conflict path e2e** — two worktrees editing the same region of one file;
    first integrates clean, second lands in `conflicts`, gate runs only on the
    first, daemon `/admin` snapshot reports the conflict.
13. **Lifespan wiring** — with `pipeline.enabled: true` the daemon constructs and
    starts the controller (`app.state._pipeline` set, lane tasks alive) and
    tears it down on shutdown without leaking tasks; `/readyz` degrades if a lane
    dies. Extends the existing daemon-lifespan integration test.

Guardrail/observability:

14. **No silent lane** — assert every lane turn emits a heartbeat with a
    `status`, and that a zero-progress turn carries a non-`ok` reason
    (ties into the observability guardrail suite).

---

## 6. Build order (suggested)

1. `pipeline/state.py` — `WorktreeJob`, `PipelineState`, `JobStatus`,
   `PipelineConfig`. Pure dataclasses, fully unit-tested.
2. `pipeline/lanes.py` — `DispatchLane`, `IntegrateLane`, `GateLane`, each a
   `tick()` coroutine over the shared state, wired to the existing primitives
   (no new git/merge logic — call `SaturationController`, `Scheduler`,
   `safe_merge`, `GitAutomation`, `async_git_repo_lock`).
3. `pipeline/controller.py` — `PipelineController` (start/stop/snapshot, the
   three tasks).
4. `daemon_wiring.py` — `_make_pipeline_backlog_provider`.
5. `daemon.py` lifespan wiring (Section 3.3) + `UserConfig.pipeline` field +
   `/admin/pipeline` + `/readyz` lane check.
6. Config docs in `general-ludd.yml` example.

Every step is independently testable; steps 1–4 need no FastAPI app.
