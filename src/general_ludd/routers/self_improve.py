"""Administrative routes for gated self-improvement workflows."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.cli_core_changes import _excluded
from general_ludd.db.repository import TodoRepository
from general_ludd.integrity.change_log import ChangeRecordStore
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
_NON_CONFIG_PLAN_SCHEMA_VERSION: int = 1
_NON_CONFIG_PLAN_FIELDS: frozenset[str] = frozenset(
    {
        "description",
        "kind",
        "project_id",
        "schema_version",
        "title",
        "worktree_path",
    }
)

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
    """Adapt a config-tier request to the applier's ``UpdatePlan`` protocol.

    The adapter is intentionally limited to the config-tier route.
    """

    kind: str
    capability_required: str
    target_paths: list[str]


@dataclass(frozen=True)
class _NonConfigPlanSpec:
    """Immutable approved identity for one non-config self-improvement run."""

    schema_version: int
    project_id: str
    kind: str
    title: str
    description: str
    worktree_path: str

    def __post_init__(self) -> None:
        """Reject ambiguous values before an approval artifact is persisted."""
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != _NON_CONFIG_PLAN_SCHEMA_VERSION
        ):
            raise ValueError("non-config plan schema version is unsupported")
        string_fields = {
            "project_id": self.project_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "worktree_path": self.worktree_path,
        }
        if any(not isinstance(value, str) for value in string_fields.values()):
            raise ValueError("non-config plan string fields are malformed")
        if (
            not self.project_id
            or self.project_id != self.project_id.strip()
            or len(self.project_id.encode("utf-8")) > 32
        ):
            raise ValueError("non-config plan project identity is malformed")
        if not self.kind.strip() or self.kind in _CONFIG_TIER_KINDS:
            raise ValueError("non-config plan kind is malformed")
        if not self.title.strip():
            raise ValueError("non-config plan title is malformed")
        if "\x00" in self.worktree_path or not Path(self.worktree_path).is_absolute():
            raise ValueError("non-config plan worktree path is not canonical")

    def to_json(self) -> str:
        """Serialize the exact approved fields in one canonical representation."""
        return json.dumps(
            {
                "description": self.description,
                "kind": self.kind,
                "project_id": self.project_id,
                "schema_version": self.schema_version,
                "title": self.title,
                "worktree_path": self.worktree_path,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(
        cls,
        raw: object,
        *,
        expected_project_id: str,
    ) -> _NonConfigPlanSpec:
        """Parse an exact canonical artifact bound to its immutable project row."""
        if not isinstance(raw, str) or not raw:
            raise ValueError("non-config approval plan artifact is missing")
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("non-config approval plan artifact is malformed") from exc
        if not isinstance(value, dict) or set(value) != _NON_CONFIG_PLAN_FIELDS:
            raise ValueError("non-config approval plan fields are malformed")
        if type(value["schema_version"]) is not int or any(
            not isinstance(value[field], str)
            for field in _NON_CONFIG_PLAN_FIELDS - {"schema_version"}
        ):
            raise ValueError("non-config approval plan field types are malformed")
        plan = cls(
            schema_version=value["schema_version"],
            project_id=cast(str, value["project_id"]),
            kind=cast(str, value["kind"]),
            title=cast(str, value["title"]),
            description=cast(str, value["description"]),
            worktree_path=cast(str, value["worktree_path"]),
        )
        if plan.project_id != expected_project_id:
            raise ValueError("non-config approval project identity drifted")
        if raw != plan.to_json():
            raise ValueError("non-config approval plan artifact is not canonical")
        return plan


class _ConfigTierCapabilityChecker:
    """Grants the config-tier capability (``config_write``) only.

    Implements the ``CapabilityChecker`` Protocol from
    :mod:`general_ludd.self_update.applier`. Fail-closed: anything beyond the
    config-tier grant is denied.
    """

    _ALLOWED: frozenset[str] = frozenset({_CONFIG_TIER_CAPABILITY})

    def allows(self, capability: str) -> bool:
        return capability in self._ALLOWED


def _get_session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession] | None:
    return getattr(app.state, "_session_factory", None)


def _resolve_non_config_project_repo(app: FastAPI, project_id: str) -> Path:
    """Resolve one stored project identity to an existing repository root."""
    if (
        not isinstance(project_id, str)
        or not project_id
        or project_id != project_id.strip()
        or len(project_id.encode("utf-8")) > 32
    ):
        raise HTTPException(
            status_code=422,
            detail="non-config self-improve approval has an invalid project identity",
        )
    manager = getattr(app.state, "_project_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=422,
            detail="non-config self-improve project workspace is unavailable",
        )
    try:
        project = manager.get_project(project_id)
        workspace_path = getattr(project, "workspace_path", "") if project else ""
        if not isinstance(workspace_path, str) or not workspace_path:
            raise ValueError("project workspace is missing")
        from general_ludd.projects.workspace import ProjectWorkspace

        workspace = ProjectWorkspace(
            project_id=project_id,
            workspace_path=workspace_path,
        )
        repo_root = workspace.repo_dir.resolve(strict=True)
        if not repo_root.is_dir():
            raise ValueError("project repository is not a directory")
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="non-config self-improve project workspace is unavailable",
        ) from exc
    return repo_root


def _confine_non_config_worktree(raw: object, repo_root: Path) -> str:
    """Return an existing canonical worktree confined to ``repo_root``."""
    if not isinstance(raw, str) or not raw or raw != raw.strip() or "\x00" in raw:
        raise ValueError("non-config approval worktree path is missing or malformed")
    try:
        canonical_root = repo_root.resolve(strict=True)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = canonical_root / candidate
        canonical_candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("non-config approval worktree path does not exist") from exc
    if not canonical_root.is_dir() or not canonical_candidate.is_dir():
        raise ValueError("non-config approval worktree path is not a directory")
    if not canonical_candidate.is_relative_to(canonical_root):
        raise ValueError("non-config approval worktree escapes its project workspace")
    return str(canonical_candidate)


_MAX_PRIORITY: int = 1000


def _coerce_priority(raw: object) -> int:
    if isinstance(raw, bool):  # bool is an int subclass; treat as unset
        return _PRIORITY_MAP["medium"]
    if isinstance(raw, int):
        return min(raw, _MAX_PRIORITY)
    return _PRIORITY_MAP.get(str(raw).lower(), _PRIORITY_MAP["medium"])


async def _persist_gated_self_improve_todos(
    repo: TodoRepository, todos: list[dict[str, object]]
) -> list[str]:
    """Persist harness-generated todos behind the self-improve human-approval gate.

    Mirrors ``EventLoop._persist_self_improve_todos``: each admitted todo is
    stamped ``work_type="self_improve"`` and parked in ``APPROVAL_REQUIRED``
    instead of landing at the default ``BACKLOG`` and flowing straight into
    normal promotion. That makes them visible to ``/admin/self-improve/approvals``
    and blocks silent execution of self-authored work (task #22, C13). Nothing
    is auto-executed. auto_queue was removed (C13 bypass).
    """
    gate = SelfImproveGate()  # max_open=10, always APPROVAL_REQUIRED
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
    app: FastAPI,
    kind: str,
    payload: dict[str, object],
    workspace_root: Path | None = None,
) -> dict[str, object]:
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
    return await _apply_approved_config_change(factory, str(approval_id), workspace_root=workspace_root)


async def _enqueue_config_change(
    factory: async_sessionmaker[AsyncSession],
    kind: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Record an APPROVAL_REQUIRED self-improve todo capturing the config change.

    The change spec (kind, capability, target paths, content) is serialized into
    ``plan_artifact`` so the eventual apply writes exactly what a human reviewed
    — the request that triggers the release cannot substitute different content.
    """
    # A human-meaningful reason, captured now so the eventual change-export
    # record explains WHY the file changed (falls back to a generic label).
    reason = (
        str(payload.get("title", "")).strip()
        or str(payload.get("description", "")).strip()
        or f"self-improve config write ({kind})"
    )
    target_paths = list(cast(Iterable[str], payload.get("target_paths", [])))
    spec: dict[str, object] = {
        "kind": kind,
        "capability_required": str(
            payload.get("capability_required", _CONFIG_TIER_CAPABILITY)
        ),
        "target_paths": target_paths,
        "change_content": str(payload.get("change_content", "")),
        "reason": reason,
    }
    targets = ", ".join(target_paths) or kind
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
    factory: async_sessionmaker[AsyncSession],
    approval_id: str,
    workspace_root: Path | None = None,
) -> dict[str, object]:
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
            spec = cast(dict[str, object], json.loads(todo.plan_artifact or "{}"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} has a malformed change spec",
            ) from exc

        resolved_workspace_root = workspace_root if workspace_root is not None else Path.cwd()

        # Change-export recorder (task #47): populate the ChangeRecordStore on
        # every successful config-tier write so `core-changes list/commit` has
        # something to export. Least-invasive + fail-soft — AtomicSafeWriter
        # swallows any exception this raises, so recording never breaks a write.
        reason = str(spec.get("reason", "")) or (
            f"self-improve config write ({spec.get('kind', 'config')})"
        )
        change_type = str(spec.get("kind", "config"))

        def _recorder(file_path: str, old: bytes | None, new: str) -> None:
            # Never record compiled artefacts / VCS internals / on-disk DBs.
            if _excluded(file_path):
                return
            store = ChangeRecordStore(
                store_dir=os.environ.get("GL_CHANGE_STORE_DIR", "")
            )
            store.record(
                file_path,
                change_type=change_type,
                reason=reason,
                old_content=old,
                new_content=new,
                signer="self_improve_apply",
            )

        safe_writer = AtomicSafeWriter(
            workspace_root=resolved_workspace_root, recorder=_recorder
        )
        applier = UpdateApplier(
            writer=safe_writer,
            capability_checker=_ConfigTierCapabilityChecker(),
            workspace_root=resolved_workspace_root,
        )
        plan = _ConfigTierPlan(
            kind=str(spec.get("kind", "config")),
            capability_required=str(
                spec.get("capability_required", _CONFIG_TIER_CAPABILITY)
            ),
            target_paths=list(cast(Iterable[str], spec.get("target_paths", []))),
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


async def _enqueue_non_config_change(
    factory: async_sessionmaker[AsyncSession],
    kind: str,
    payload: dict[str, object],
    *,
    project_id: str,
    repo_root: Path,
) -> dict[str, object]:
    """Enqueue an immutable project-bound non-config approval plan (C13).

    No execution happens without a human approval_id. The record is created
    with work_type=self_improve and status=APPROVAL_REQUIRED; a human must
    approve it via /admin/self-improve/approvals before re-POSTing with the
    approval_id to execute.
    """
    title = (
        str(payload.get("title", "")).strip() or f"self-improve {kind} change"
    )[:512]
    desc = str(payload.get("description", ""))
    try:
        worktree_path = _confine_non_config_worktree(
            payload.get("worktree_path"),
            repo_root,
        )
        spec = _NonConfigPlanSpec(
            schema_version=_NON_CONFIG_PLAN_SCHEMA_VERSION,
            project_id=project_id,
            kind=kind,
            title=title,
            description=desc,
            worktree_path=worktree_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="non-config self-improve request has an invalid plan artifact",
        ) from exc
    async with factory() as session:
        repo = TodoRepository(session)
        created = await repo.create(
            {
                "project_id": spec.project_id,
                "title": spec.title,
                "description": desc or f"Self-improve {kind} change awaiting human approval",
                "status": TodoStatus.APPROVAL_REQUIRED.value,
                "work_type": SELF_IMPROVE_WORK_TYPE,
                "priority": _PRIORITY_MAP["high"],
                "created_by": "self_improve_apply",
                "plan_artifact": spec.to_json(),
            }
        )
        approval_id = created.todo_id
        await session.commit()
    return {
        "tier": kind,
        "status": "approval_required",
        "approval_id": approval_id,
    }


async def _apply_approved_non_config_change(
    app: FastAPI,
    factory: async_sessionmaker[AsyncSession],
    approval_id: str,
) -> dict[str, object]:
    """Execute only the released todo's immutable project-bound plan."""
    async with factory() as session:
        repo = TodoRepository(session)
        approval_todo = await repo.get_by_id(approval_id)
        if approval_todo is None:
            raise HTTPException(
                status_code=404,
                detail=f"approval {approval_id} not found",
            )
        if getattr(approval_todo, "work_type", None) != SELF_IMPROVE_WORK_TYPE:
            raise HTTPException(
                status_code=409,
                detail=f"approval {approval_id} is not a self-improve record",
            )
        if approval_todo.status != TodoStatus.QUEUED.value:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"approval {approval_id} is not released "
                    f"(status={approval_todo.status}); a human must approve it first"
                ),
            )
        stored_project_id = getattr(approval_todo, "project_id", None)
        if not isinstance(stored_project_id, str):
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} has a malformed plan artifact",
            )
        try:
            spec = _NonConfigPlanSpec.from_json(
                getattr(approval_todo, "plan_artifact", None),
                expected_project_id=stored_project_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} has a malformed plan artifact",
            ) from exc
        repo_root = _resolve_non_config_project_repo(app, spec.project_id)
        try:
            worktree_path = _confine_non_config_worktree(
                spec.worktree_path,
                repo_root,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} has an invalid worktree artifact",
            ) from exc
        if worktree_path != spec.worktree_path:
            raise HTTPException(
                status_code=422,
                detail=f"approval {approval_id} worktree identity drifted",
            )
        await session.commit()

    from general_ludd.reload.self_improve import SelfImprovementWorkflow

    workflow = SelfImprovementWorkflow()
    validation = await asyncio.to_thread(workflow.validate_improvement, worktree_path)
    apply_result = await asyncio.to_thread(
        workflow.apply_improvement,
        approval_id,
        validation,
    )
    reload_result = await asyncio.to_thread(workflow.reload_if_needed, apply_result)

    if apply_result.applied:
        async with factory() as session:
            repo = TodoRepository.scoped(session, spec.project_id)
            approved = await repo.get_by_id(approval_id)
            if approved is not None:
                active = await repo.transition(
                    approval_id,
                    TodoStatus.ACTIVE,
                    expected_version=approved.version,
                )
                await repo.transition(
                    approval_id,
                    TodoStatus.COMPLETE,
                    expected_version=active.version,
                )
                await session.commit()

    return {
        "todo_id": approval_id,
        "validation_passed": validation.success,
        "applied": apply_result.applied,
        "reload_needed": apply_result.reload_needed,
        "reload_status": reload_result.status,
    }


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register self-improvement administration routes on ``app``."""

    @app.post("/admin/self-improve/analyze")
    async def admin_self_improve_analyze() -> dict[str, object]:
        harness = SelfImprovementHarness()
        findings = harness.run_gap_analysis()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": findings,
            "findings_count": len(findings),
        }
        return {"findings": findings, "findings_count": len(findings)}

    @app.post("/admin/self-improve/run")
    async def admin_self_improve_run() -> dict[str, object]:
        harness = SelfImprovementHarness()
        result = harness.run_full_cycle()
        _daemon_state["self_improve_last_analysis"] = {
            "findings": result.get("findings", []),
            "findings_count": result.get("findings_count", 0),
            "todos_enqueued": result.get("todos_enqueued", 0),
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
            cast(list[object], _daemon_state["todos"]).extend(result.get("todos", []))
        return result

    @app.post("/admin/self-improve/apply")
    async def admin_self_improve_apply(payload: dict[str, object]) -> dict[str, object]:
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
            project_id = str(payload.get("project_id", "") or "")
            workspace_root: Path | None = None
            if project_id:
                pm = getattr(app.state, "_project_manager", None)
                if pm is not None:
                    proj = pm.get_project(project_id)
                    if proj is not None and proj.workspace_path:
                        from general_ludd.projects.workspace import ProjectWorkspace

                        ws = ProjectWorkspace(
                            project_id=project_id,
                            workspace_path=proj.workspace_path,
                        )
                        workspace_root = ws.repo_dir
            return await _config_tier_apply(app, kind, payload, workspace_root=workspace_root)

        # Non-config-tier path: gate through SelfImproveGate (C13).
        # Same two-step flow as config-tier:
        #   1. No approval_id -> ENQUEUE an APPROVAL_REQUIRED record (no execution)
        #   2. approval_id referencing a human-RELEASED record -> execute
        factory = _get_session_factory(app)
        if factory is None:
            raise HTTPException(
                status_code=503,
                detail="non-config self-improve apply requires the approval database",
            )
        approval_id = payload.get("approval_id")
        if not approval_id:
            project_id = str(payload.get("project_id", "") or "")
            repo_root = _resolve_non_config_project_repo(app, project_id)
            return await _enqueue_non_config_change(
                factory,
                kind,
                payload,
                project_id=project_id,
                repo_root=repo_root,
            )

        return await _apply_approved_non_config_change(
            app,
            factory,
            str(approval_id),
        )

    @app.get("/admin/self-improve/status")
    async def admin_self_improve_status() -> dict[str, object]:
        last = _daemon_state.get("self_improve_last_analysis")
        if last is None:
            return {"status": "never_run", "findings_count": 0}
        return {"status": "completed", **cast(dict[str, object], last)}

    # Human approval gate for self-authored self-improve todos.
    #
    # All admitted self-improve todos are parked in APPROVAL_REQUIRED
    # (self-modification approval bypass otherwise). These routes are the WIRED
    # release path: without them held todos would strand forever. auto_queue
    # was removed (C13 bypass).
    # ------------------------------------------------------------------

    def _todo_view(todo: object) -> dict[str, object]:
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
    async def admin_self_improve_list_approvals() -> dict[str, object]:
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
    async def admin_self_improve_approve(todo_id: str) -> dict[str, object]:
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
        todo_id: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
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
