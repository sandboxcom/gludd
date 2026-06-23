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

## What Phase-2 wiring looks like

The executable blueprint is **§7 of
[`docs/design/daemon_integration_plan.md`](../../docs/design/daemon_integration_plan.md)**.
Summary of what a Phase-2 wiring wave must add:

1. **New HTTP surface — `routers/self_update.py`** (does not exist today):
   - `POST /admin/self-update/plan` — body `{raw_text, requested_by, approval_token?}`
     → build `SelfUpdateRequest`, run `classifier.classify()` → `SelfUpdatePlan`,
     call `apply.apply_plan(...)` with the daemon-supplied `validate` + `audit_sink`,
     return the `ApplyResult.outcome` + audit dict.
   - `POST /admin/self-update/enqueue` → `priority.to_todo_spec(plan, request)`
     then `TodoRepository.create`, so the request enters the normal backlog.
   - Register in `create_daemon_app`'s router block and in
     `routers/__init__.register_all`. PSK-gated (`/admin/*` is never public).

2. **`_lifespan` construction + state key:**
   - Build an `audit_sink` closure over `session_factory` writing through
     `AuditEventRepository`; store on `app.state._self_update_audit_sink`.
   - Wire a real `validate` callable (reuse the preflight pattern at
     `daemon.py:761-772`), or pass `validate=None` which fail-closes any
     code-tier change (`apply.py:292-301`) — the safe default.

3. **Event-loop scheduler refinement (no new phase):**
   - In `_dispatch_jobs_via_scheduler` (`event_loop/loop.py:718-731`), branch on
     `todo.queue == "self_update"` and build the `WorkItem` via
     `priority.to_work_item(plan, todo_id)` so code-tier self-updates serialize
     on the `self_update:code` resource label (`priority.py:26-29`).
   - Reconstruct the tier from the todo's `tier:` tag (`priority.py:88-91`).

4. **`UserConfig` block:** add `self_update: dict[str, Any] = {}` with
   `auto_apply_config` (default `True`, `apply.py:157`) and an approval policy.

5. **Integration test** (`tests/integration/test_self_update_router_wired.py`)
   proving: config-tier auto-applies; protected-path requests are `refused`;
   `/admin/self-update/enqueue` creates a prioritised todo; admin PSK enforced.

Until that wave lands, the four scaffolding files exist purely so the Phase-2
contract is in tree, tested, and ready to wire — deleting them would discard
the spec for the work the design doc describes.

## Pointer

- Design doc: [`docs/design/daemon_integration_plan.md`](../../docs/design/daemon_integration_plan.md) — §7 (self_update apply + priority router), §8 Batch C/D/E/F (ordered execution plan).
- Original build: gludd issue #81.
