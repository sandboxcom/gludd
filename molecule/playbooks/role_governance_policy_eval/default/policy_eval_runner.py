#!/usr/bin/env python3
"""Molecule runner script — exercises the governance CapabilityRouter, PolicyEngine,
and ComplianceChecker end-to-end and writes a JSON artifact.

Invoked by the molecule converge playbook via ``ansible.builtin.script``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../src"))

from general_ludd.governance.contracts import Policy, Rule
from general_ludd.governance.core import CapabilityRouter, ComplianceChecker, PolicyEngine


def main() -> int:
    engine = PolicyEngine()
    engine.register_policy(
        Policy(name="Security", description="Security posture policy", domain="security", level="enterprise")
    )
    engine.register_rule(Rule(policy_name="Security", rule_id="S-001", condition="has:encrypt", action="require"))
    engine.register_rule(Rule(policy_name="Security", rule_id="S-002", condition="has:audit_log", action="require"))
    engine.register_rule(
        Rule(policy_name="Security", rule_id="S-003", condition="missing:insecure_port", action="block")
    )

    subject_caps = {"encrypt", "audit_log"}
    router = CapabilityRouter(capabilities=subject_caps)

    def evaluate_fn(rule: Rule, subject: str) -> bool:
        return router.interpret_condition(rule.condition, router._capabilities)

    checker = ComplianceChecker(engine, evaluate_fn=evaluate_fn)
    report = checker.check("subject-e2e")

    artifact_dir = os.environ.get("GLUDD_ARTIFACT_DIR", "/tmp/gludd-governance-policy-eval")
    os.makedirs(artifact_dir, exist_ok=True)

    result = {
        "subject": report.subject,
        "status": report.status,
        "violations": report.violations,
        "audit_count": len(report.audit_trail),
        "engine_policy_count": len(engine),
    }
    with open(os.path.join(artifact_dir, "policy_eval.json"), "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    print(json.dumps(result, indent=2))
    return 0 if report.status == "compliant" else 1


if __name__ == "__main__":
    sys.exit(main())
