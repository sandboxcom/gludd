"""Integration tests for governance capability router — policy eval via capability routing.

Exercises the full pipeline: CapabilityRouter → PolicyEngine → ComplianceChecker.
"""

from __future__ import annotations

from general_ludd.governance.contracts import Policy, Rule
from general_ludd.governance.core import CapabilityRouter, ComplianceChecker, PolicyEngine


class TestCapabilityRouterIntegration:
    """CapabilityRouter integrated with PolicyEngine and ComplianceChecker."""

    # ── router construction and interpretation ──────────────────────────

    def test_router_empty_no_capabilities(self):
        router = CapabilityRouter()
        assert router.has_any({"read"}) is False
        assert router.interpret_condition("has:write", set()) is False

    def test_router_with_capabilities(self):
        router = CapabilityRouter(capabilities={"read", "write", "admin"})
        assert router.has_any({"read", "write"}) is True
        assert router.has_any({"deploy"}) is False
        assert router.has_all({"read", "write"}) is True

    def test_router_add_and_remove_capabilities(self):
        router = CapabilityRouter()
        router.add("read")
        router.add("write")
        assert router.has_all({"read", "write"})
        router.remove("read")
        assert router.has_any({"read"}) is False
        assert router.has_any({"write"}) is True

    def test_router_interpret_condition(self):
        router = CapabilityRouter(capabilities={"encrypt", "audit"})
        assert router.interpret_condition("has:encrypt", {"encrypt", "audit"})
        assert not router.interpret_condition("has:deploy", {"encrypt", "audit"})
        assert router.interpret_condition("missing:deploy", {"encrypt", "audit"})
        assert not router.interpret_condition("missing:encrypt", {"encrypt", "audit"})

    def test_router_interpret_all_of(self):
        router = CapabilityRouter(capabilities={"a", "b", "c"})
        assert router.interpret_condition("all_of:a,b", {"a", "b", "c"})
        assert not router.interpret_condition("all_of:a,d", {"a", "b", "c"})

    def test_router_interpret_any_of(self):
        router = CapabilityRouter(capabilities={"a", "b"})
        assert router.interpret_condition("any_of:a,c", {"a", "b"})
        assert router.interpret_condition("any_of:x,y,c", {"a", "b", "c"})
        assert not router.interpret_condition("any_of:x,y", {"a", "b"})

    # ── router wired into policy engine ─────────────────────────────────

    def test_router_evaluates_rules_by_capability(self):
        engine = PolicyEngine()
        engine.register_policy(Policy(name="Security", description="d", domain="security", level="enterprise"))
        engine.register_rule(Rule(policy_name="Security", rule_id="S-1", condition="has:encrypt", action="require"))
        engine.register_rule(Rule(policy_name="Security", rule_id="S-2", condition="has:audit", action="require"))
        engine.register_rule(Rule(policy_name="Security", rule_id="S-3", condition="missing:deploy", action="block"))

        def evaluate_fn(rule, subject):
            return _ROUTER_REGISTRY[subject].interpret_condition(
                rule.condition, _ROUTER_REGISTRY[subject]._capabilities
            )

        _ROUTER_REGISTRY["repo-a"] = CapabilityRouter(capabilities={"encrypt", "audit"})
        _ROUTER_REGISTRY["repo-b"] = CapabilityRouter(capabilities={"encrypt"})

        checker = ComplianceChecker(engine, evaluate_fn=evaluate_fn)
        report_a = checker.check("repo-a")
        report_b = checker.check("repo-b")

        assert report_a.status == "compliant"
        assert report_b.status == "non_compliant"
        assert "S-2" in report_b.violations

    def test_router_compliance_checker_wired_directly(self):
        engine = PolicyEngine()
        engine.register_policy(Policy(name="Gate", description="d", domain="qa", level="project"))
        engine.register_rule(Rule(policy_name="Gate", rule_id="G-1", condition="has:governance:read", action="require"))
        engine.register_rule(
            Rule(policy_name="Gate", rule_id="G-2", condition="missing:governance:admin", action="block")
        )

        subject_caps = {"governance:read", "governance:write"}
        router = CapabilityRouter(capabilities=subject_caps)

        def evaluate_fn(rule, subject):
            return router.interpret_condition(rule.condition, router._capabilities)

        checker = ComplianceChecker(engine, evaluate_fn=evaluate_fn)
        report = checker.check("subject-1")

        assert report.subject == "subject-1"
        assert report.status == "compliant"
        assert len(report.audit_trail) == 2

    def test_router_default_condition_passes(self):
        router = CapabilityRouter(capabilities={"read"})
        assert router.interpret_condition("unknown:format", {"read"}) is True

    # ── cross-domain capability checks ──────────────────────────────────

    def test_router_multiple_policies_different_domains(self):
        engine = PolicyEngine()
        engine.register_policy(Policy(name="DP", description="d", domain="data", level="enterprise"))
        engine.register_policy(Policy(name="SP", description="d", domain="security", level="project"))
        engine.register_rule(Rule(policy_name="DP", rule_id="D-1", condition="has:data:read", action="require"))
        engine.register_rule(Rule(policy_name="DP", rule_id="D-2", condition="has:data:write", action="require"))
        engine.register_rule(Rule(policy_name="SP", rule_id="S-1", condition="has:security:audit", action="require"))

        caps = {"data:read", "data:write", "security:audit"}
        router = CapabilityRouter(capabilities=caps)

        def evaluate_fn(rule, subject):
            return router.interpret_condition(rule.condition, router._capabilities)

        checker = ComplianceChecker(engine, evaluate_fn=evaluate_fn)
        report = checker.check("multi-domain-subject")
        assert report.status == "compliant"
        assert len(report.audit_trail) == 3


_ROUTER_REGISTRY: dict[str, CapabilityRouter] = {}
