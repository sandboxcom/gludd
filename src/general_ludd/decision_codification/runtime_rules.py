"""Exact rule-engine adapter used by deterministic decision lookup."""

from __future__ import annotations

from typing import Protocol

from general_ludd.decision_codification.schema import DecisionRuleBundleV1
from general_ludd.rules.engine import Rule, RuleEngine


class RuleBundleAdapter(Protocol):
    """Adapter boundary for the existing deterministic rules engine."""

    def matching_leaf_ids(
        self, bundle: DecisionRuleBundleV1, context: dict[str, object]
    ) -> tuple[str, ...]:
        """Return unique matching exported leaf IDs."""
        ...


class RulesEngineAdapter:
    """Flatten exported tree paths into the existing exact Rule vocabulary."""

    def matching_leaf_ids(
        self, bundle: DecisionRuleBundleV1, context: dict[str, object]
    ) -> tuple[str, ...]:
        """Compile tree paths and return the uniquely matching leaf IDs."""
        nodes = {node.node_id: node for node in bundle.nodes}
        leaves = {leaf.leaf_id: leaf for leaf in bundle.leaves}
        compiled: list[Rule] = []
        rule_to_leaf: dict[str, str] = {}

        def walk(identifier: str, conditions: tuple[dict[str, object], ...]) -> None:
            leaf = leaves.get(identifier)
            if leaf is not None:
                rule_id = f"decision-path-{len(compiled)}"
                compiled.append(
                    Rule(
                        rule_id=rule_id,
                        priority=len(compiled),
                        scope=bundle.project_id,
                        condition={"all": list(conditions)},
                        actions=[{"type": "decision_leaf", "leaf_id": leaf.leaf_id}],
                    )
                )
                rule_to_leaf[rule_id] = leaf.leaf_id
                return
            node = nodes[identifier]
            match_op = "eq" if node.operator == "eq" else "neq"
            miss_op = "neq" if node.operator == "eq" else "eq"
            match: dict[str, object] = {
                "field": node.feature_id,
                "op": match_op,
                "value": node.value,
            }
            miss: dict[str, object] = {
                "field": node.feature_id,
                "op": miss_op,
                "value": node.value,
            }
            walk(node.match_id, (*conditions, match))
            walk(node.miss_id, (*conditions, miss))

        walk(bundle.root_id, ())
        matches = RuleEngine(compiled).evaluate(context)
        return tuple(sorted({rule_to_leaf[item["rule_id"]] for item in matches}))


__all__ = ["RuleBundleAdapter", "RulesEngineAdapter"]
