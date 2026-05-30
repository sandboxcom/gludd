"""Unit tests for rules engine."""

from agentic_harness.rules.engine import Rule, RuleEngine


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
