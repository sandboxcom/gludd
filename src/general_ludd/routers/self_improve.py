from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from general_ludd.db.repository import TodoRepository
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_update.applier import UpdateApplier
from general_ludd.self_update.safe_writer import AtomicSafeWriter

# Kinds routed through the config-tier UpdateApplier path. ``role`` and ``code``
# tiers are handled elsewhere (Phase 4 wires code-tier hot rotation).
_CONFIG_TIER_KINDS: frozenset[str] = frozenset({"config", "yaml"})
_CONFIG_TIER_CAPABILITY: str = "config_write"


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
                persisted_ids: list[str] = []
                for todo_data in result["todos"]:
                    created = await repo.create(todo_data=todo_data)
                    persisted_ids.append(created.todo_id)
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
        if kind in _CONFIG_TIER_KINDS:
            workspace_root = Path.cwd()
            safe_writer = AtomicSafeWriter(workspace_root=workspace_root)
            applier = UpdateApplier(
                writer=safe_writer,
                capability_checker=_ConfigTierCapabilityChecker(),
                workspace_root=workspace_root,
            )
            plan = _ConfigTierPlan(
                kind=kind,
                capability_required=str(
                    payload.get("capability_required", _CONFIG_TIER_CAPABILITY)
                ),
                target_paths=list(payload.get("target_paths", [])),
            )
            result = applier.apply(plan, str(payload.get("change_content", "")))
            return {
                "tier": "config",
                "status": result.status,
                "target_paths": result.target_paths,
                "evidence": result.evidence,
            }

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
        validation = workflow.validate_improvement(worktree_path)
        apply_result = workflow.apply_improvement(todo["todo_id"], validation)
        reload_result = workflow.reload_if_needed(apply_result)
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
