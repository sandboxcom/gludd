"""Rules engine for deterministic policy overlays."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Rule(BaseModel):
    rule_id: str
    enabled: bool = True
    priority: int = 100
    scope: str = "global"
    condition: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    audit_message: str = ""


class RuleEngine:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = rules or []

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
            if value is not None:
                return bool(ctx_value in value)
            return False
        if op == "contains":
            return bool(value in ctx_value)
        return False

    @staticmethod
    def _resolve_field(field: str, context: dict[str, Any]) -> Any:
        parts = field.split(".")
        obj: Any = context
        for part in parts:
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        return obj
