"""Task-routing helpers shared by the event loop and compatibility callers."""

from __future__ import annotations

import json
import logging
from typing import Any

from general_ludd.schemas.benchmark import TaskType

logger = logging.getLogger(__name__)

TOOL_USE_WORK_TYPES: frozenset[str] = frozenset(
    {"analysis", "audit", "code", "bug_fix", "refactor", "feature", "test"}
)
CODE_WORK_TYPES: frozenset[str] = frozenset(
    {"code", "bug_fix", "refactor", "feature", "test"}
)

WORK_TYPE_TASK_TYPE_MAP: dict[str, str] = {
    "bug_fix": "bug_fix",
    "code": "feature",
    "test": "test_write",
    "review": "code_review",
    "refactor": "refactor",
    "docs": "documentation",
    "infra": "feature",
    "prompt": "feature",
    "analysis": "feature",
    "audit": "feature",
    "release": "feature",
    "dependency": "feature",
    "security": "security_fix",
    "model": "feature",
    "unknown": "feature",
    "model_decision": "feature",
    "langgraph_generate": "feature",
    "enforcement_gate": "feature",
    "enforcement_gate_push_guard": "feature",
    "enforcement_gate_check": "feature",
}

WORK_TYPE_PLAYBOOK_MAP: dict[str, str] = {
    "code": "validate_task.yml",
    "test": "molecule_test.yml",
    "analysis": "gap_analysis.yml",
    "audit": "log_audit.yml",
    "prompt": "prompt_eval.yml",
    "self_improve": "self_improve_harness.yml",
    "dependency": "dependency_update.yml",
    "review": "return_review.yml",
    "docs": "noop.yml",
    "infra": "noop.yml",
    "security": "noop.yml",
    "model": "noop.yml",
    "release": "noop.yml",
    "model_decision": "langgraph_decide.yml",
    "langgraph_generate": "langchain_generate.yml",
    "enforcement_gate": "enforcement_gate.yml",
    "enforcement_gate_push_guard": "enforcement_gate.yml",
    "enforcement_gate_check": "enforcement_gate.yml",
}

_RESOURCE_BASE_COST: dict[str, float] = {
    "low_resource": 0.05,
    "medium_resource": 0.25,
    "high_resource": 1.0,
}


def format_acceptance_criteria(raw_ac: str | None) -> str:
    """Render stored JSON criteria as prompt-ready Markdown."""
    if not raw_ac:
        return ""
    try:
        parsed = json.loads(raw_ac)
    except (json.JSONDecodeError, TypeError):
        return raw_ac
    if not isinstance(parsed, list):
        return raw_ac
    if not parsed:
        return ""
    return "\n".join(f"- {criterion}" for criterion in parsed)


def self_update_work_item_from_todo(todo: Any, todo_id: str) -> Any:
    """Build a scheduler work item for a self-update queue todo."""
    from general_ludd.self_update.model import ApplyTier
    from general_ludd.self_update.priority import work_item_for_tier

    tags = getattr(todo, "tags", None) or []
    tier_value = ""
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("tier:"):
            tier_value = tag.split(":", 1)[1].strip()
            break
    try:
        tier = ApplyTier(tier_value)
    except ValueError:
        tier = ApplyTier.REFUSED
    return work_item_for_tier(tier, todo_id)


def resolve_prompt_text_static(
    prompt_registry: Any,
    prompt_profile: str | None,
    **kwargs: object,
) -> str | None:
    """Resolve a project template before falling back to the registry."""
    project_templates_dir: object = kwargs.pop("project_templates_dir", None)
    if not prompt_profile:
        return None
    if project_templates_dir is not None:
        from pathlib import Path

        template_path = Path(str(project_templates_dir)) / prompt_profile
        if template_path.is_file():
            try:
                from jinja2 import FileSystemLoader
                from jinja2.sandbox import SandboxedEnvironment

                environment = SandboxedEnvironment(
                    loader=FileSystemLoader(str(project_templates_dir)),
                    autoescape=True,
                )
                return environment.get_template(prompt_profile).render(**kwargs)
            except Exception:
                logger.debug(
                    "Jinja project-template render failed for profile %r; "
                    "falling through to registry render",
                    prompt_profile,
                    exc_info=True,
                )
    if prompt_registry is None:
        return None
    try:
        result: str = prompt_registry.render(prompt_profile, **kwargs)
        return result
    except Exception:
        logger.warning(
            "Registry render failed for prompt profile %r; returning no prompt text",
            prompt_profile,
            exc_info=True,
        )
        return None


def work_type_to_task_type(work_type: str) -> TaskType:
    """Map a work type onto the stable task taxonomy."""
    mapped = WORK_TYPE_TASK_TYPE_MAP.get(work_type, "feature")
    try:
        return TaskType(mapped)
    except ValueError:
        return TaskType.FEATURE


def playbook_for_work_type(
    work_type: str,
    default: str = "noop.yml",
    *,
    project_id: str | None = None,
    workspaces: dict[str, Any] | None = None,
) -> str:
    """Resolve a project override or the canonical work-type playbook."""
    workspace = workspaces.get(project_id) if workspaces and project_id else None
    if workspace is not None and hasattr(workspace, "playbooks_dir"):
        from pathlib import Path

        playbook_path = Path(workspace.playbooks_dir) / f"{work_type}.yml"
        if playbook_path.is_file():
            return str(playbook_path)
    return WORK_TYPE_PLAYBOOK_MAP.get(work_type, default)


def compute_todo_estimate(todo: object) -> float:
    """Estimate todo cost from its resource profile and confidence."""
    resource_profile: str = (
        getattr(todo, "resource_profile", "low_resource") or "low_resource"
    )
    confidence: float | None = getattr(todo, "confidence", None)
    base_cost = _RESOURCE_BASE_COST.get(resource_profile, 0.05)
    effective_confidence = 0.5 if confidence is None else float(confidence)
    return round(base_cost * (1.5 - effective_confidence), 4)
