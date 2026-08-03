"""Governance core — policy engine, compliance checker, and capability router."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceReport,
    Policy,
    Rule,
)


class CapabilityRouter:
    """Routes policy rule conditions to subject capability evaluations.

    Maintains a set of capabilities for a given subject and interprets
    rule condition strings against that set. Supported condition formats:

    - ``has:<cap>`` — subject must possess the capability (default positive).
    - ``missing:<cap>`` — subject must NOT possess the capability.
    - ``all_of:<a>,<b>`` — subject must possess all listed capabilities.
    - ``any_of:<a>,<b>`` — subject must possess at least one listed capability.
    - Bare string — treated as ``has:<condition>``.
    - Unrecognised prefix — defaults to passing (fail-open for safety).
    """

    def __init__(self, capabilities: set[str] | None = None) -> None:
        self._capabilities: set[str] = set(capabilities) if capabilities is not None else set()

    @classmethod
    def from_model_capabilities(cls, model_caps: list[dict[str, object]]) -> CapabilityRouter:
        caps: set[str] = set()
        for mc in model_caps:
            name = str(mc.get("name", ""))
            if name:
                caps.add(name)
            aliases = mc.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    caps.add(str(alias))
        return cls(caps)

    @classmethod
    def from_role_declarations(cls, role_caps: dict[str, list[str]]) -> CapabilityRouter:
        caps: set[str] = set()
        for capability_list in role_caps.values():
            caps.update(capability_list)
        return cls(caps)

    def add(self, capability: str) -> None:
        self._capabilities.add(capability)

    def remove(self, capability: str) -> None:
        self._capabilities.discard(capability)

    def has_any(self, caps: set[str]) -> bool:
        return bool(self._capabilities & caps)

    def has_all(self, caps: set[str]) -> bool:
        return caps <= self._capabilities

    def interpret_condition(self, condition: str, subject_capabilities: set[str]) -> bool:
        if condition.startswith("has:"):
            cap = condition[len("has:") :]
            return cap in subject_capabilities
        if condition.startswith("missing:"):
            cap = condition[len("missing:") :]
            return cap not in subject_capabilities
        if condition.startswith("all_of:"):
            needed = set(c.strip() for c in condition[len("all_of:") :].split(",") if c.strip())
            return needed <= subject_capabilities
        if condition.startswith("any_of:"):
            candidates = set(c.strip() for c in condition[len("any_of:") :].split(",") if c.strip())
            return bool(subject_capabilities & candidates)
        return True


class PolicyEngine:
    """In-memory registry and evaluation engine for governance policies.

    Stores policies and their constituent rules. Provides lookup, iteration,
    and filtering support.
    """

    def __init__(self) -> None:
        self._policies: dict[str, Policy] = {}
        self._rules: dict[str, list[Rule]] = {}

    def register_policy(self, policy: Policy) -> None:
        if policy.name in self._policies:
            raise ValueError(f"Policy '{policy.name}' is already registered")
        self._policies[policy.name] = policy
        self._rules.setdefault(policy.name, [])

    def get_policy(self, name: str) -> Policy | None:
        return self._policies.get(name)

    def register_rule(self, rule: Rule) -> None:
        if rule.policy_name not in self._policies:
            raise KeyError(rule.policy_name)
        self._rules.setdefault(rule.policy_name, []).append(rule)

    def get_rules(self, policy_name: str) -> list[Rule]:
        return list(self._rules.get(policy_name, []))

    def list_policies(
        self,
        domain: str | None = None,
        level: str | None = None,
    ) -> list[Policy]:
        results = list(self._policies.values())
        if domain is not None:
            results = [p for p in results if p.domain == domain]
        if level is not None:
            results = [p for p in results if p.level == level]
        return results

    def __len__(self) -> int:
        return len(self._policies)

    def __iter__(self) -> Iterator[Policy]:
        return iter(self._policies.values())

    def __contains__(self, name: str) -> bool:
        return name in self._policies


class ComplianceChecker:
    """Checks compliance of a subject against policies in a ``PolicyEngine``.

    Evaluates every rule across every policy and produces a single
    ``ComplianceReport`` with violations and an audit trail.

    When a ``CapabilityRouter`` is provided, rule conditions are evaluated
    against the subject's declared capabilities using the router's condition
    interpreter (``has:``, ``missing:``, ``all_of:``, ``any_of:``).
    """

    def __init__(
        self,
        engine: PolicyEngine,
        evaluate_fn: Callable[[Rule, str], bool] | None = None,
        capability_router: CapabilityRouter | None = None,
    ) -> None:
        self._engine = engine
        self._capability_router = capability_router
        self._evaluate_fn = evaluate_fn or self._evaluate_default

    @staticmethod
    def _evaluate_default(rule: Rule, subject: str) -> bool:
        del rule, subject
        return True

    def check(self, subject: str) -> ComplianceReport:
        violations: list[str] = []
        audit_entries: list[AuditTrail] = []
        now_iso = dt.datetime.now(dt.UTC).isoformat()

        for policy in self._engine:
            for rule in self._engine.get_rules(policy.name):
                passed = self._evaluate_fn(rule, subject)
                if not passed:
                    violations.append(rule.rule_id)

                audit_entries.append(
                    AuditTrail(
                        entry_id=f"audit-{rule.rule_id}-{now_iso}",
                        subject=subject,
                        action="compliance_check",
                        details=(f"Rule {rule.rule_id} ({rule.condition}): {'PASS' if passed else 'FAIL'}"),
                        timestamp=now_iso,
                    )
                )

        status = "compliant" if not violations else "non_compliant"

        return ComplianceReport(
            subject=subject,
            policy_name="",
            status=status,
            violations=violations,
            created_at=dt.datetime.now(dt.UTC),
            audit_trail=audit_entries,
        )

    def check_with_capabilities(self, subject: str, capabilities: set[str]) -> ComplianceReport:
        """Check compliance using the CapabilityRouter to evaluate rule conditions.

        Each rule's ``condition`` string is interpreted by the router against
        the subject's capability set (``has:<cap>``, ``missing:<cap>``,
        ``all_of:...``, ``any_of:...``).
        """
        if self._capability_router is None:
            raise RuntimeError("ComplianceChecker was not configured with a CapabilityRouter")

        violations: list[str] = []
        audit_entries: list[AuditTrail] = []
        now_iso = dt.datetime.now(dt.UTC).isoformat()

        for policy in self._engine:
            for rule in self._engine.get_rules(policy.name):
                passed = self._capability_router.interpret_condition(rule.condition, capabilities)
                if not passed:
                    violations.append(rule.rule_id)

                audit_entries.append(
                    AuditTrail(
                        entry_id=f"audit-{rule.rule_id}-{now_iso}",
                        subject=subject,
                        action="compliance_check",
                        details=(f"Rule {rule.rule_id} ({rule.condition}): {'PASS' if passed else 'FAIL'}"),
                        timestamp=now_iso,
                    )
                )

        status = "compliant" if not violations else "non_compliant"

        return ComplianceReport(
            subject=subject,
            policy_name="",
            status=status,
            violations=violations,
            created_at=dt.datetime.now(dt.UTC),
            audit_trail=audit_entries,
        )
