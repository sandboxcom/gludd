"""Rules engine for deterministic policy overlays."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ActionType(StrEnum):
    ROUTE = "route"
    PAUSE_QUEUE = "pause_queue"
    REDUCE_BUCKETS = "reduce_buckets"
    SET_MODEL_PROFILE = "set_model_profile"
    SET_PROMPT_PROFILE = "set_prompt_profile"
    SET_QUALITY_THRESHOLD = "set_quality_threshold"
    ENABLE_ADAPTIVE_ROUTING = "enable_adaptive_routing"


class Rule(BaseModel):
    rule_id: str
    enabled: bool = True
    priority: int = 100
    scope: str = "global"
    condition: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    audit_message: str = ""

    @field_validator("rule_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("rule_id must not be empty")
        return v


class RuleAction(BaseModel):
    rule_id: str
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    audit_message: str = ""

    @field_validator("rule_id", "action_type", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v


def apply_rule_actions(
    actions: list[dict[str, Any]],
    todo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply rule actions to a todo or context, returning modifications.

    Accepts both flattened format (type, profile_id) and event_loop format
    (action_type, params.profile_id).
    """
    overrides: dict[str, Any] = {}
    for action in actions:
        action_type = action.get("type") or action.get("action_type", "")
        params = action.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if action_type == ActionType.SET_MODEL_PROFILE:
            profile_id = action.get("profile_id") or params.get("profile_id")
            if profile_id and isinstance(profile_id, str):
                overrides["model_profile"] = profile_id
        elif action_type == ActionType.SET_PROMPT_PROFILE:
            profile_id = action.get("profile_id") or params.get("profile_id")
            if profile_id and isinstance(profile_id, str):
                overrides["prompt_profile"] = profile_id
        elif action_type == ActionType.SET_QUALITY_THRESHOLD:
            value = action.get("value") or params.get("value")
            if isinstance(value, (int, float)):
                overrides["quality_threshold"] = float(value)
        elif action_type == ActionType.ENABLE_ADAPTIVE_ROUTING:
            value = action.get("value", params.get("value", True))
            overrides["enable_adaptive_routing"] = bool(value)
    return overrides


class RuleEngine:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = rules or []
        self._rules.sort(key=lambda r: r.priority)

    def add_rule(self, rule: Rule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

    def evaluate(
        self, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for rule in self._rules:
            if not rule.enabled:
                continue
            if self._matches(rule.condition, context):
                results.append({
                    "rule_id": rule.rule_id,
                    "actions": rule.actions,
                    "audit_message": rule.audit_message,
                })
        return results

    def _matches(
        self, condition: dict[str, Any], context: dict[str, Any]
    ) -> bool:
        if "all" in condition:
            return all(self._eval_cond(c, context) for c in condition["all"])
        if "any" in condition:
            return any(self._eval_cond(c, context) for c in condition["any"])
        if condition:
            return self._eval_cond(condition, context)
        return True

    def _eval_cond(
        self, cond: dict[str, Any], context: dict[str, Any]
    ) -> bool:
        field = cond.get("field", "")
        op = cond.get("op", "eq")
        value = cond.get("value")
        ctx_value = self._resolve_field(field, context)
        if op == "eq":
            return bool(ctx_value == value)
        if op == "neq":
            return bool(ctx_value != value)
        if op == "in":
            if value is not None and ctx_value is not None:
                return bool(ctx_value in value)
            return False
        if op == "contains":
            if ctx_value is not None:
                return bool(value in ctx_value)
            return False
        if op == "gt":
            if ctx_value is None:
                return False
            return bool(ctx_value > value)
        if op == "lt":
            if ctx_value is None:
                return False
            return bool(ctx_value < value)
        if op == "gte":
            if ctx_value is None:
                return False
            return bool(ctx_value >= value)
        if op == "lte":
            if ctx_value is None:
                return False
            return bool(ctx_value <= value)
        return False

    @staticmethod
    def _resolve_field(field: str, context: dict[str, Any]) -> Any:
        parts = field.split(".")
        obj: Any = context
        for part in parts:
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        return obj


def evaluate_rules(
    rules: list[Rule], context: dict[str, Any],
) -> list[RuleAction]:
    engine = RuleEngine(rules=rules)
    raw = engine.evaluate(context)
    queue_name = ""
    q = context.get("queue")
    if isinstance(q, dict):
        queue_name = q.get("queue_name", "")

    actions: list[RuleAction] = []
    for result in raw:
        for action_dict in result["actions"]:
            params = {k: v for k, v in action_dict.items() if k != "type"}
            if queue_name and "queue" not in params:
                params["queue"] = queue_name
            actions.append(RuleAction(
                rule_id=result["rule_id"],
                action_type=action_dict.get("type", "unknown"),
                params=params,
                audit_message=result.get("audit_message", ""),
            ))
    return actions


def default_rules() -> list[Rule]:
    return [
        Rule(
            rule_id="route_failing_validation_to_qa",
            priority=10,
            condition={"all": [
                {"field": "todo.status", "op": "eq", "value": "failed"},
                {"field": "todo.work_type", "op": "in", "value": ["test", "code", "review"]},
            ]},
            actions=[{"type": "route", "queue": "qa"}],
            audit_message="Routing failing validation to qa queue",
        ),
        Rule(
            rule_id="route_dependency_updates",
            priority=10,
            condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
            actions=[{"type": "route", "queue": "dependency"}],
            audit_message="Routing dependency update to dependency queue",
        ),
        Rule(
            rule_id="route_openbao_update_to_manual_hold",
            priority=5,
            condition={"all": [
                {"field": "todo.tags", "op": "contains", "value": "openbao"},
                {"field": "todo.tags", "op": "contains", "value": "image_update"},
            ]},
            actions=[{"type": "route", "queue": "manual_hold", "approval_required": True}],
            audit_message="Routing OpenBao image update to manual_hold with approval_required",
        ),
        Rule(
            rule_id="pause_queue_model_down",
            priority=5,
            condition={"field": "model_profile.status", "op": "eq", "value": "down"},
            actions=[{"type": "pause_queue"}],
            audit_message="Pausing queue because model profile is down",
        ),
        Rule(
            rule_id="reduce_local_heavy_high_load",
            priority=20,
            condition={"all": [
                {"field": "queue.resource_profile", "op": "eq", "value": "local_heavy"},
                {"field": "load_snapshot.load_ratio", "op": "gt", "value": 1.0},
            ]},
            actions=[{"type": "reduce_buckets", "reduction": 2}],
            audit_message="Reducing local-heavy buckets when 10m load exceeds target",
        ),
        Rule(
            rule_id="reduce_api_metered_budget_exhaustion",
            priority=20,
            condition={"all": [
                {"field": "queue.resource_profile", "op": "in", "value": ["ai_heavy"]},
                {"field": "budget.budget_near_exhaustion", "op": "eq", "value": True},
            ]},
            actions=[{"type": "reduce_buckets", "reduction": 2}],
            audit_message="Reducing API-metered buckets when budget near exhaustion",
        ),
    ]
