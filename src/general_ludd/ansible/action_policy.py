"""Ansible action policy configuration and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from general_ludd.ansible.isolation import ProcessIsolationConfig


class ActionPolicyConfig(BaseModel):
    enabled: bool = True
    default_mode: Literal["allow", "deny"] = "allow"
    validate_before_run: bool = True
    audit_after_run: bool = True
    deny_unknown_playbooks: bool = False
    disabled_playbooks: list[str] = Field(default_factory=list)
    disabled_roles: list[str] = Field(default_factory=list)
    disabled_collections: list[str] = Field(default_factory=list)
    disabled_modules: list[str] = Field(default_factory=list)
    process_isolation: ProcessIsolationConfig | None = None


@dataclass
class ActionManifest:
    playbook: str
    roles: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class PolicyResult:
    allowed: bool
    denied_items: list[str] = field(default_factory=list)
    reason: str = ""


def validate_action(
    policy: ActionPolicyConfig, manifest: ActionManifest
) -> PolicyResult:
    if not policy.enabled:
        return PolicyResult(allowed=True)

    denied: list[str] = []
    reasons: list[str] = []

    if policy.default_mode == "deny":
        return PolicyResult(
            allowed=False,
            denied_items=[manifest.playbook],
            reason="Default mode is deny",
        )

    if manifest.playbook in policy.disabled_playbooks:
        denied.append(manifest.playbook)
        reasons.append(f"Playbook '{manifest.playbook}' is disabled")

    for role in manifest.roles:
        if role in policy.disabled_roles:
            denied.append(role)
            reasons.append(f"Role '{role}' is disabled")

    for collection in manifest.collections:
        if collection in policy.disabled_collections:
            denied.append(collection)
            reasons.append(f"Collection '{collection}' is disabled")

    for module in manifest.modules:
        if module in policy.disabled_modules:
            denied.append(module)
            reasons.append(f"Module '{module}' is disabled")

    if policy.process_isolation and policy.process_isolation.enabled:
        for module in manifest.modules:
            if policy.process_isolation.is_module_blocked(module):
                denied.append(module)
                reasons.append(
                    f"Module '{module}' blocked by process isolation"
                )

    if denied:
        return PolicyResult(
            allowed=False,
            denied_items=denied,
            reason="; ".join(reasons),
        )

    return PolicyResult(allowed=True)
