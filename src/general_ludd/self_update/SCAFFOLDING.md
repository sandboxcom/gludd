# SCAFFOLDING — `self_update/` Phase-2 modules

This directory contains two generations of the self-update pipeline sitting
side-by-side. Several files are **intentional Phase-2 scaffolding** — fully
implemented, unit-tested, but NOT yet wired into the running daemon. They are
not dead code and must not be deleted by a "dead-code" sweep. This file is the
paper trail.

## File-by-file status

| File | Status | Notes |
|---|---|---|
| `model.py` | **Phase-2 scaffolding** | Pure dataclasses (`Subsystem`, `ChangeKind`, `ApplyTier`, `SelfUpdatePlan`, `SelfUpdateRequest`). Shared vocabulary for the classifier/apply/priority modules. Not yet referenced by any daemon code path. |
| `classifier.py` | **Phase-2 scaffolding** | NL → `SelfUpdatePlan` keyword classifier (`classify`, `llm_route` stub). Not yet called from any router or event-loop phase. |
| `apply.py` | **Phase-2 scaffolding** | The apply ladder (`apply_plan`) — config-tier auto-apply, scaffold-tier, guarded code-tier. Protected-path + hard-deny guards. No `validate`/`audit_sink` is wired from the daemon yet. |
| `priority.py` | **Phase-2 scaffolding** | `compute_priority` / `to_todo_spec` / `to_work_item` / `describe_scheduler_hook`. Produces backlog entries + scheduler work-items but is not yet called from `_dispatch_jobs_via_scheduler`. |
| `router.py` | **Wired (Phase 1)** | `UpdateRequestRouter` / `UpdatePlan` / `UpdateRequest`. Re-exported from `__init__.py`. This is the older, live routing surface. |
| `applier.py` | **Wired (Phase 1)** | `UpdateApplier` with structural `CapabilityChecker` / `SafeWriter` protocols. Decoupled from `apply.py` by design — older live path. |
| `safe_writer.py` | **Wired (Phase 1)** | `AtomicSafeWriter` — concrete `SafeWriter` used by `applier.py`. |

The split exists because issue #81 landed the original Phase-1 pipeline
(`router.py` + `applier.py` + `safe_writer.py`) and then built a richer
Phase-2 ladder (`model.py` + `classifier.py` + `apply.py` + `priority.py`)
whose daemon-side wiring was deferred. Both generations coexist until the
Phase-2 wiring lands and the Phase-1 surface can be retired.

## Phase-2 wiring progress

The executable blueprint is **§7 of
[`docs/design/daemon_integration_plan.md`](../../docs/design/daemon_integration_plan.md)**.
The wiring is tracked as a 7-step sequence. Steps 1–5 are **DONE**; Step 6 is
**NOT DONE** (the critical gap); Step 7 is **PARTIAL**. Step 6 remains the
single blocker for end-to-end self-updates.

### DONE

- **Step 1 — `UserConfig.self_update` field.** `self_update: dict[str, Any] = {}`
  added to `UserConfig` with `auto_apply_config` (default `True`, `apply.py:157`)
  and an approval policy. The Phase-2 config block now has a typed home.
- **Step 2 — `routers/self_update.py`.** New HTTP surface exists with:
  - `POST /admin/self-update/plan` — body `{raw_text, requested_by, approval_token?}`
    → build `SelfUpdateRequest`, run `classifier.classify()` → `SelfUpdatePlan`,
    call `apply.apply_plan(...)` with the daemon-supplied `validate` + `audit_sink`,
    return the `ApplyResult.outcome` + audit dict.
  - `POST /admin/self-update/enqueue` → `priority.to_todo_spec(plan, request)`
    then `TodoRepository.create`, so the request enters the normal backlog.
- **Step 3 — `audit_sink` in `_lifespan`.** Closure over `session_factory` writing
  through `AuditEventRepository`; stored on `app.state._self_update_audit_sink`.
  `validate` wired to the preflight pattern (`daemon.py:761-772`) — or `validate=None`
  which fail-closes any code-tier change (`apply.py:292-301`), the safe default.
- **Step 4 — Register router.** `self_update.router` registered in
  `create_daemon_app`'s router block and in `routers/__init__.register_all`.
  PSK-gated (`/admin/*` is never public).
- **Step 5 — Event-loop scheduler refinement (no new phase).** DONE. The
  scheduler branch in `_dispatch_jobs_via_scheduler`
  (`event_loop/loop.py:718-731`) now branches on
  `todo.queue == "self_update"` and builds the `WorkItem` via
  `priority.to_work_item(plan, todo_id)` so code-tier self-updates serialize
  on the `self_update:code` resource label (`priority.py:26-29`). The tier is
  reconstructed from the todo's `tier:` tag (`priority.py:88-91`). Covered by
  15 unit tests.

### PARTIAL

- **Step 7 — Integration test.** `tests/integration/test_self_update_router_wired.py`
  now exists with tests 1–4 DONE: config-tier auto-applies; protected-path
  requests are `refused`; `/admin/self-update/enqueue` creates a prioritised
  todo; admin PSK enforced. Tests 5–6 (code-tier apply triggers
  `reload_if_needed`, and the full code-tier end-to-end path) are **blocked on
  Step 6** — they cannot be written until `arm.set_code_target` + the daemon
  reload hook exist.

### NOT DONE

- **Step 6 — `arm.set_code_target` + `reload_if_needed` (THE critical gap).**
  No code path exists yet to (a) arm a `set_code_target` mutation via
  `apply.apply_plan` for a code-tier plan and (b) trigger a daemon
  `reload_if_needed` after a successful apply. Until this lands, an approved
  code-tier self-update is classified, queued, and scheduled-eligible but can
  **never actually mutate code or reload the daemon**. This is the single
  blocking gap for end-to-end Phase-2 self-updates.

### Net state

The scaffolding files (`model.py`, `classifier.py`, `apply.py`, `priority.py`)
plus Steps 1–5 mean the **intake half** of the pipeline is wired AND the
scheduler dispatches self-update todos: requests can be classified, applied at
config tier, audited, enqueued into the backlog, and picked up by the event
loop. Step 7's integration tests (1–4) confirm the config-tier path end-to-end.
The **execution half** remains incomplete: Step 6 (code-tier arming + reload)
is still unwritten, and Step 7's tests 5–6 are blocked on it, so code-tier
self-updates still cannot actually run. The scaffolding files remain in tree,
tested, and required as the contract for the remaining execution-side wiring —
deleting them would discard the spec for the work the design doc describes.

## Pointer

- Design doc: [`docs/design/daemon_integration_plan.md`](../../docs/design/daemon_integration_plan.md) — §7 (self_update apply + priority router), §8 Batch C/D/E/F (ordered execution plan).
- Original build: gludd issue #81.
