"""Exact feature-context assembly for codified decision lookup."""

from __future__ import annotations

from general_ludd.decision_codification.schema import (
    DecisionContextV1,
    DecisionRuleBundleV1,
)

_RESERVED_RUNTIME_FEATURES: dict[str, object] = {"codification_known": True}


def build_rule_context(
    context_input: DecisionContextV1,
    bundle: DecisionRuleBundleV1,
) -> dict[str, object] | None:
    """Merge exact guards and features, refusing collisions or missing inputs."""
    context: dict[str, object] = dict(context_input.exact_guards)
    for name, value in context_input.features.items():
        if name in context and context[name] != value:
            return None
        context[name] = value
    for name, reserved_value in _RESERVED_RUNTIME_FEATURES.items():
        if any(node.feature_id == name for node in bundle.nodes):
            context[name] = reserved_value
    if any(node.feature_id not in context for node in bundle.nodes):
        return None
    return context
