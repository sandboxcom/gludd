# SPEC — HITL Approval, Pause/Resume Fidelity, and the Completion Gate

Status: DRAFT (implementation-ready)
Scope: four coupled defects in the human-in-the-loop (HITL) approval surface,
the pause/resume path, the completion quality gate, and the
`NEEDS_MORE_WORK` requeue dead-end.
Author: research/spec pass (verify-first).

This spec is turnkey: every claim below was re-verified against the tree at
authoring time (branch `development`). Where a source line in the originating
brief was stale, the corrected line is called out in the **Verification**
section and used throughout the rest of the spec.

---

## 0. Verification log (read this first)

Every item was checked against source. `TRUE`/`FALSE`/`CORRECTED` verdicts:

### Item 1 — ApprovalGate is dead scaffolding — **TRUE**
- `approval/gate.py:31-33` — `ApprovalGate.request_approval` returns
  `ApprovalResponse(request=request)`; `ApprovalResponse.decision` defaults to
  `ApprovalDecision.PENDING` (`gate.py:26`). Nothing can move it off PENDING.
- `request_approval` has **zero** production call sites. All callers are tests:
  `tests/unit/test_approval_gate.py:20`, `tests/unit/test_approval_gate_wiring.py:28`,
  `tests/unit/test_hitl_approval_wiring.py:101`.
- `daemon.py:1355-1356` — `from general_ludd.approval.gate import ApprovalGate` /
  `app.state._approval_gate = ApprovalGate()`.
- `routers/approval.py` exposes exactly one route: `GET /admin/approval/status`
  (`routers/approval.py:24-31`) returning `{"wired": ..., "gate_type": ...}`.
- The real, working decision surface: `routers/human_todos.py` —
  `PATCH /api/human-todos/{human_todo_id}` (`human_todos.py:219-222`) requires
  `human_resolver` + `human_resolution` for both `done` (`:240-245`) and
  `dismissed` (`:249-254`), and unblocks the linked parent agent todo
  `BLOCKED_ON_HUMAN -> QUEUED` (done) / `-> CANCELLED` (dismissed)
  (`:278-299`). Parent is set to `BLOCKED_ON_HUMAN` at POST time
  (`human_todos.py:143-161`).
- Three unrelated approve/deny mechanisms confirmed:
  1. `ApprovalGate` (dead).
  2. `human_todos` (working).
  3. In-memory `_escalation_store` in `routers/security.py` — accessor
     `_get_esc_store` (`security.py:92-97`), sync helper
     `_sync_escalation_from_human_todo` (`security.py:183-218`), plus its own
     status machine on `POST /admin/perm/escalation-request`
     (`security.py:433-551`) with hand-rolled approve/deny endpoints
     (`security.py:577-650` / `:652-692`).
- `routers/self_improve.py` `_ConfigTierCapabilityChecker` (`self_improve.py:58-69`,
  instantiated `:281`) is a fail-closed capability allow-list; the *human* gate
  for config writes is an ad hoc `TodoStatus` flow (`APPROVAL_REQUIRED -> QUEUED
  -> ACTIVE -> COMPLETE`, enforced at `self_improve.py:231`, `:296-301`), not
  `ApprovalGate`.

### Item 2 — pause/resume drops history AND depth — **TRUE (with a sharpening)**
- `controllers/pause_controller.py:185-195` builds `AgentEnvironmentSnapshot`
  with neither `messages=` nor `depth=` — both take defaults.
- `agents/types.py:42-54` — `AgentTask` has **no** `messages` field (fields:
  `task_id, agent_name, description, prompt, parent_task_id, invoker_name,
  project_id, depth, tools, estimated_effort`). `depth` default `0`
  (`types.py:51`).
- `dispatcher.resume_project` threads `depth=snap.depth` correctly
  (`dispatcher.py:523-531`). `PauseController.resume_rehydrate`
  (`pause_controller.py:203-242`) calls it (`:240`).
- **That path is DEAD in prod**: `routers/pause.py` `api_resume_project`
  (`pause.py:123-179`) reimplements rehydration inline — hydrates via
  `hibernation._store.hydrate(...)` (`:151`) and dispatches via
  `dispatcher.dispatch_one(task)` (`:161`), never calling `resume_rehydrate`
  or `resume_project`. Its inline `AgentTask(...)` (`pause.py:152-160`) has no
  `depth=` argument, so every resumed task resets to `depth=0` — even though
  `handle_obj.depth` is read at `:149`.
- Max-depth guard: `dispatcher._check_nesting_depth` (`dispatcher.py:120-136`);
  the `task.depth > limit` compare is `dispatcher.py:126`. (Brief said
  "126-132"; the enclosing method is 120-136.)
- `hibernation.messages_from_dicts` (`hibernation.py:164-166`) and
  `HibernationController.parked` (`hibernation.py:533-536`) — **zero** prod
  callers; only tests (`test_agent_hibernation.py`, `test_hibernation_integration.py`).
- **Open trace RESOLVED — the sharpening.** In-flight message history is NOT
  reachable from `AgentDispatcher._active_tasks` in *either* execution path,
  for a deeper reason than "the field is missing":
  - `_active_tasks` def `dispatcher.py:68`; write `dispatcher.py:349`
    (`dispatch_one`, before `await self._executor(task)`); pop
    `dispatcher.py:426` (`finally`). The stored `AgentTask` is never mutated
    in between.
  - The daemon executor wired into the dispatcher, `_gateway_executor`
    (`daemon.py:2087-2101`, wired at `daemon.py:2118`), issues a **single**
    `model_gateway.call_model_with_retry(profile_id, [{"role":"user","content":
    task.prompt}], ...)` (`daemon.py:2091-2094`). It does **not** instantiate
    `ToolCallLoop` at all. So on the subagent-dispatch path there is no
    multi-turn history to lose beyond the prompt.
  - The multi-turn conversation actually lives in a **disjoint** path: the
    EventLoop Phase-2 executor calls `ToolCallLoop.run_with_tools`
    (`event_loop/loop.py:2643-2701`), whose `messages` list is a local
    variable (`execution/tool_loop.py:189-192`) grown/compacted per iteration
    and **never written back** to any `AgentTask`, `_active_tasks` entry, or
    todo row. This path is keyed by `JobSpec`/`job_id`/`todo_id`, not by
    `AgentDispatcher`.
  - Therefore the correct place to capture in-flight history is a new
    write-back seam in `ToolCallLoop.run_with_tools` (§Item 2 fix), not
    `_active_tasks`.

### Item 3 — completion gate — **PARTIALLY REFUTED, as briefed**
- System B (`quality/project_gate.py:run_project_gate`) IS wired at both
  completion-transition sites:
  - `review/decision_applier.py:57-110` — downgrades `complete ->
    needs_more_work` on gate failure (`:91-97`); pre-checks
    `(workspace/"project.yml").is_file()` (`:66`).
  - `event_loop/loop.py:3347-3395` — downgrades to
    `TodoStatus.NEEDS_MORE_WORK` (`:3395`); pre-checks `is_file()` (`:3355`).
  - `run_project_gate` is fail-closed (`project_gate.py:69-104, 168-192`) and
    powered by the P-1 runner (`project_runner` — `ProjectCommandRunner`,
    `load_project_profile`). It works.
- System A (`quality/gate.py:QualityGateChecker.enforce`) is the dead one:
  `enforce()` has **zero** production callers. The class's only prod use is
  `routers/maintenance.py:118-126`, which calls `check_python_coverage(...)`
  (the route is `POST /admin/quality/check`), never `enforce()`.
- Confirmed dead config: `schemas/quality_gate.py:62` `block_todo_complete`
  (default `True`) and siblings `block_commit/block_merge/block_tag/block_push/
  block_reload` (`:63-67`). `enforce()` (`gate.py:78-88`) reads
  `block_todo_complete/commit/merge/push/reload` — but is never called, and
  System B's downgrade is hardcoded/unconditional (no config gate).
- `is_file()` pre-check confirmed on both sites → target projects **without an
  explicit `project.yml`** get NO completion gate, even though `run_project_gate`
  → `load_project_profile` supports auto-detect.
- Bypasses confirmed: self_update reload (`loop.py:3141` sets
  `COMPLETE`/`FAILED` with no gate call) and self-improve config-tier approval
  (`self_improve.py` §Item 1) never touch System B.
- **S20 fail-open** — `quality/gate.py:79` `all(g.get("passed", True) for g in
  gate_results)` treats a gate dict missing `passed` as PASSED. Confirmed.
  Sibling `quality/preflight.py` is fail-closed. Verified safe to flip: every
  `enforce([...])` call in `tests/unit/test_quality_gate.py` supplies `passed`
  explicitly (`test_quality_gate.py:53, 60, 65`).

### Item 4 — `NEEDS_MORE_WORK` is a dispatcher dead-end — **TRUE (with a file correction)**
- `VALID_TRANSITIONS[NEEDS_MORE_WORK]` legally allows `{QUEUED, ACTIVE}` in
  both machines: `db/repository.py:74` and `schemas/todo.py:100`
  (schemas also allows `CANCELLED`).
- The only production transition **into** `QUEUED` that fires from a hold is
  `self_improve/approval.py:80-97` (`approve()`), which requires
  `APPROVAL_REQUIRED` (`is_pending_approval`, `:76-78`) — never
  `NEEDS_MORE_WORK`.
- `claim_runnable` claims **only** QUEUED (`db/repository.py:442`, filter
  `status == TodoStatus.QUEUED.value` at `:456`). So `NEEDS_MORE_WORK` todos are
  invisible to the dispatcher forever.
- `remediation/blocker_detector.py:246-298` (`_scan_chronic_requeues`) DOES scan
  `NEEDS_MORE_WORK` (`:261`) but only emits a read-only `BlockedTask` finding;
  it honors `max_requeues_before_chronic` (`:274`).
- **File correction:** the brief's `remediation/remediation_scheduler.py` does
  NOT exist. The module is `remediation/{__init__,blocker_detector,dispatcher,
  reporter}.py`. `RemediationDispatcher.remediate` (`dispatcher.py:114-176`)
  can create a *fresh* QUEUED todo via the `dispatch_agent` strategy
  (`_dispatch_remediation_agent`, `dispatcher.py:182-238`) — but that creates a
  NEW todo id and never transitions the existing `NEEDS_MORE_WORK` row. This
  spec therefore lands the requeue sweep as a **new EventLoop phase**
  (primary design), not "an action off remediation_scheduler.py".
- EventLoop phase order is asserted verbatim by tests:
  `tests/unit/test_event_loop.py:60-76` (15-phase `expected` list) and
  `tests/e2e/test_obj04_event_loop.py`. Any new phase must update both.

---

## 1. Item 1 — Make ApprovalGate a thin adapter over human-todos, and consolidate the three gates

### 1.1 Problem
G7 HITL approval is instantiated, introspected ("is it wired?"), and never
invoked. Meanwhile two *other* approve/deny mechanisms carry the real load:
`human_todos` (durable, unblocks parents, already wired to escalation sync) and
an in-memory `_escalation_store` (volatile, lost on restart). This is triple
maintenance with one working surface.

### 1.2 Design
Make `ApprovalGate` a **thin adapter over human-todos**, and route the two ad
hoc callers (`routers/security.py` escalation creation, `routers/self_improve.py`
config-tier approval) through it so there is exactly one durable approval
surface.

`ApprovalGate` gains a repository/session dependency and two async methods:

- `request_approval(request)` — create a `HumanTodoModel` with
  `category="permission_escalation"`, `title`/`body` from the request, storing
  `resource_id`/`action`/`requester` in the human-todo tags/body and, when the
  request maps to an agent todo, `parent_agent_todo_id`. Return an
  `ApprovalResponse` whose `decision=PENDING` and whose
  `request.metadata["human_todo_id"]` carries the created row id.
- `check_decision(human_todo_id)` — read the row; map `status=="done"` →
  `APPROVED`, `status=="dismissed"` → `DENIED`, else `PENDING`. `reviewer` ←
  `human_resolver`, `comment` ← `human_resolution`.

Blocking/resume then comes free: resolving the human-todo already flips the
parent `BLOCKED_ON_HUMAN -> QUEUED/CANCELLED` (`human_todos.py:278-299`) and
already syncs escalation rows (`security.py:_sync_escalation_from_human_todo`).

The `_escalation_store` in-memory list is retired; escalation creation and
approve/deny read/write human-todos of `category="permission_escalation"` (the
sync helper already keeps them coherent).

### 1.3 Exact changes

**`src/general_ludd/approval/gate.py`** — replace the dead body.

Before (`gate.py:31-33`):
```python
class ApprovalGate:
    def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(request=request)
```

After (sketch — async, repo-backed):
```python
class ApprovalGate:
    """Thin adapter over the durable human-todo approval surface.

    ``request_approval`` files a ``permission_escalation`` human-todo (which,
    when it carries ``parent_agent_todo_id``, blocks that agent todo on
    BLOCKED_ON_HUMAN via the human-todos router wiring). ``check_decision``
    reads the resolved status back: done -> APPROVED, dismissed -> DENIED.
    """

    def __init__(self, human_todo_repo: HumanTodoRepository) -> None:
        self._repo = human_todo_repo

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        row = await self._repo.create(
            agent_id=request.requester,
            title=f"[approval] {request.action} on {request.resource_id}",
            body=request.reason or f"{request.requester} requests {request.action}",
            category="permission_escalation",
            priority="high",
            parent_agent_todo_id=request.metadata.get("parent_agent_todo_id"),
            tags=["approval", f"resource:{request.resource_id}", f"action:{request.action}"],
        )
        md = {**request.metadata, "human_todo_id": str(getattr(row, "id", ""))}
        req2 = replace(request, metadata=md)  # dataclasses.replace
        return ApprovalResponse(request=req2, decision=ApprovalDecision.PENDING)

    async def check_decision(self, human_todo_id: str) -> ApprovalResponse:
        row = await self._repo.get_by_id(human_todo_id)
        if row is None:
            return ApprovalResponse(request=_unknown_request(human_todo_id))
        status = getattr(row, "status", "open")
        decision = {
            "done": ApprovalDecision.APPROVED,
            "dismissed": ApprovalDecision.DENIED,
        }.get(status, ApprovalDecision.PENDING)
        return ApprovalResponse(
            request=_request_from_row(row),
            decision=decision,
            reviewer=getattr(row, "human_resolver", "") or "",
            comment=getattr(row, "human_resolution", "") or "",
        )
```
(`ApprovalRequest`/`ApprovalResponse` are unchanged dataclasses; add
`from dataclasses import replace`.)

**`src/general_ludd/daemon.py:1355-1356`** — construct the gate with the repo
factory instead of no args. Before:
```python
from general_ludd.approval.gate import ApprovalGate
app.state._approval_gate = ApprovalGate()
```
After: pass the human-todo repository (or a session-factory closure that
builds `HumanTodoRepository` per call), consistent with how the daemon wires
other repos. The `GET /admin/approval/status` route (`routers/approval.py`) is
unchanged — `wired`/`gate_type` still work.

**`src/general_ludd/routers/security.py`** — route escalation creation/approve/
deny through the gate + human-todos instead of the in-memory `_escalation_store`:
- `POST /admin/perm/escalation-request` (`security.py:433-551`): keep the
  `_is_strict_subset_of_both` auto-approve fast-path (`security.py:251-272`),
  but for the non-auto path call `app.state._approval_gate.request_approval(...)`
  rather than appending a dict to `_get_esc_store(app)` (`security.py:495`).
- Approve/deny endpoints (`security.py:577-650` / `:652-692`): resolve the
  underlying human-todo (`PATCH`-equivalent to `done`/`dismissed`) so the
  existing `_sync_escalation_from_human_todo` (`security.py:183-218`) keeps the
  escalation view coherent, then drop `_get_esc_store`/`_escalation_store`
  entirely.

**`src/general_ludd/routers/self_improve.py`** — config-tier approval
(`_ConfigTierCapabilityChecker` context, `self_improve.py:58-69`, `:281`)
continues to *apply* via `UpdateApplier`, but the human approval step routes
through the gate: file the approval via `request_approval` and release the
self-improve todo to `QUEUED` only on `check_decision(...) == APPROVED` (this
replaces the raw `APPROVAL_REQUIRED -> QUEUED` hand-off with a gate-mediated
one; the `TodoStatus` flow at `self_improve.py:231`, `:296-301` is preserved).

### 1.4 Tests that FAIL today (add; TDD)
- `tests/unit/test_approval_gate_adapter.py::test_request_approval_creates_permission_escalation_human_todo`
  — asserts a `HumanTodoModel` with `category="permission_escalation"` is
  created and `human_todo_id` is echoed back. Fails today: `request_approval`
  creates nothing.
- `...::test_check_decision_maps_done_to_approved` — resolve the human-todo to
  `done`, assert `check_decision(...).decision == APPROVED`. Fails today: no
  `check_decision` method exists.
- `...::test_check_decision_maps_dismissed_to_denied` — symmetric for DENIED.
- `...::test_request_approval_blocks_parent_agent_todo` — with
  `parent_agent_todo_id`, assert the parent transitions to `BLOCKED_ON_HUMAN`.
- `tests/unit/test_security_escalation_uses_gate.py::test_escalation_request_files_human_todo_not_memory_store`
  — assert `app.state._escalation_store` is not created / not used.
- Existing `tests/unit/test_hitl_approval_wiring.py` and
  `test_approval_gate_wiring.py` must be updated: they currently assert the
  PENDING-always behavior and a no-arg constructor; those assertions change.

### 1.5 Effort / risk / rollback
- Effort: ~1–1.5 days. The heavy lifting (durable todos, parent unblock,
  escalation sync) already exists; this is adapter + call-site rewiring.
- Risk: MEDIUM. Escalation approve/deny endpoints change their backing store —
  any external client polling `_escalation_store`-shaped responses must be
  covered by the sync helper's response shape. Auto-approve fast-path must be
  preserved verbatim to avoid regressing least-privilege grants.
- Rollback: revert `gate.py` + the daemon wiring line; the security/self_improve
  call sites fall back to their current ad hoc paths. No schema migration, so
  rollback is code-only.

---

## 2. Item 2 — Preserve conversation history AND recursion depth across pause/resume

### 2.1 Problem
Two independent losses on resume:
1. **Depth** is dropped because the production resume path
   (`routers/pause.py:123-179`) reimplements rehydration inline and omits
   `depth=`, resetting resumed trees to `depth=0` and defeating the max-nesting
   guard (`dispatcher.py:126`). The correct code already exists
   (`dispatcher.resume_project` threads `depth=snap.depth`,
   `pause_controller.resume_rehydrate` calls it) but is bypassed.
2. **Messages** are dropped because (a) `AgentTask` has no `messages` field,
   (b) `pause_controller.quiesce_project` snapshots only `description`/`prompt`
   (`pause_controller.py:190-193`), and (c) the real multi-turn history lives in
   `ToolCallLoop.run_with_tools`'s **local** `messages` list
   (`tool_loop.py:189-192`) on the EventLoop/JobSpec path — never written
   anywhere durable, never reachable from `_active_tasks`.

### 2.2 Design — depth (the cheap half: stop bypassing working code)
Delete the inline rehydration loop in `routers/pause.py:132-166` and call the
already-correct controller path:
```python
snapshots, status, errors = await controller.resume_rehydrate(
    "project", req.project_id, dispatcher=dispatcher, hibernation=hibernation,
)
rehydrated_count = len(snapshots)
rehydrate_errors = errors
```
`resume_rehydrate` (`pause_controller.py:203-242`) → `dispatcher.resume_project`
(`dispatcher.py:506-534`) already threads `depth=snap.depth` (`:531`). This
restores the recursion guard for resumed trees at zero new code.

Corequisite: `HibernationHandle` must carry `depth` (the inline loop read
`handle_obj.depth` at `pause.py:149`, so the field exists on the handle) and
`AgentEnvironmentSnapshot` must expose `depth` so `resume_project` reads a real
value. Verify `AgentEnvironmentSnapshot.depth` is populated at dehydrate time in
`quiesce_project` (see §2.3).

### 2.3 Design — messages (the real half)
The history that matters is the `ToolCallLoop` conversation on the EventLoop
path, not the single-shot dispatcher call. Two-part fix:

**(a) Add a message field to the snapshot and the task.**
- `agents/types.py:42-54` — add `messages: list[dict[str, object]] | None = None`
  to `AgentTask` (nullable, defaulted, so no call-site breaks).
- `AgentEnvironmentSnapshot` — add/populate `messages` and `depth` fields so
  the dehydrate/rehydrate round-trip carries them.
  `hibernation.messages_from_dicts` (`hibernation.py:164-197`) is the existing,
  tested bridge for the dict↔`ContextMessage` conversion — wire it in here so
  it stops being test-only.

**(b) Capture in-flight messages at their real source — a write-back seam in
`ToolCallLoop`.** Because `run_with_tools` keeps `messages` local
(`tool_loop.py:189-192`), add an optional sink so the running transcript is
observable at the quiesce boundary:
- Add a constructor/param hook, e.g. `on_messages_update:
  Callable[[list[dict]], None] | None = None`, invoked after each turn append
  in `run_with_tools`. The EventLoop caller (`loop.py:2643-2701`) passes a sink
  that writes the latest transcript to a per-job registry keyed by
  `job_id`/`todo_id` (a small dict on the loop, mirroring `_active_tasks`), or
  persists a compacted transcript onto the todo row.
- `quiesce_project` then reads that registry (not `_active_tasks`) for the
  live transcript and stores it in the snapshot. Update
  `pause_controller.quiesce_project` (`pause_controller.py:185-195`) to set
  `messages=<captured>` and `depth=<task.depth>` on `AgentEnvironmentSnapshot`.

For the subagent-dispatch path (`daemon.py:_gateway_executor`,
`daemon.py:2087-2101`), there is no multi-turn loop today — history == the
prompt. Two acceptable options, pick per effort budget:
- **Minimal**: document that this path has only prompt-level history; snapshot
  the prompt (already done). No behavior change.
- **Complete**: migrate `_gateway_executor` to use `ToolCallLoop` with the same
  `on_messages_update` sink and store the transcript on the `AgentTask.messages`
  field, so `_active_tasks` *does* carry live history and quiesce can read it
  directly. This is the only way `_active_tasks` becomes the right seam; it is
  larger and should be a follow-up slice, not gated into this change.

**(c) Stop bypassing the dehydration policy.** `quiesce_project` currently calls
`hibernation._store.dehydrate_async(snap)` directly (`pause_controller.py:196`),
skipping the `min_depth`/`min_context_messages` policy in
`HibernationController`. Route through the controller's policy method
(e.g. `HibernationController.parked()` / a `dehydrate_if_policy_warrants`
entry, `hibernation.py:533-561`) so the min-depth/min-context thresholds apply
and `parked()` stops being test-only.

### 2.4 Exact changes (summary table)
| File:line | Change |
|---|---|
| `routers/pause.py:132-166` | Delete inline rehydration loop; call `controller.resume_rehydrate("project", req.project_id, dispatcher=..., hibernation=...)`. |
| `agents/types.py:52` | Add `messages: list[dict[str, object]] | None = None` to `AgentTask`. |
| `agents/hibernation.py` (`AgentEnvironmentSnapshot`) | Add `messages` + ensure `depth` fields; use `messages_from_dicts` on rehydrate. |
| `execution/tool_loop.py:172-…` (`run_with_tools`) | Add `on_messages_update` sink; invoke after each turn append. |
| `event_loop/loop.py:2643-2701` | Pass a sink that records the running transcript to a per-job registry keyed by `job_id`/`todo_id`. |
| `controllers/pause_controller.py:185-196` | Snapshot `messages=<captured>` + `depth=<task.depth>`; route dehydrate through the policy method, not `_store.dehydrate_async` directly. |
| `agents/dispatcher.py:523-531` | (No change — already threads `depth`.) Optionally thread `messages=snap.messages` into the rehydrated `AgentTask`. |

### 2.5 Tests that FAIL today (add; TDD)
- `tests/unit/test_pause_resume_depth.py::test_resume_preserves_depth`
  — dehydrate an `AgentTask(depth=3)`, resume via the router, assert the
  re-enqueued task has `depth==3`. Fails today: inline loop resets to 0.
- `tests/integration/test_pause_resume_router_uses_controller.py::test_api_resume_project_calls_resume_rehydrate`
  — assert `resume_rehydrate` is invoked (spy). Fails today: router bypasses it.
- `tests/unit/test_agenttask_messages_field.py::test_agenttask_has_messages_field`
  — `AgentTask(...).messages is None` constructs. Fails today: no field.
- `tests/unit/test_toolloop_message_sink.py::test_run_with_tools_reports_transcript`
  — assert the `on_messages_update` sink receives a growing transcript. Fails
  today: no sink; `messages` is local.
- `tests/integration/test_quiesce_captures_messages.py::test_quiesce_snapshots_live_transcript`
  — drive a tool loop, quiesce, assert the snapshot carries the running
  messages. Fails today: only `description`/`prompt` captured.
- `tests/unit/test_quiesce_respects_dehydration_policy.py::test_quiesce_below_min_depth_not_dehydrated`
  — assert the min-depth policy is honored. Fails today: `_store.dehydrate_async`
  is called directly.

### 2.6 Effort / risk / rollback
- Effort: depth half ~0.5 day (delete + call existing code + one test). Messages
  half ~2–3 days (snapshot/task fields + tool-loop sink + registry + policy
  routing). The `_gateway_executor`→`ToolCallLoop` "Complete" option is a
  separate ~2-day follow-up.
- Risk: depth half LOW (activating already-tested code). Messages half MEDIUM —
  the tool-loop sink is on a hot path; keep it O(1) per turn (store a reference,
  don't deep-copy every turn) and guard the registry with the loop's existing
  concurrency discipline.
- Rollback: depth half — restore the inline loop. Messages half — the new
  fields are nullable/defaulted, so reverting the sink leaves `messages=None`
  and the system degrades to today's prompt-only behavior with no crash.

---

## 3. Item 3 — Wire `block_todo_complete` (flip the fail-open default in the same change)

> **Landing constraint:** Item 3 MUST land together with Item 4. Strengthening
> the gate without a requeue sweep strands more work permanently (see §4).

### 3.1 Problem
- `block_todo_complete` and its 5 siblings are dead config: System B's downgrade
  is hardcoded/unconditional and `enforce()` (which reads them) is never called.
- Target projects **without** `project.yml` get no gate at all (both call sites
  guard on `is_file()`).
- `enforce()`'s `all(g.get("passed", True))` (`gate.py:79`) is fail-open — a
  landmine the moment `enforce()` enters production.

### 3.2 Design
Do NOT rebuild the gate. Two surgical changes:

**(a) Flip the fail-open default in `quality/gate.py:79` — in this same change.**
Before:
```python
all_passed = all(g.get("passed", True) for g in gate_results)
```
After:
```python
all_passed = all(g.get("passed", False) for g in gate_results)
```
Verified safe: every `enforce([...])` call in
`tests/unit/test_quality_gate.py` (`:53, :60, :65`) supplies `passed`
explicitly, so no existing test breaks. This must land in the same commit that
first exercises `enforce()` in production.

**(b) Make the gate reach projects without an explicit `project.yml`.** Replace
the `is_file()` pre-check at both sites with a profile-resolution attempt that
uses `run_project_gate`'s own fail-closed `load_project_profile` auto-detect
(which the runner already supports). Concretely, in
`review/decision_applier.py:64-66` and `event_loop/loop.py:3353-3355`, drop the
`if (workspace / "project.yml").is_file():` guard and instead call
`run_project_gate(str(workspace))` unconditionally when `repo_root is not None`,
letting `run_project_gate` return `passed=False` + `error` when no profile can
be resolved (fail-closed) — but gate that fail-closed verdict behind the new
`block_todo_complete` config so a project with genuinely no toolchain isn't
force-failed (see (c)).

**(c) Honor `block_todo_complete` at both System B sites.** Read the config and
only apply the `complete -> needs_more_work` downgrade when
`enforcement.block_todo_complete` is true. This makes the config live for the
first time; combined with (a) it is fail-closed. Consider consolidating the
downgrade decision through `QualityGateChecker.enforce()` so System A stops
being dead code and the config is honored in exactly one place. If self_update
reload (`loop.py:3141`) and self-improve config approval are in scope, gate them
behind `block_reload` / a config-tier equivalent (follow-up; not required for
this landing).

### 3.3 Exact changes
| File:line | Change |
|---|---|
| `quality/gate.py:79` | `g.get("passed", True)` → `g.get("passed", False)` (fail-closed). |
| `review/decision_applier.py:64-66` | Remove `is_file()` guard; call `run_project_gate` unconditionally; apply downgrade only when `block_todo_complete`. |
| `event_loop/loop.py:3353-3355` | Same as above for the reconcile-phase site. |
| `schemas/quality_gate.py:62` | (No default change needed; `block_todo_complete=True` is now honored — verify default is the intended production posture.) |

### 3.4 Tests that FAIL today (add; TDD)
- `tests/unit/test_quality_gate.py::test_enforce_missing_passed_key_fails_closed`
  — `enforce([{}])["all_passed"] is False`. Fails today: returns `True`
  (fail-open).
- `tests/integration/test_completion_gate_no_project_yml.py::test_target_without_project_yml_is_gated`
  — a target repo with no `project.yml` and a failing declared/auto-detected
  check downgrades `complete -> needs_more_work`. Fails today: `is_file()` guard
  skips the gate entirely.
- `tests/integration/test_completion_gate_config.py::test_block_todo_complete_false_disables_downgrade`
  — with `block_todo_complete=False`, a failing gate does NOT downgrade. Fails
  today: downgrade is unconditional (config ignored).

### 3.5 Effort / risk / rollback
- Effort: ~1 day (small edits; most cost is the integration tests + the
  no-`project.yml` auto-detect path).
- Risk: MEDIUM — flipping fail-open→fail-closed and removing the `is_file()`
  guard both make the gate stricter; without Item 4 this strands work (hence the
  co-landing constraint). Gate every new strictness behind
  `block_todo_complete` so it can be disabled by config in an emergency.
- Rollback: config kill-switch (`block_todo_complete=False`) disables the
  downgrade without a code revert; the `gate.py:79` flip is a one-line revert.

---

## 4. Item 4 — Requeue sweep for `NEEDS_MORE_WORK` (COREQUISITE — lands WITH Item 3)

### 4.1 Problem
`NEEDS_MORE_WORK` is where System B's gate-failure downgrade lands
(`decision_applier.py:92`, `loop.py:3395`), but `claim_runnable` claims only
QUEUED (`db/repository.py:456`), and no production path transitions
`NEEDS_MORE_WORK -> QUEUED`. So gate-failed todos are invisible to the
dispatcher forever. Strengthening the gate (Item 3) without this sweep makes the
problem strictly worse.

### 4.2 Design — new EventLoop phase `requeue_needs_more_work`
Add a phase that moves `NEEDS_MORE_WORK -> QUEUED` after a cooldown, respecting
`max_requeues_before_chronic` so chronic failures park instead of looping.

- New repository helper `TodoRepository.claim_needs_more_work_for_requeue(
  cooldown_s, project_id=None)` selecting `status == NEEDS_MORE_WORK` with
  `updated_at <= now - cooldown_s` and `run_count < max_requeues_before_chronic`,
  transitioning each to `QUEUED` (transition already legal:
  `repository.py:74`). Reuse the optimistic guarded-UPDATE pattern from
  `claim_runnable` (`repository.py:442-474`) to avoid double-requeue.
- Todos at/over the chronic threshold are NOT requeued; instead park them
  (`MANUAL_HOLD`) and let `blocker_detector._scan_chronic_requeues`
  (`blocker_detector.py:246-298`) surface the human finding it already emits.
  This gives the remediation system the last word on chronic failures.
- New loop method `_phase_requeue_needs_more_work` calling the helper. Place it
  in `PHASE_ORDER` **before** `claim_runnable_todos` so a requeued todo is
  claimable on the same tick (or immediately after `reconcile_completed_decisions`,
  which is where the downgrade happens — pick placement so a downgrade in tick N
  is eligible for requeue no later than tick N+1 after cooldown).

Config: add `requeue_cooldown_s` (default e.g. 300) alongside
`max_requeues_before_chronic` in `RemediationConfig`
(`remediation/blocker_detector.py`), or a new `RequeueConfig` — reuse the
existing `max_requeues_before_chronic` value so chronic classification stays
consistent with the detector.

Rejected alternative: hanging this off `RemediationDispatcher`. Its
`dispatch_agent` strategy creates a *new* QUEUED todo id
(`dispatcher.py:182-238`), which loses the original todo's identity/lineage and
would double-count against the review pipeline. In-place `NEEDS_MORE_WORK ->
QUEUED` on the same row (incrementing `run_count`) is the correct semantics and
keeps the gate/downgrade/requeue loop bounded by `max_requeues_before_chronic`.

### 4.3 Mandatory test updates (phase order is asserted)
- `tests/unit/test_event_loop.py:60-76` — add `"requeue_needs_more_work"` to the
  `expected` phase list at the chosen position; update the count.
- `tests/e2e/test_obj04_event_loop.py` — update its phase-order/phase-count
  assertions to match.

### 4.4 Tests that FAIL today (add; TDD)
- `tests/unit/test_requeue_sweep.py::test_needs_more_work_requeued_after_cooldown`
  — a `NEEDS_MORE_WORK` todo older than the cooldown transitions to `QUEUED`.
  Fails today: no such transition exists.
- `...::test_needs_more_work_within_cooldown_not_requeued` — respects cooldown.
- `...::test_chronic_needs_more_work_parked_not_requeued` — at/over
  `max_requeues_before_chronic`, the todo is parked (`MANUAL_HOLD`), not
  requeued. Fails today: no sweep at all.
- `tests/integration/test_gate_downgrade_then_requeue.py::test_gate_failed_todo_becomes_claimable_again`
  — end-to-end: gate downgrades `complete -> needs_more_work`, sweep requeues,
  `claim_runnable` returns it. Fails today: `claim_runnable` never sees it.
- `tests/unit/test_event_loop.py::test_event_loop_tick_runs_all_phases` — updated
  expected list; fails until the new phase is registered.

### 4.5 Effort / risk / rollback
- Effort: ~1–1.5 days (repo helper + phase + config + the two mandatory
  phase-order test updates + new tests).
- Risk: MEDIUM — a requeue loop with a too-short cooldown or a missing chronic
  cap could thrash a permanently-failing todo. Mitigate with the
  `max_requeues_before_chronic` cap (reused from the detector) and a non-trivial
  default cooldown. The optimistic-UPDATE claim prevents double-requeue under
  concurrent ticks.
- Rollback: remove the phase from `PHASE_ORDER` (todos revert to stranding, i.e.
  today's behavior) — but Item 3 must be reverted/disabled in the same step,
  else strict-gate + no-sweep strands more work than baseline.

---

## 5. Landing order (hard constraints)

1. **Item 4 and Item 3 land together (single PR or back-to-back, Item 4 first
   or same PR).** Item 3 strengthens the gate; Item 4 is what keeps its output
   claimable. Shipping Item 3 alone is a regression.
2. **Item 2 depth-half** is independent and safe — land anytime (it activates
   already-tested code).
3. **Item 2 messages-half** is independent of Items 1/3/4 — land after the
   depth-half; the `_gateway_executor`→`ToolCallLoop` "Complete" option is a
   later follow-up.
4. **Item 1** is independent of the completion gate; it can land in parallel,
   but its `routers/security.py` + `routers/self_improve.py` rewiring should be
   one PR so the three approval mechanisms collapse to one atomically.

Suggested sequence: (Item 2 depth) → (Item 3 + Item 4 together) → (Item 1) →
(Item 2 messages) → (Item 2 `_gateway_executor` follow-up).

## 6. Risk register (cross-cutting)
- **S20 fail-open flip** (`gate.py:79`) is only safe when co-landed with the
  first production use of `enforce()` — never flip it in isolation without also
  ensuring all `enforce` inputs carry `passed`.
- **Phase-order tests** (`test_event_loop.py`, `test_obj04_event_loop.py`) are
  the canary for Item 4 — CI red there means the new phase wasn't reflected in
  both assertions.
- **Hot-path cost** of the Item 2 tool-loop sink — must be O(1) per turn.
- **Escalation store retirement** (Item 1) — verify no external client depends
  on the volatile `_escalation_store` response shape before deleting it.
