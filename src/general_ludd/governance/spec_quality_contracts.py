"""Spec quality audit contracts — AuditRule, AuditFinding, AuditReport.

Defines the formal contracts for behavioral spec quality auditing.
Used by spec quality checkers and the spec quality gate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass(eq=True, unsafe_hash=True)
class AuditRule:
    """A quality rule that checks a behavioral spec entry.

    Each rule belongs to a category and has a severity level.
    The ``check_fn`` field names a concrete checker function or script
    that implements the rule.
    """

    rule_id: str
    name: str
    description: str
    category: str
    severity: str = "error"
    check_fn: str = ""
    active: bool = True


@dataclass(eq=True)
class AuditFinding:
    """A finding produced by applying an audit rule to a spec entry.

    Tracks the rule that produced it, the spec entry it applies to,
    and the evidence supporting the finding.
    """

    rule_id: str
    spec_id: str
    severity: str
    message: str
    evidence: str = ""
    line: int = 0


@dataclass(eq=True)
class AuditReport:
    """Aggregates findings from a spec quality audit run.

    Computes aggregate statistics and quality ratio from findings.
    """

    findings: list[AuditFinding] = field(default_factory=list)
    rules_applied: list[str] = field(default_factory=list)
    timestamp: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")

    @property
    def unique_specs_checked(self) -> int:
        return len({f.spec_id for f in self.findings})

    @property
    def unique_rules_fired(self) -> int:
        return len({f.rule_id for f in self.findings})

    def findings_by_severity(self, severity: str) -> list[AuditFinding]:
        return [f for f in self.findings if f.severity == severity]

    def findings_by_rule(self, rule_id: str) -> list[AuditFinding]:
        return [f for f in self.findings if f.rule_id == rule_id]

    def findings_by_spec(self, spec_id: str) -> list[AuditFinding]:
        return [f for f in self.findings if f.spec_id == spec_id]

    def has_errors(self) -> bool:
        return self.error_count > 0


class RuleRegistry:
    """In-memory registry of audit rules for spec quality checking.

    Provides add/get/remove/list operations for audit rules.
    Iterating the registry yields rules in insertion order.
    """

    def __init__(self) -> None:
        self._rules: dict[str, AuditRule] = {}

    def add_rule(self, rule: AuditRule) -> None:
        if rule.rule_id in self._rules:
            raise ValueError(f"Rule '{rule.rule_id}' already exists")
        self._rules[rule.rule_id] = rule

    def get_rule(self, rule_id: str) -> AuditRule | None:
        return self._rules.get(rule_id)

    def remove_rule(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def list_rules(
        self,
        category: str | None = None,
        severity: str | None = None,
        active_only: bool = True,
    ) -> list[AuditRule]:
        results = list(self._rules.values())
        if active_only:
            results = [r for r in results if r.active]
        if category is not None:
            results = [r for r in results if r.category == category]
        if severity is not None:
            results = [r for r in results if r.severity == severity]
        return results

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[AuditRule]:
        return iter(self._rules.values())

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._rules


class SpecAuditor:
    """Applies registered audit rules to behavioral spec entries.

    Accepts parsed spec entries and runs each active rule against them,
    producing an ``AuditReport``.
    """

    def __init__(self, registry: RuleRegistry) -> None:
        self._registry = registry

    @property
    def rules(self) -> list[AuditRule]:
        return [r for r in self._registry if r.active]

    def audit(self, entries: list[dict[str, object]]) -> AuditReport:
        findings: list[AuditFinding] = []
        for rule in self._registry:
            if not rule.active:
                continue
            for entry in entries:
                result = self._apply_rule(rule, entry)
                if result is not None:
                    findings.append(result)

        return AuditReport(
            findings=findings,
            rules_applied=[r.rule_id for r in self._registry if r.active],
        )

    def _apply_rule(self, rule: AuditRule, entry: dict[str, object]) -> AuditFinding | None:
        """Apply a single rule to a single spec entry. Returns a finding or None."""
        spec_id = str(entry.get("spec_id", ""))
        body = str(entry.get("body", ""))

        handlers = {
            "enforcement_present": self._check_enforcement_present,
            "enforcement_concrete": self._check_enforcement_concrete,
            "body_non_empty": self._check_body_non_empty,
            "no_placeholder_enforcement": self._check_no_placeholder_enforcement,
            "behavior_measurable": self._check_behavior_measurable,
        }

        handler = handlers.get(rule.category)
        if handler is None:
            return None
        return handler(rule, spec_id, body)

    def _check_enforcement_present(self, rule: AuditRule, spec_id: str, body: str) -> AuditFinding | None:
        if "**Enforcement:**" not in body:
            return AuditFinding(
                rule_id=rule.rule_id,
                spec_id=spec_id,
                severity=rule.severity,
                message=f"Missing Enforcement field in spec {spec_id}",
                evidence=body[:200],
            )
        return None

    def _check_enforcement_concrete(self, rule: AuditRule, spec_id: str, body: str) -> AuditFinding | None:
        import re

        enf_match = re.search(r"\*\*Enforcement:\*\*\s*(.+)$", body, re.MULTILINE)
        if not enf_match:
            return None
        enf_text = enf_match.group(1)
        concrete_indicators = [
            r"`make\s+[\w-]+`",
            r"\.(?:ts|py|sh|yml|yaml|js|mjs)",
            r"AGENTS\.md",
            r"opencode\.json",
            r"\.github/workflows/",
            r"\b(?:Makefile|plugin|hook|workflow|target|guard|prerequisite)\b",
        ]
        if not any(re.search(indicator, enf_text, re.IGNORECASE) for indicator in concrete_indicators):
            return AuditFinding(
                rule_id=rule.rule_id,
                spec_id=spec_id,
                severity=rule.severity,
                message=f"Enforcement field does not reference concrete mechanism in spec {spec_id}",
                evidence=enf_text,
            )
        return None

    def _check_body_non_empty(self, rule: AuditRule, spec_id: str, body: str) -> AuditFinding | None:
        stripped = body.strip()
        if not stripped or stripped.startswith("###"):
            return AuditFinding(
                rule_id=rule.rule_id,
                spec_id=spec_id,
                severity=rule.severity,
                message=f"Spec {spec_id} has empty body",
                evidence="",
            )
        return None

    def _check_no_placeholder_enforcement(self, rule: AuditRule, spec_id: str, body: str) -> AuditFinding | None:
        import re

        enf_match = re.search(r"\*\*Enforcement:\*\*\s*(.+)$", body, re.MULTILINE)
        if not enf_match:
            return None
        enf_text = enf_match.group(1)
        if re.search(
            r"\b(?:none|tbd|todo|planned|proposal|future|placeholder)\b",
            enf_text,
            re.IGNORECASE,
        ):
            return AuditFinding(
                rule_id=rule.rule_id,
                spec_id=spec_id,
                severity=rule.severity,
                message=f"Enforcement field contains placeholder in spec {spec_id}",
                evidence=enf_text,
            )
        return None

    def _check_behavior_measurable(self, rule: AuditRule, spec_id: str, body: str) -> AuditFinding | None:
        import re

        beh_match = re.search(r"\*\*Behavior:\*\*\s*(.+)$", body, re.MULTILINE)
        if not beh_match:
            return None
        beh_text = beh_match.group(1)
        measurable_outcomes = [
            r"\b(?:block|deny|reject|record|classify|restore|verify)\b",
            r"\b\d+\s*(?:%|percent|seconds|minutes|files|tests)\b",
            r"exit\s+(?:0|1|non-zero)",
        ]
        if not any(re.search(pattern, beh_text, re.IGNORECASE) for pattern in measurable_outcomes):
            return None
        advisory_only = r"\b(?:advisory|suggestion|recommended|optional|best effort)\b"
        if re.search(advisory_only, beh_text, re.IGNORECASE):
            return AuditFinding(
                rule_id=rule.rule_id,
                spec_id=spec_id,
                severity=rule.severity,
                message=f"Behavior is advisory rather than measurable in spec {spec_id}",
                evidence=beh_text,
            )
        return None


__all__ = [
    "AuditFinding",
    "AuditReport",
    "AuditRule",
    "RuleRegistry",
    "SpecAuditor",
]
