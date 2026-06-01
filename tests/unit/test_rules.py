"""Unit tests for rules engine."""

from __future__ import annotations

from general_ludd.rules.engine import Rule, RuleAction, RuleEngine, default_rules, evaluate_rules


class TestRuleEngine:
    def test_rule_matches_simple_eq(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="route_dependency",
            condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
            actions=[{"type": "route", "queue": "dependency"}],
        ))
        results = engine.evaluate({"todo": {"work_type": "dependency"}})
        assert len(results) == 1
        assert results[0]["rule_id"] == "route_dependency"

    def test_rule_no_match(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="route_dependency",
            condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
            actions=[{"type": "route", "queue": "dependency"}],
        ))
        results = engine.evaluate({"todo": {"work_type": "code"}})
        assert len(results) == 0

    def test_rule_disabled_skipped(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="disabled_rule",
            enabled=False,
            condition={"field": "x", "op": "eq", "value": 1},
            actions=[{"type": "noop"}],
        ))
        results = engine.evaluate({"x": 1})
        assert len(results) == 0

    def test_rule_all_condition(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="all_rule",
            condition={"all": [
                {"field": "a", "op": "eq", "value": 1},
                {"field": "b", "op": "eq", "value": 2},
            ]},
            actions=[{"type": "combined"}],
        ))
        results = engine.evaluate({"a": 1, "b": 2})
        assert len(results) == 1
        results = engine.evaluate({"a": 1, "b": 3})
        assert len(results) == 0

    def test_rule_any_condition(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="any_rule",
            condition={"any": [
                {"field": "a", "op": "eq", "value": 1},
                {"field": "b", "op": "eq", "value": 2},
            ]},
            actions=[{"type": "either"}],
        ))
        results = engine.evaluate({"a": 1, "b": 99})
        assert len(results) == 1

    def test_empty_condition_matches_all(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="catchall",
            condition={},
            actions=[{"type": "default"}],
        ))
        results = engine.evaluate({"anything": True})
        assert len(results) == 1

    def test_rules_sorted_by_priority(self):
        engine = RuleEngine()
        engine.add_rule(Rule(rule_id="low", priority=200, actions=[{"type": "low"}]))
        engine.add_rule(Rule(rule_id="high", priority=10, actions=[{"type": "high"}]))
        results = engine.evaluate({})
        assert results[0]["rule_id"] == "high"
        assert results[1]["rule_id"] == "low"


class TestRuleEngineDefaultRules:
    def test_rule_routes_failing_validation_to_qa(self):
        engine = RuleEngine(rules=default_rules())
        results = engine.evaluate({
            "todo": {"status": "failed", "work_type": "test"},
        })
        matching = [
            r for r in results
            if any(a.get("type") == "route" and a.get("queue") == "qa" for a in r["actions"])
        ]
        assert len(matching) > 0

    def test_rule_routes_dependency_to_dependency_queue(self):
        engine = RuleEngine(rules=default_rules())
        results = engine.evaluate({
            "todo": {"work_type": "dependency"},
        })
        matching = [
            r for r in results
            if any(a.get("type") == "route" and a.get("queue") == "dependency" for a in r["actions"])
        ]
        assert len(matching) > 0

    def test_rule_routes_openbao_update_to_manual_hold(self):
        engine = RuleEngine(rules=default_rules())
        results = engine.evaluate({
            "todo": {"tags": ["openbao", "image_update"]},
        })
        matching = [
            r for r in results
            if any(
                a.get("type") == "route" and a.get("queue") == "manual_hold" and a.get("approval_required") is True
                for a in r["actions"]
            )
        ]
        assert len(matching) > 0

    def test_rule_reduces_buckets_when_load_high(self):
        engine = RuleEngine(rules=default_rules())
        results = engine.evaluate({
            "load_snapshot": {"loadavg_10m": 10.0, "logical_cpu_count": 4, "load_ratio": 2.5},
            "queue": {"resource_profile": "local_heavy", "queue_name": "ansible"},
        })
        matching = [
            r for r in results
            if any(a.get("type") == "reduce_buckets" for a in r["actions"])
        ]
        assert len(matching) > 0

    def test_rule_reduces_api_metered_near_exhaustion(self):
        engine = RuleEngine(rules=default_rules())
        results = engine.evaluate({
            "queue": {"resource_profile": "ai_heavy", "queue_name": "model"},
            "budget": {"budget_near_exhaustion": True},
        })
        matching = [
            r for r in results
            if any(a.get("type") == "reduce_buckets" for a in r["actions"])
        ]
        assert len(matching) > 0


class TestEvaluateRules:
    def test_evaluate_rules_returns_rule_actions(self):
        rules = [
            Rule(
                rule_id="test_route",
                condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
                actions=[{"type": "route", "queue": "dependency"}],
                audit_message="routing dep",
            ),
        ]
        actions = evaluate_rules(rules, {"todo": {"work_type": "dependency"}})
        assert len(actions) == 1
        assert isinstance(actions[0], RuleAction)
        assert actions[0].rule_id == "test_route"
        assert actions[0].action_type == "route"
        assert actions[0].params["queue"] == "dependency"
        assert actions[0].audit_message == "routing dep"

    def test_evaluate_rules_no_match_returns_empty(self):
        rules = [
            Rule(
                rule_id="test_route",
                condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
                actions=[{"type": "route", "queue": "dependency"}],
            ),
        ]
        actions = evaluate_rules(rules, {"todo": {"work_type": "code"}})
        assert len(actions) == 0

    def test_evaluate_rules_captures_queue_from_context(self):
        rules = [
            Rule(
                rule_id="reduce_load",
                condition={"field": "queue.resource_profile", "op": "eq", "value": "local_heavy"},
                actions=[{"type": "reduce_buckets", "reduction": 2}],
            ),
        ]
        actions = evaluate_rules(rules, {
            "queue": {"resource_profile": "local_heavy", "queue_name": "ansible"},
        })
        assert len(actions) == 1
        assert actions[0].params["queue"] == "ansible"


class TestRuleEngineGtLt:
    def test_gt_operator(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="gt_rule",
            condition={"field": "x", "op": "gt", "value": 5},
            actions=[{"type": "fire"}],
        ))
        assert len(engine.evaluate({"x": 10})) == 1
        assert len(engine.evaluate({"x": 3})) == 0

    def test_lt_operator(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="lt_rule",
            condition={"field": "x", "op": "lt", "value": 5},
            actions=[{"type": "fire"}],
        ))
        assert len(engine.evaluate({"x": 3})) == 1
        assert len(engine.evaluate({"x": 10})) == 0

    def test_gte_operator(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="gte_rule",
            condition={"field": "x", "op": "gte", "value": 5},
            actions=[{"type": "fire"}],
        ))
        assert len(engine.evaluate({"x": 5})) == 1
        assert len(engine.evaluate({"x": 4})) == 0

    def test_contains_operator(self):
        engine = RuleEngine()
        engine.add_rule(Rule(
            rule_id="contains_rule",
            condition={"field": "tags", "op": "contains", "value": "openbao"},
            actions=[{"type": "fire"}],
        ))
        assert len(engine.evaluate({"tags": ["openbao", "security"]})) == 1
        assert len(engine.evaluate({"tags": ["security"]})) == 0
