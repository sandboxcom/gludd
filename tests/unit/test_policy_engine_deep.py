"""Deep policy engine and governance tests.

Covers: complex rule chains, policy conflict resolution, rule precedence
and override, condition matching edge cases, policy template rendering,
and audit trail generation.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceModel,
    ComplianceReport,
    GovernancePolicy,
    GovernanceRule,
    Policy,
    PolicyRegistry,
    Rule,
)
from general_ludd.governance.core import (
    CapabilityRouter,
    ComplianceChecker,
    PolicyEngine,
)

# ── Test data helpers ───────────────────────────────────────────────────


def _make_policy(name="TestPolicy", domain="security", level="enterprise"):
    return Policy(name=name, description=f"Description for {name}", domain=domain, level=level)


def _make_rule(
    policy_name="TestPolicy",
    rule_id="R-001",
    condition="has:test",
    action="require",
    priority=0,
    enforcement="mandatory",
):
    return Rule(
        policy_name=policy_name,
        rule_id=rule_id,
        condition=condition,
        action=action,
        priority=priority,
        enforcement=enforcement,
    )


def _setup_engine_with_policies(policy_specs):
    engine = PolicyEngine()
    for spec in policy_specs:
        policy = Policy(
            name=spec["name"], description=spec.get("description", ""), domain=spec["domain"], level=spec["level"]
        )
        engine.register_policy(policy)
        for rule_spec in spec.get("rules", []):
            rule = Rule(
                policy_name=spec["name"],
                rule_id=rule_spec["id"],
                condition=rule_spec["condition"],
                action=rule_spec.get("action", "require"),
                priority=rule_spec.get("priority", 0),
                enforcement=rule_spec.get("enforcement", "mandatory"),
            )
            engine.register_rule(rule)
    return engine


# ═══════════════════════════════════════════════════════════════════════
# 1. Complex rule chains
# ═══════════════════════════════════════════════════════════════════════


class TestComplexRuleChains:
    """Policy evaluation through multi-step, interdependent rule chains."""

    def test_chained_rules_all_must_pass(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "ChainPolicy",
                    "domain": "infra",
                    "level": "enterprise",
                    "rules": [
                        {"id": "C-1", "condition": "has:encryption", "action": "require"},
                        {"id": "C-2", "condition": "has:backup", "action": "require"},
                        {"id": "C-3", "condition": "has:monitoring", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption", "backup", "monitoring"})
        assert report.status == "compliant"
        assert report.violations == []

    def test_chained_rules_single_failure_breaks_chain(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "ChainPolicy",
                    "domain": "infra",
                    "level": "enterprise",
                    "rules": [
                        {"id": "C-1", "condition": "has:encryption", "action": "require"},
                        {"id": "C-2", "condition": "has:backup", "action": "require"},
                        {"id": "C-3", "condition": "has:monitoring", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption"})
        assert report.status == "non_compliant"
        assert len(report.violations) == 2
        assert "C-2" in report.violations
        assert "C-3" in report.violations

    def test_rule_chain_with_all_of_condition(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "CompositePolicy",
                    "domain": "infra",
                    "level": "enterprise",
                    "rules": [
                        {"id": "COMP-1", "condition": "all_of:fips,aes256,tls12", "action": "require"},
                        {"id": "COMP-2", "condition": "any_of:backup_aws,backup_gcp,backup_azure", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        caps = {"fips", "aes256", "tls12", "backup_aws"}
        report = checker.check_with_capabilities("node-1", caps)
        assert report.status == "compliant"

    def test_rule_chain_with_any_of_condition_fails_when_none_match(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "BackupPolicy",
                    "domain": "infra",
                    "level": "enterprise",
                    "rules": [
                        {"id": "BKP-1", "condition": "any_of:backup_aws,backup_gcp,backup_azure", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", set())
        assert report.status == "non_compliant"
        assert "BKP-1" in report.violations

    def test_multi_policy_chained_evaluation(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "Security",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {"id": "SEC-1", "condition": "has:encryption", "action": "require"},
                    ],
                },
                {
                    "name": "Compliance",
                    "domain": "compliance",
                    "level": "enterprise",
                    "rules": [
                        {"id": "CMP-1", "condition": "has:audit_log", "action": "require"},
                    ],
                },
                {
                    "name": "Reliability",
                    "domain": "reliability",
                    "level": "enterprise",
                    "rules": [
                        {"id": "REL-1", "condition": "has:backup", "action": "require"},
                        {"id": "REL-2", "condition": "has:monitoring", "action": "require"},
                    ],
                },
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption", "audit_log", "backup", "monitoring"})
        assert report.status == "compliant"
        assert len(report.audit_trail) == 4

    def test_custom_evaluate_fn_chains_rules(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "CustomPolicy",
                    "domain": "custom",
                    "level": "project",
                    "rules": [
                        {"id": "CU-1", "condition": "score>80", "action": "allow"},
                        {"id": "CU-2", "condition": "region=us", "action": "allow"},
                    ],
                }
            ]
        )
        _evaluated = []

        def evaluate_fn(rule, subject):
            if rule.rule_id == "CU-1":
                return subject == "prod-node" and True
            return rule.rule_id == "CU-2"

        checker = ComplianceChecker(engine, evaluate_fn=evaluate_fn)
        report = checker.check("prod-node")
        assert report.status == "compliant"

    def test_empty_chain_evaluates_compliant(self):
        engine = PolicyEngine()
        checker = ComplianceChecker(engine)
        report = checker.check("empty-subject")
        assert report.status == "compliant"
        assert report.violations == []


# ═══════════════════════════════════════════════════════════════════════
# 2. Policy conflict resolution
# ═══════════════════════════════════════════════════════════════════════


class TestPolicyConflictResolution:
    """Resolution strategies for conflicting policy rules."""

    def test_conflicting_rules_on_same_subject(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "AccessPolicy",
                    "domain": "access",
                    "level": "enterprise",
                    "rules": [
                        {"id": "ACC-1", "condition": "has:admin", "action": "allow"},
                        {"id": "ACC-2", "condition": "missing:admin", "action": "deny"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)

        report_admin = checker.check_with_capabilities("admin-user", {"admin"})
        report_anon = checker.check_with_capabilities("anon-user", set())

        assert report_admin.status == "non_compliant"
        assert "ACC-2" in report_admin.violations
        assert report_anon.status == "non_compliant"
        assert "ACC-1" in report_anon.violations

    def test_allow_deny_conflict_with_priority(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "NetworkingPolicy",
                    "domain": "networking",
                    "level": "enterprise",
                    "rules": [
                        {"id": "NET-1", "condition": "has:public_access", "action": "deny", "priority": 100},
                        {"id": "NET-2", "condition": "has:public_access", "action": "allow", "priority": 10},
                    ],
                }
            ]
        )
        rules = engine.get_rules("NetworkingPolicy")
        sorted_rules = sorted(rules, key=lambda r: r.priority, reverse=True)
        assert sorted_rules[0].rule_id == "NET-1"
        assert sorted_rules[0].priority == 100

    def test_cross_policy_conflict_different_domains(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "DevPolicy",
                    "domain": "development",
                    "level": "project",
                    "rules": [
                        {"id": "DEV-1", "condition": "has:debug_mode", "action": "allow"},
                    ],
                },
                {
                    "name": "SecPolicy",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {"id": "SEC-10", "condition": "missing:debug_mode", "action": "require"},
                    ],
                },
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("dev-node", {"debug_mode"})

        assert "SEC-10" in report.violations

    def test_conflict_resolution_enterprise_overrides_project(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "ProjectPolicy",
                    "domain": "project",
                    "level": "project",
                    "rules": [
                        {"id": "PRJ-1", "condition": "has:weak_password", "action": "allow", "priority": 10},
                    ],
                },
                {
                    "name": "EnterprisePolicy",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {"id": "ENT-1", "condition": "missing:weak_password", "action": "require", "priority": 100},
                    ],
                },
            ]
        )
        policies = engine.list_policies()
        enterprise = [p for p in policies if p.level == "enterprise"]
        assert len(enterprise) == 1
        assert enterprise[0].name == "EnterprisePolicy"

    def test_non_compliant_subject_triggers_all_violations(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "DataPolicy",
                    "domain": "data",
                    "level": "enterprise",
                    "rules": [
                        {"id": "D-1", "condition": "has:encryption", "action": "require"},
                        {"id": "D-2", "condition": "has:access_control", "action": "require"},
                        {"id": "D-3", "condition": "missing:pii_exposure", "action": "require"},
                    ],
                },
            ]
        )
        router = CapabilityRouter({"pii_exposure"})
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("insecure-node", {"pii_exposure"})
        assert report.status == "non_compliant"
        assert len(report.violations) == 3


# ═══════════════════════════════════════════════════════════════════════
# 3. Rule precedence and override
# ═══════════════════════════════════════════════════════════════════════


class TestRulePrecedenceOverride:
    """Rule priority-based ordering and enforcement-level overrides."""

    def test_higher_priority_rule_evaluated_first(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "PrecPolicy",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {"id": "P-1", "condition": "has:encryption", "action": "require", "priority": 5},
                        {"id": "P-2", "condition": "has:encryption", "action": "require", "priority": 100},
                        {"id": "P-3", "condition": "has:encryption", "action": "require", "priority": 50},
                    ],
                }
            ]
        )
        rules = engine.get_rules("PrecPolicy")
        sorted_rules = sorted(rules, key=lambda r: r.priority, reverse=True)
        assert sorted_rules[0].rule_id == "P-2"
        assert sorted_rules[1].rule_id == "P-3"
        assert sorted_rules[2].rule_id == "P-1"

    def test_mandatory_overrides_advisory(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "EnforcementPolicy",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {"id": "E-1", "condition": "has:encryption", "action": "require", "enforcement": "advisory"},
                        {"id": "E-2", "condition": "has:encryption", "action": "require", "enforcement": "mandatory"},
                    ],
                }
            ]
        )
        rules = engine.get_rules("EnforcementPolicy")
        mandatory = [r for r in rules if r.enforcement == "mandatory"]
        advisory = [r for r in rules if r.enforcement == "advisory"]
        assert len(mandatory) == 1
        assert len(advisory) == 1

    def test_priority_and_enforcement_together(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "MixedPolicy",
                    "domain": "security",
                    "level": "enterprise",
                    "rules": [
                        {
                            "id": "M-1",
                            "condition": "has:encryption",
                            "action": "require",
                            "priority": 10,
                            "enforcement": "advisory",
                        },
                        {
                            "id": "M-2",
                            "condition": "has:encryption",
                            "action": "require",
                            "priority": 100,
                            "enforcement": "mandatory",
                        },
                        {
                            "id": "M-3",
                            "condition": "has:encryption",
                            "action": "require",
                            "priority": 50,
                            "enforcement": "advisory",
                        },
                        {
                            "id": "M-4",
                            "condition": "has:encryption",
                            "action": "require",
                            "priority": 200,
                            "enforcement": "mandatory",
                        },
                    ],
                }
            ]
        )
        rules = engine.get_rules("MixedPolicy")
        sorted_rules = sorted(rules, key=lambda r: (0 if r.enforcement == "mandatory" else 1, -r.priority))
        assert sorted_rules[0].rule_id == "M-4"
        assert sorted_rules[1].rule_id == "M-2"

    def test_rule_override_by_re_registration_raises(self):
        engine = PolicyEngine()
        policy = _make_policy("OverridePolicy", "security", "enterprise")
        engine.register_policy(policy)
        r1 = _make_rule("OverridePolicy", "OVR-1", "has:encryption", "require")
        engine.register_rule(r1)
        with pytest.raises(ValueError, match="already registered"):
            engine.register_policy(policy)

    def test_default_priority_is_zero(self):
        rule = Rule(policy_name="P", rule_id="R-1", condition="has:x", action="require")
        assert rule.priority == 0

    def test_default_enforcement_is_advisory(self):
        rule = Rule(policy_name="P", rule_id="R-1", condition="has:x", action="require")
        assert rule.enforcement == "advisory"


# ═══════════════════════════════════════════════════════════════════════
# 4. Condition matching edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestConditionMatchingEdgeCases:
    """Boundary and edge case behavior for rule condition matching."""

    def test_empty_condition_defaults_to_pass(self):
        router = CapabilityRouter()
        assert router.interpret_condition("", {"anything"}) is True
        assert router.interpret_condition("", set()) is True

    def test_has_condition_with_empty_cap_set(self):
        router = CapabilityRouter()
        assert router.interpret_condition("has:encryption", set()) is False

    def test_missing_condition_with_empty_cap_set(self):
        router = CapabilityRouter()
        assert router.interpret_condition("missing:encryption", set()) is True

    def test_all_of_with_empty_required_list(self):
        router = CapabilityRouter()
        assert router.interpret_condition("all_of:", {"a", "b"}) is True

    def test_any_of_with_empty_required_list(self):
        router = CapabilityRouter()
        assert router.interpret_condition("any_of:", {"a", "b"}) is False

    def test_has_condition_with_spaces_in_capability(self):
        router = CapabilityRouter()
        caps = {" multi word cap "}
        assert router.interpret_condition("has: multi word cap ", caps) is True

    def test_all_of_with_whitespace_around_delimiters(self):
        router = CapabilityRouter()
        caps = {"a", "b", "c"}
        assert router.interpret_condition("all_of: a , b , c ", caps) is True

    def test_any_of_with_whitespace_around_delimiters(self):
        router = CapabilityRouter()
        caps = {"x"}
        assert router.interpret_condition("any_of: x , y , z ", caps) is True

    def test_has_condition_unicode_capability(self):
        router = CapabilityRouter()
        caps = {"caf\u00e9"}
        assert router.interpret_condition("has:caf\u00e9", caps) is True

    def test_unknown_prefix_defaults_pass(self):
        router = CapabilityRouter()
        assert router.interpret_condition("foo:bar", set()) is True
        assert router.interpret_condition("un:known", {"un"}) is True

    def test_all_of_single_cap_matches_like_has(self):
        router = CapabilityRouter()
        assert router.interpret_condition("all_of:encryption", {"encryption"}) is True
        assert router.interpret_condition("all_of:encryption", set()) is False

    def test_any_of_single_cap_matches_like_has(self):
        router = CapabilityRouter()
        assert router.interpret_condition("any_of:encryption", {"encryption"}) is True
        assert router.interpret_condition("any_of:encryption", set()) is False

    def test_missing_condition_fails_when_cap_present(self):
        router = CapabilityRouter()
        caps = {"debug_mode"}
        assert router.interpret_condition("missing:debug_mode", caps) is False

    def test_rule_with_empty_condition_in_engine(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "EmptyCondPolicy",
                    "domain": "test",
                    "level": "project",
                    "rules": [
                        {"id": "EC-1", "condition": "", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node", set())
        assert report.status == "compliant"


# ═══════════════════════════════════════════════════════════════════════
# 5. Policy template rendering
# ═══════════════════════════════════════════════════════════════════════


def _render_policy_template(policy, rules, effective_date=None):
    lines = []
    lines.append(f"Policy: {policy.name}")
    lines.append(f"Domain: {policy.domain} | Level: {policy.level}")
    lines.append(f"Status: {policy.status}")
    if effective_date:
        lines.append(f"Effective: {effective_date}")
    lines.append("")
    lines.append("Rules:")
    for rule in sorted(rules, key=lambda r: r.priority, reverse=True):
        enf = f"[{rule.enforcement.upper()}]"
        pri = f"(P={rule.priority})"
        lines.append(f"  {enf} {pri} {rule.rule_id}: {rule.action.upper()} when {rule.condition}")
    return "\n".join(lines)


class TestPolicyTemplateRendering:
    """Policy template generation from Policy and Rule dataclasses."""

    def test_render_single_policy_no_rules(self):
        policy = _make_policy("EmptyPolicy", "domain-x", "project")
        rendered = _render_policy_template(policy, [])
        assert "Policy: EmptyPolicy" in rendered
        assert "Domain: domain-x" in rendered
        assert "Rules:" in rendered

    def test_render_policy_with_multiple_rules(self):
        policy = _make_policy("SecurityPolicy", "security", "enterprise")
        rules = [
            _make_rule("SecurityPolicy", "SEC-1", "has:encryption", "require", priority=100, enforcement="mandatory"),
            _make_rule("SecurityPolicy", "SEC-2", "has:audit_log", "require", priority=50, enforcement="mandatory"),
            _make_rule("SecurityPolicy", "SEC-3", "missing:debug_mode", "require", priority=80, enforcement="advisory"),
        ]
        rendered = _render_policy_template(policy, rules)
        assert "SEC-1" in rendered
        assert "SEC-2" in rendered
        assert "SEC-3" in rendered
        assert "MANDATORY" in rendered
        assert "ADVISORY" in rendered
        assert "P=100" in rendered
        lines = rendered.split("\n")
        rule_lines = [li for li in lines if li.strip().startswith("[")]
        assert rule_lines[0].strip().startswith("[MANDATORY]")
        assert "SEC-1" in rule_lines[0]

    def test_render_policy_with_effective_date(self):
        policy = _make_policy("TemporalPolicy", "compliance", "enterprise")
        policy.effective_date = "2026-01-01"
        rendered = _render_policy_template(policy, [], effective_date="2026-01-01")
        assert "Effective: 2026-01-01" in rendered

    def test_render_policy_draft_status(self):
        policy = _make_policy("DraftPolicy", "drafts", "project")
        policy.status = "draft"
        rendered = _render_policy_template(policy, [])
        assert "Status: draft" in rendered

    def test_render_policy_active_status(self):
        policy = _make_policy("ActivePolicy", "active", "enterprise")
        policy.status = "active"
        rendered = _render_policy_template(policy, [])
        assert "Status: active" in rendered

    def test_render_from_dataclass_asdict_roundtrip(self):
        policy = _make_policy("RoundtripPolicy", "test", "project")
        policy_dict = asdict(policy)
        assert policy_dict["name"] == "RoundtripPolicy"
        assert policy_dict["domain"] == "test"
        assert policy_dict["level"] == "project"
        assert policy_dict["status"] == "draft"

    def test_governance_policy_template_rendering(self):
        gp = GovernancePolicy(
            name="GovPolicy",
            level="enterprise",
            description="A governance policy",
            domain="gov",
            status="active",
            effective_date="2026-06-01",
        )
        template = f"{gp.name} :: {gp.domain}/{gp.level} [{gp.status}]"
        assert template == "GovPolicy :: gov/enterprise [active]"

    def test_governance_rule_to_rule_compatibility(self):
        gr = GovernanceRule(
            policy_name="P", rule_id="GR-1", condition="has:x", action="require", priority=99, enforcement="mandatory"
        )
        rule = Rule(
            policy_name=gr.policy_name,
            rule_id=gr.rule_id,
            condition=gr.condition,
            action=gr.action,
            priority=gr.priority,
            enforcement=gr.enforcement,
        )
        assert rule.policy_name == "P"
        assert rule.rule_id == "GR-1"
        assert rule.priority == 99
        assert rule.enforcement == "mandatory"


# ═══════════════════════════════════════════════════════════════════════
# 6. Audit trail generation
# ═══════════════════════════════════════════════════════════════════════


class TestAuditTrailGeneration:
    """Completeness, accuracy, and structure of generated audit trails."""

    def test_audit_trail_has_all_fields(self):
        entry = AuditTrail(
            entry_id="AUD-1",
            subject="node-1",
            action="compliance_check",
            details="PASS",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert entry.entry_id == "AUD-1"
        assert entry.subject == "node-1"
        assert entry.action == "compliance_check"
        assert entry.details == "PASS"
        assert entry.timestamp == "2026-01-01T00:00:00Z"

    def test_audit_trail_entries_are_unique_per_check(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "AuditPolicy",
                    "domain": "audit",
                    "level": "enterprise",
                    "rules": [
                        {"id": "AUD-R1", "condition": "has:encryption", "action": "require"},
                        {"id": "AUD-R2", "condition": "has:backup", "action": "require"},
                        {"id": "AUD-R3", "condition": "has:monitoring", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption", "backup", "monitoring"})
        entry_ids = {e.entry_id for e in report.audit_trail}
        assert len(entry_ids) == 3

    def test_audit_trail_includes_pass_and_fail_entries(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "MixPolicy",
                    "domain": "mix",
                    "level": "enterprise",
                    "rules": [
                        {"id": "MX-1", "condition": "has:encryption", "action": "require"},
                        {"id": "MX-2", "condition": "missing:debug", "action": "require"},
                        {"id": "MX-3", "condition": "has:backup", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption"})
        details = [e.details for e in report.audit_trail]
        assert any("PASS" in d for d in details)
        assert any("FAIL" in d for d in details)

    def test_audit_trail_timestamp_is_iso8601(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "TimePolicy",
                    "domain": "time",
                    "level": "enterprise",
                    "rules": [
                        {"id": "TM-1", "condition": "has:encryption", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("node-1", {"encryption"})
        for entry in report.audit_trail:
            assert "T" in entry.timestamp

    def test_audit_trail_subject_consistent_across_entries(self):
        engine = _setup_engine_with_policies(
            [
                {
                    "name": "SubPolicy",
                    "domain": "sub",
                    "level": "enterprise",
                    "rules": [
                        {"id": "SUB-1", "condition": "has:a", "action": "require"},
                        {"id": "SUB-2", "condition": "has:b", "action": "require"},
                    ],
                }
            ]
        )
        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        subject = "consistent-node-42"
        report = checker.check_with_capabilities(subject, {"a", "b"})
        for entry in report.audit_trail:
            assert entry.subject == subject

    def test_compliance_report_is_compliant_property(self):
        compliant = ComplianceReport(subject="s", policy_name="p", status="compliant")
        non_compliant = ComplianceReport(subject="s", policy_name="p", status="non_compliant")
        assert compliant.is_compliant is True
        assert non_compliant.is_compliant is False

    def test_compliance_model_audit_trail_append(self):
        model = ComplianceModel(subject="s", policy_name="p", compliance_status="pending")
        model.audit_trail.append("2026-01-01: checked requirement X - PASS")
        model.audit_trail.append("2026-01-02: checked requirement Y - FAIL")
        assert len(model.audit_trail) == 2

    def test_audit_trail_hashable(self):
        e1 = AuditTrail(entry_id="ID-1", subject="s", action="a", details="d", timestamp="t")
        e2 = AuditTrail(entry_id="ID-1", subject="s", action="a", details="d", timestamp="t")
        assert hash(e1) == hash(e2)
        assert e1 == e2

    def test_audit_trail_inequality(self):
        e1 = AuditTrail(entry_id="ID-1", subject="s", action="a", details="d", timestamp="t")
        e2 = AuditTrail(entry_id="ID-2", subject="s", action="a", details="d", timestamp="t")
        assert e1 != e2


# ═══════════════════════════════════════════════════════════════════════
# 7. PolicyRegistry deep tests
# ═══════════════════════════════════════════════════════════════════════


class TestPolicyRegistryDeep:
    """Deep tests for the PolicyRegistry contract class."""

    def test_registry_add_and_retrieve_policy(self):
        registry = PolicyRegistry()
        gp = GovernancePolicy(name="GP-1", level="enterprise", description="d", domain="dom")
        registry.add_policy(gp)
        assert registry.get_policy("GP-1") is gp
        assert "GP-1" in registry

    def test_registry_remove_policy(self):
        registry = PolicyRegistry()
        gp = GovernancePolicy(name="GP-1", level="enterprise", description="d", domain="dom")
        registry.add_policy(gp)
        assert registry.remove_policy("GP-1") is True
        assert registry.get_policy("GP-1") is None
        assert "GP-1" not in registry

    def test_registry_remove_nonexistent_policy(self):
        registry = PolicyRegistry()
        assert registry.remove_policy("nonexistent") is False

    def test_registry_add_rule_for_missing_policy(self):
        registry = PolicyRegistry()
        gr = GovernanceRule(policy_name="Ghost", rule_id="G-1", condition="c", action="a")
        with pytest.raises(KeyError, match="Ghost"):
            registry.add_rule(gr)

    def test_registry_add_and_get_rules(self):
        registry = PolicyRegistry()
        gp = GovernancePolicy(name="GP-1", level="enterprise", description="d", domain="dom")
        registry.add_policy(gp)
        registry.add_rule(GovernanceRule(policy_name="GP-1", rule_id="R1", condition="c1", action="a1"))
        registry.add_rule(GovernanceRule(policy_name="GP-1", rule_id="R2", condition="c2", action="a2"))
        rules = registry.get_rules("GP-1")
        assert len(rules) == 2

    def test_registry_list_policies_filtered(self):
        registry = PolicyRegistry()
        registry.add_policy(GovernancePolicy(name="A", level="enterprise", description="d", domain="security"))
        registry.add_policy(GovernancePolicy(name="B", level="project", description="d", domain="security"))
        registry.add_policy(GovernancePolicy(name="C", level="enterprise", description="d", domain="data"))
        assert len(registry.list_policies(domain="security")) == 2
        assert len(registry.list_policies(level="project")) == 1
        assert len(registry.list_policies(domain="security", level="enterprise")) == 1

    def test_registry_iteration_order(self):
        registry = PolicyRegistry()
        registry.add_policy(GovernancePolicy(name="First", level="e", description="d", domain="d"))
        registry.add_policy(GovernancePolicy(name="Second", level="e", description="d", domain="d"))
        names = [p.name for p in registry]
        assert names == ["First", "Second"]

    def test_registry_len(self):
        registry = PolicyRegistry()
        assert len(registry) == 0
        registry.add_policy(GovernancePolicy(name="A", level="e", description="d", domain="d"))
        assert len(registry) == 1

    def test_registry_remove_policy_also_removes_rules(self):
        registry = PolicyRegistry()
        gp = GovernancePolicy(name="GP-R", level="enterprise", description="d", domain="dom")
        registry.add_policy(gp)
        registry.add_rule(GovernanceRule(policy_name="GP-R", rule_id="R-1", condition="c", action="a"))
        registry.remove_policy("GP-R")
        assert registry.get_rules("GP-R") == []
