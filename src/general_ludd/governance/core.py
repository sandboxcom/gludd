"""Governance core — policy engine and compliance checker."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceReport,
    Policy,
    Rule,
)


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
    """

    def __init__(
        self,
        engine: PolicyEngine,
        evaluate_fn: Callable[[Rule, str], bool] | None = None,
    ) -> None:
        self._engine = engine
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
