"""Structural tests for rules/engine.py — policy overlay rules engine."""

from general_ludd.rules.engine import (
    ActionType,
    Rule,
    RuleAction,
    RuleEngine,
    apply_rule_actions,
    default_rules,
    evaluate_rules,
)


class TestActionType:
    def test_enum_members_exist(self):
        assert ActionType.ROUTE == "route"
        assert ActionType.PAUSE_QUEUE == "pause_queue"
        assert ActionType.SET_MODEL_PROFILE == "set_model_profile"
        assert isinstance(ActionType.ROUTE, str)


class TestRule:
    def test_construct_minimal(self):
        r = Rule(rule_id="test-rule")
        assert r.rule_id == "test-rule"
        assert r.enabled is True
        assert r.priority == 100

    def test_construct_full(self):
        r = Rule(
            rule_id="my-rule",
            enabled=False,
            priority=5,
            scope="project",
            condition={"field": "status"},
            actions=[{"type": "route"}],
            audit_message="test audit",
        )
        assert r.rule_id == "my-rule"
        assert r.enabled is False
        assert r.priority == 5

    def test_rule_id_stripped_and_required(self):
        r = Rule(rule_id="  foo  ")
        assert r.rule_id == "foo"

    def test_rule_id_empty_raises(self):
        import pytest
        with pytest.raises(ValueError):
            Rule(rule_id="")


class TestRuleAction:
    def test_construct(self):
        ra = RuleAction(rule_id="r1", action_type="route")
        assert ra.rule_id == "r1"
        assert ra.action_type == "route"

    def test_fields_stripped(self):
        ra = RuleAction(rule_id="  r1  ", action_type="  route  ")
        assert ra.rule_id == "r1"
        assert ra.action_type == "route"


class TestApplyRuleActions:
    def test_empty_actions(self):
        result = apply_rule_actions([])
        assert result == {}

    def test_set_model_profile_flattened(self):
        result = apply_rule_actions([
            {"type": "set_model_profile", "profile_id": "ultra"},
        ])
        assert result["model_profile"] == "ultra"

    def test_set_model_profile_event_loop_format(self):
        result = apply_rule_actions([
            {"action_type": "set_model_profile", "params": {"profile_id": "ultra"}},
        ])
        assert result["model_profile"] == "ultra"

    def test_set_prompt_profile(self):
        result = apply_rule_actions([
            {"type": "set_prompt_profile", "profile_id": "concise"},
        ])
        assert result["prompt_profile"] == "concise"

    def test_set_quality_threshold(self):
        result = apply_rule_actions([
            {"type": "set_quality_threshold", "value": 0.85},
        ])
        assert result["quality_threshold"] == 0.85

    def test_enable_adaptive_routing(self):
        result = apply_rule_actions([
            {"type": "enable_adaptive_routing", "value": True},
        ])
        assert result["enable_adaptive_routing"] is True


class TestRuleEngine:
    def test_empty_engine(self):
        engine = RuleEngine()
        assert engine.evaluate({}) == []

    def test_evaluate_enabled_rule_matches(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={}),
        ])
        results = engine.evaluate({})
        assert len(results) == 1
        assert results[0]["rule_id"] == "r1"

    def test_disabled_rule_skipped(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", enabled=False, condition={}),
        ])
        assert engine.evaluate({}) == []

    def test_condition_eq_match(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "x.y", "op": "eq", "value": 1}),
        ])
        results = engine.evaluate({"x": {"y": 1}})
        assert len(results) == 1

    def test_condition_eq_no_match(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "x", "op": "eq", "value": 1}),
        ])
        assert engine.evaluate({"x": 2}) == []

    def test_condition_all(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"all": [
                {"field": "a", "op": "eq", "value": 1},
                {"field": "b", "op": "eq", "value": 2},
            ]}),
        ])
        assert len(engine.evaluate({"a": 1, "b": 2})) == 1
        assert engine.evaluate({"a": 1, "b": 3}) == []

    def test_condition_any(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"any": [
                {"field": "a", "op": "eq", "value": 1},
                {"field": "b", "op": "eq", "value": 999},
            ]}),
        ])
        assert len(engine.evaluate({"a": 1, "b": 2})) == 1

    def test_condition_in(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "x", "op": "in", "value": [1, 2, 3]}),
        ])
        assert len(engine.evaluate({"x": 2})) == 1

    def test_condition_contains(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "s", "op": "contains", "value": "abc"}),
        ])
        assert len(engine.evaluate({"s": "xxabcyy"})) == 1

    def test_condition_gt(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "v", "op": "gt", "value": 5}),
        ])
        assert len(engine.evaluate({"v": 10})) == 1

    def test_condition_lt(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "v", "op": "lt", "value": 5}),
        ])
        assert len(engine.evaluate({"v": 2})) == 1

    def test_resolve_field_dotted(self):
        engine = RuleEngine(rules=[
            Rule(rule_id="r1", condition={"field": "a.b.c", "op": "eq", "value": 99}),
        ])
        results = engine.evaluate({"a": {"b": {"c": 99}}})
        assert len(results) == 1

    def test_add_rule(self):
        engine = RuleEngine()
        engine.add_rule(Rule(rule_id="new-rule", condition={}))
        assert len(engine.evaluate({})) == 1


class TestEvaluateRules:
    def test_returns_rule_actions(self):
        rules = [Rule(rule_id="r1", actions=[{"type": "route", "queue": "qa"}])]
        actions = evaluate_rules(rules, {"queue": {"queue_name": "myq"}})
        assert len(actions) == 1
        assert actions[0].rule_id == "r1"
        assert actions[0].action_type == "route"


class TestDefaultRules:
    def test_returns_list_of_rules(self):
        rules = default_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 4
        assert all(isinstance(r, Rule) for r in rules)
