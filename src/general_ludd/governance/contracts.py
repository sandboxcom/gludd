"""Governance contracts — policy, rule, and compliance dataclass models.

Defines the formal contracts for governance policies, their constituent rules,
compliance tracking, and a policy registry for storage and lookup. Callers
depend on these contracts, not on concrete data modules.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(eq=True)
class GovernancePolicy:
    """A governance policy with metadata about domain, level, and status."""

    name: str
    level: str
    description: str
    domain: str
    status: str = "draft"
    effective_date: str | None = None


@dataclass(eq=True)
class GovernanceRule:
    """A rule within a governance policy, defining a condition and action.

    Each rule belongs to a policy (referenced by ``policy_name``).  Rules
    carry a priority (higher = more important) and an enforcement level
    indicating how strictly the rule is applied.
    """

    policy_name: str
    rule_id: str
    condition: str
    action: str
    priority: int = 0
    enforcement: str = "advisory"


@dataclass(eq=True)
class ComplianceModel:
    """Tracks compliance of a subject against a policy.

    Records which requirements have been met, which remain unmet, and
    provides an audit trail of compliance checks over time.
    """

    subject: str
    policy_name: str
    compliance_status: str
    requirements_met: list[str] = field(default_factory=list)
    requirements_unmet: list[str] = field(default_factory=list)
    audit_trail: list[str] = field(default_factory=list)
    last_reviewed: str | None = None


@dataclass(eq=True, unsafe_hash=True)
class Policy:
    """A governance policy with metadata about domain, level, and status.

    Distinct from ``GovernancePolicy`` — this is the expert-level contract
    used by ``PolicyEngine`` and ``ComplianceChecker``.
    """

    name: str
    description: str
    domain: str
    level: str
    status: str = "draft"
    effective_date: str | None = None
    rules: list[str] = field(default_factory=list, hash=False, compare=False)


@dataclass(eq=True, unsafe_hash=True)
class Rule:
    """A rule within a governance policy, defining a condition and action.

    Distinct from ``GovernanceRule`` — this is the expert-level contract
    used by ``PolicyEngine`` and ``ComplianceChecker``.
    """

    policy_name: str
    rule_id: str
    condition: str
    action: str
    priority: int = 0
    enforcement: str = "advisory"


@dataclass(eq=True, unsafe_hash=True)
class AuditTrail:
    """An immutable audit trail entry recording a governance action.

    Tracks who acted on what, when, and with what details.
    """

    entry_id: str
    subject: str
    action: str
    details: str
    timestamp: str


@dataclass(eq=True)
class ComplianceReport:
    """Reports compliance of a subject against policies.

    Includes violation details and an auto-generated audit trail.
    Distinct from ``ComplianceModel``.
    """

    subject: str
    policy_name: str
    status: str
    violations: list[str] = field(default_factory=list)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    audit_trail: list[AuditTrail] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return self.status == "compliant"


class PolicyRegistry:
    """In-memory registry of governance policies and their rules.

    Provides add/get/remove/list operations for policies and rules.
    Iterating the registry yields policies in insertion order.
    """

    def __init__(self) -> None:
        self._policies: dict[str, GovernancePolicy] = {}
        self._rules: dict[str, list[GovernanceRule]] = {}

    def add_policy(self, policy: GovernancePolicy) -> None:
        if policy.name in self._policies:
            raise ValueError(f"Policy '{policy.name}' already exists")
        self._policies[policy.name] = policy
        self._rules.setdefault(policy.name, [])

    def get_policy(self, name: str) -> GovernancePolicy | None:
        return self._policies.get(name)

    def remove_policy(self, name: str) -> bool:
        if name in self._policies:
            del self._policies[name]
            self._rules.pop(name, None)
            return True
        return False

    def add_rule(self, rule: GovernanceRule) -> None:
        if rule.policy_name not in self._policies:
            raise KeyError(rule.policy_name)
        self._rules.setdefault(rule.policy_name, []).append(rule)

    def get_rules(self, policy_name: str) -> list[GovernanceRule]:
        return list(self._rules.get(policy_name, []))

    def list_policies(
        self,
        domain: str | None = None,
        level: str | None = None,
    ) -> list[GovernancePolicy]:
        results = list(self._policies.values())
        if domain is not None:
            results = [p for p in results if p.domain == domain]
        if level is not None:
            results = [p for p in results if p.level == level]
        return results

    def __len__(self) -> int:
        return len(self._policies)

    def __iter__(self) -> Iterator[GovernancePolicy]:
        return iter(self._policies.values())

    def __contains__(self, name: str) -> bool:
        return name in self._policies


__all__ = [
    "AuditTrail",
    "ComplianceModel",
    "ComplianceReport",
    "GovernancePolicy",
    "GovernanceRule",
    "Policy",
    "PolicyRegistry",
    "Rule",
]
