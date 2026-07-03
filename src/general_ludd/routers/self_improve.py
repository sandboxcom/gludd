from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.approval import (
    SELF_IMPROVE_WORK_TYPE,
    ApprovalError,
    SelfImproveApprovalManager,
)
from general_ludd.self_improve.gate import SelfImproveGate
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_update.applier import UpdateApplier
from general_ludd.self_update.safe_writer import AtomicSafeWriter

# Kinds routed through the config-tier UpdateApplier path. ``role`` and ``code``
# tiers are handled elsewhere (Phase 4 wires code-tier hot rotation).
_CONFIG_TIER_KINDS: frozenset[str] = frozenset({"config", "yaml"})
_CONFIG_TIER_CAPABILITY: str = "config_write"

# Priority label -> integer, mirroring EventLoop._PRIORITY_MAP so self-improve
# todos persisted from the admin surfaces sort the same as loop-persisted ones.
_PRIORITY_MAP: dict[str, int] = {"low": 0, "medium": 5, "high": 10, "critical": 20}

# Statuses that mean a self-improve todo is no longer "open" (does not count
# against the SelfImproveGate.max_open runaway cap).
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        TodoStatus.COMPLETE.value,
        TodoStatus.FAILED.value,
        TodoStatus.CANCELLED.value,
    }
)


@dataclass
class _ConfigTierPlan:
    """Adapter exposing the applier's ``UpdatePlan`` Protocol shape from a
    request payload (config-tier only)."""

    kind: str
    capability_required: str
    target_paths: list[str]


class _ConfigTierCapabilityChecker:
    """Grants the config-tier capability (``config_write``) only.

    Implements the ``CapabilityChecker`` Protocol from
    :mod:`general_ludd.self_update.applier`. Fail-closed: anything beyond the
    config-tier grant is denied.
    """

    _ALLOWED: frozenset[str] = frozenset({_CONFIG_TIER_CAPABILITY})

    def allows(self, capability: str) -> bool:
        return capability in self._ALLOWED


def _get_session_factory(app: FastAPI) -> Any:
    return getattr(app.state, "_session_factory", None)


def _coerce_priority(raw: Any) -> int:
    if isinstance(raw, bool):  # bool is an int subclass; treat as unset
        return _PRIORITY_MAP["medium"]
    if isinstance(raw, int):
        return raw
    return _PRIORITY_MAP.get(str(raw).lower(), _PRIORITY_MAP["medium"])


async def _persist_gated_self_improve_todos(
    repo: TodoRepository, todos: list[dict[str, Any]]
) -> list[str]:
    """Persist harness-generated todos behind the self-improve human-approval gate.

    Mirrors ``EventLoop._persist_self_improve_todos``: each admitted todo is
    stamped ``work_type="self_improve"`` and (with the gate's default
    ``auto_queue=False``) parked in ``APPROVAL_REQUIRED`` instead of landing at
    the default ``BACKLOG`` and flowing straight into normal promotion. That
    makes them visible to ``/admin/self-improve/approvals`` and blocks silent
    execution of self-authored work (task #22). Nothing is auto-executed.
    """
    gate = SelfImproveGate()  # defaults: max_open=10, auto_queue=False
    existing = await repo.list_by_work_type(SELF_IMPROVE_WORK_TYPE)
    open_count = sum(
        1 for t in existing if getattr(t, "status", None) not in _TERMINAL_STATUSES
    )
    persisted_ids: list[str] = []
    for todo in todos:
        decision = gate.evaluate(todo, open_count=open_count)
        if not decision.admitted:
            continue
        payload = {
            "title": str(todo.get("title", "Self-improvement task"))[:512],
            "description": str(todo.get("description", "")),
            "status": decision.initial_status,
            "work_type": SELF_IMPROVE_WORK_TYPE,
            "priority": _coerce_priority(todo.get("priority", "high")),
            "created_by": "self_improve_harness",
        }
        created = await repo.create(payload)
        persisted_ids.append(created.todo_id)
        open_count += 1
    return persisted_ids


async def _config_tier_apply(
    app: FastAPI, kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Human-gated config-tier apply (task #22).

    Without a released approval record this NEVER writes to disk: it either
    enqueues an ``APPROVAL_REQUIRED`` change record (no ``approval_id``) or
    performs the write from a previously-approved record's stored spec.
    """
    factory = _get_session_factory(app)
    if factory is None:
        # Fail-closed: no DB means no way to record/verify a human approval, so
        # we must not perform an unreviewed on-disk write.
        raise HTTPException(
            status_code=503,
            detail="config-tier self-improve apply requires the approval database",
        )

    approval_id = payload.get("approval_id")
    if not approval_id:
        return await _enqueue_config_change(factory, kind, payload)
    return await _apply_approved_config_change(factory, str(approval_id))


async def _enqueue_config_change(
    factory: Any, kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Record an APPROVAL_REQUIRED self-improve todo capturing the config change.

    The change spec (kind, capability, target paths, content) is serialized into
    ``plan_artifact`` so the eventual apply writes exactly what a human reviewed
    — the request that triggers the release cannot substitute different content.
    """
    spec = {
        "kind": kind,
        "capability_required": str(
            payload.get("capability_required", _CONFIG_TIER_CAPABILITY)
        ),
        "target_paths": list(payload.get("target_paths", [])),
        "change_content": str(payload.get("change_content", "")),
    }
    targets = ", ".join(spec["target_paths"]) or kind
    async with factory() as session:
        repo = TodoRepository(session)
        created = await repo.create(
            {
                "title": f"Self-improve config write: {targets}"[:512],
                "description": (
                    "Config-tier self-improve on-disk write awaiting human "
                    "approval. Release via /admin/self-improve/approvals then "
                    "re-POST /admin/self-improve/apply with approval_id."
                ),
                "status": TodoStatus.APPROVAL_REQUIRED.value,
                "work_type": SELF_IMPROVE_WORK_TYPE,
                "priority": _PRIORITY_MAP["high"],
                "created_by": "self_improve_apply",
                "plan_artifact": json.dumps(spec),
            }
        )
        approval_id = created.todo_id
        await session.commit()
    return {
        "tier": "config",
        "status": "approval_required",
        "approval_id": approval_id,
        "target_paths": spec["target_paths"],
    }


async def _apply_approved_config_change(
    factory: Any, approval_id: str
) -> dict[str, Any]:
    """Perform the on-disk config write for a human-RELEASED approval record.

    The record must be a self-improve todo that a human approved
    (APPROVAL_REQUIRED -> QUEUED). The write uses the spec stored on the record,
    routed through UpdateApplier + AtomicSafeWriter so the capability/denylist/
    YAML/rollback guards still run. A successfully-applied record is consumed
    (QUEUED -> ACTIVE -> COMPLETE) so it cannot be replayed.
    """
    async with factory() as session:
        repo = TodoRepository(session)
        todo = await repo.get_by_id(approval_id)
        if todo is None:
            raise HTTPException(
                status_code=404, detail=f"approval {approval_id} not found"
            )
        if getattr(todo, "work_type", None) != SELF_IMPROVE_WORK_TYPE:
            raise HTTPException(
                status_code=409,
                detail=f"approval {approval_id} is not a self-improve record",
            )
        if todo.status != TodoStatus.QUEUED.value:
            # Not yet released by a human (APPROVAL_REQUIRED), or already
            # consumed/rejected. Refuse rather than write.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"approval {approval_id} is not released "
                    f"(status={todo.status}); a human must approve it first"
                ),
            )
        try:
            spec = json.loads(todo.plan_artifact or "{}")
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} has a malformed change spec",
            ) from exc

        workspace_root = Path.cwd()
        safe_writer = AtomicSafeWriter(workspace_root=workspace_root)
        applier = UpdateApplier(
            writer=safe_writer,
            capability_checker=_ConfigTierCapabilityChecker(),
            workspace_root=workspace_root,
        )
        plan = _ConfigTierPlan(
            kind=str(spec.get("kind", "config")),
            capability_required=str(
                spec.get("capability_required", _CONFIG_TIER_CAPABILITY)
            ),
            target_paths=list(spec.get("target_paths", [])),
        )
        result = applier.apply(plan, str(spec.get("change_content", "")))

        if result.status == "applied":
            # Consume the record so the approval cannot be replayed into a
            # second write (QUEUED -> ACTIVE -> COMPLETE).
            active = await repo.transition(
                approval_id, TodoStatus.ACTIVE, expected_version=todo.version
            )
            await repo.transition(
                approval_id, TodoStatus.COMPLETE, expected_version=active.version
            )
            await session.commit()
        return {
            "tier": "config",
            "status": result.status,
            "approval_id": approval_id,
            "target_paths": result.target_paths,
            "evidence": result.evidence,
        }


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/self-improve/analyze")
    async def admin_self_improve_analyze() -> dict[str, Any]:
        harness = SelfImprovementHarness()
        findings = harness.run_gap_analysis()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": findings,
            "findings_count": len(findings),
        }
        return {"findings": findings, "findings_count": len(findings)}

    @app.post("/admin/self-improve/run")
    async def admin_self_improve_run() -> dict[str, Any]:
        harness = SelfImprovementHarness()
        result = harness.run_full_cycle()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": result["findings"],
            "findings_count": result["findings_count"],
            "todos_enqueued": result["todos_enqueued"],
        }
        factory = _get_session_factory(app)
        if factory is not None:
            async with factory() as session:
                repo = TodoRepository(session)
                persisted_ids = await _persist_gated_self_improve_todos(
                    repo, result.get("todos", [])
                )
                await session.commit()
                result["persisted_todo_ids"] = persisted_ids
        else:
            _daemon_state["todos"].extend(result["todos"])
        return result

    @app.post("/admin/self-improve/apply")
    async def admin_self_improve_apply(payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", ""))

        # Config-tier path: route through UpdateApplier + AtomicSafeWriter. The
        # applier owns capability gating, workspace confinement, the protected-
        # path deny-list, and YAML validation before the atomic write. Phase 4
        # will add code-tier hot rotation; until then non-config kinds fall
        # through to the legacy SelfImprovementWorkflow validate/apply/reload.
        #
        # HUMAN-APPROVAL GATE (task #22): a config-tier apply must NOT write to
        # disk on the bare authenticated request. Two-step flow, mirroring the
        # event-loop self-improve gate:
        #   1. No ``approval_id`` -> ENQUEUE an APPROVAL_REQUIRED self-improve
        #      record capturing the change spec and return WITHOUT writing. The
        #      record shows up in /admin/self-improve/approvals for a human.
        #   2. ``approval_id`` referencing a human-RELEASED (approved -> QUEUED)
        #      self-improve record -> perform the write, using the RECORDED spec
        #      (never the request body) so an approve-A / apply-B bait-and-switch
        #      is impossible. The capability/denylist/YAML/rollback guards still
        #      run inside UpdateApplier + AtomicSafeWriter.
        if kind in _CONFIG_TIER_KINDS:
            return await _config_tier_apply(app, kind, payload)

        # Legacy / code-tier path: validate -> apply -> reload via
        # SelfImprovementWorkflow. Validation runs the test suite in the given
        # worktree; a failing or missing worktree means NOT applied (fail-closed).
        from general_ludd.reload.self_improve import SelfImprovementWorkflow

        workflow = SelfImprovementWorkflow()
        todo = workflow.create_improvement_todo(
            title=str(payload.get("title", "")),
            description=str(payload.get("description", "")),
        )
        worktree_path = str(payload.get("worktree_path", ""))
        # These are blocking sync ops (validate runs the worktree TEST SUITE via
        # subprocess; reload broadcasts to workers) — offload so the code-tier
        # self-improve apply doesn't freeze the event loop for the full run.
        validation = await asyncio.to_thread(workflow.validate_improvement, worktree_path)
        apply_result = await asyncio.to_thread(
            workflow.apply_improvement, todo["todo_id"], validation
        )
        reload_result = await asyncio.to_thread(workflow.reload_if_needed, apply_result)
        return {
            "todo_id": todo["todo_id"],
            "validation_passed": validation.success,
            "applied": apply_result.applied,
            "reload_needed": apply_result.reload_needed,
            "reload_status": reload_result.status,
        }

    @app.get("/admin/self-improve/status")
    async def admin_self_improve_status() -> dict[str, Any]:
        last = _daemon_state.get("self_improve_last_analysis")
        if last is None:
            return {"status": "never_run", "findings_count": 0}
        return {"status": "completed", **last}

    # ------------------------------------------------------------------
    # Human approval gate for self-authored self-improve todos.
    #
    # SelfImproveGate.auto_queue defaults to False, so admitted self-improve
    # todos are parked in APPROVAL_REQUIRED instead of executing without review
    # (self-modification approval bypass otherwise). These routes are the WIRED
    # release path: without them held todos would strand forever.
    # ------------------------------------------------------------------

    def _todo_view(todo: Any) -> dict[str, Any]:
        return {
            "todo_id": getattr(todo, "todo_id", None),
            "title": getattr(todo, "title", None),
            "status": getattr(todo, "status", None),
            "work_type": getattr(todo, "work_type", None),
            "priority": getattr(todo, "priority", None),
            "project_id": getattr(todo, "project_id", None),
            "version": getattr(todo, "version", None),
            "created_at": str(getattr(todo, "created_at", "")) or None,
            "created_by": getattr(todo, "created_by", None),
        }

    @app.get("/admin/self-improve/approvals")
    async def admin_self_improve_list_approvals() -> dict[str, Any]:
        """List self-improve todos awaiting a human approve/reject decision."""
        factory = _get_session_factory(app)
        if factory is None:
            return {"pending": [], "count": 0}
        manager = SelfImproveApprovalManager()
        async with factory() as session:
            repo = TodoRepository(session)
            pending = await manager.list_pending(repo)
            rows = [_todo_view(t) for t in pending]
        return {"pending": rows, "count": len(rows)}

    @app.post("/admin/self-improve/approvals/{todo_id}/approve")
    async def admin_self_improve_approve(todo_id: str) -> dict[str, Any]:
        """Release a held self-improve todo into the queue (APPROVAL_REQUIRED -> QUEUED)."""
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database session factory")
        manager = SelfImproveApprovalManager()
        async with factory() as session:
            repo = TodoRepository(session)
            try:
                todo = await manager.approve_by_id(repo, todo_id)
            except ApprovalError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            await session.commit()
            return {"approved": True, "todo": _todo_view(todo)}

    @app.post("/admin/self-improve/approvals/{todo_id}/reject")
    async def admin_self_improve_reject(
        todo_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Reject a held self-improve todo (APPROVAL_REQUIRED -> CANCELLED)."""
        reason = str((payload or {}).get("reason", ""))
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(status_code=503, detail="No database session factory")
        manager = SelfImproveApprovalManager()
        async with factory() as session:
            repo = TodoRepository(session)
            try:
                todo = await manager.reject_by_id(repo, todo_id, reason=reason)
            except ApprovalError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            await session.commit()
            return {"rejected": True, "todo": _todo_view(todo)}
