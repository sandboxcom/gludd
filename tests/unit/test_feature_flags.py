"""Deep feature flag and rollout tests.

Covers: flag evaluation, percentage rollout, targeting rules, default values,
flag dependency chains, thread safety, hash stability, and edge cases.
"""

from __future__ import annotations

import threading

import pytest

from general_ludd.feature_flags import (
    FeatureFlag,
    FlagEvaluator,
    TargetingRule,
    _stable_hash,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def evaluator() -> FlagEvaluator:
    return FlagEvaluator([])


@pytest.fixture
def populated_evaluator() -> FlagEvaluator:
    return FlagEvaluator(
        [
            FeatureFlag("enable_new_ui", default=False, rollout_percentage=50.0),
            FeatureFlag("dark_mode", default=True),
            FeatureFlag(
                "admin_panel",
                default=False,
                targeting_rules=[TargetingRule("role", "admin", operator="eq")],
            ),
            FeatureFlag("beta_search", default=False, dependencies=["dark_mode"]),
            FeatureFlag(
                "advanced_analytics",
                default=False,
                dependencies=["beta_search", "admin_panel"],
                depends_on_all=True,
            ),
            FeatureFlag(
                "export_csv",
                default=False,
                dependencies=["dark_mode", "enable_new_ui"],
                depends_on_all=False,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# _stable_hash
# ---------------------------------------------------------------------------


class TestStableHash:
    def test_deterministic(self) -> None:
        a = _stable_hash("user-42")
        b = _stable_hash("user-42")
        assert a == b

    def test_different_keys_produce_different_hashes(self) -> None:
        h1 = _stable_hash("user-1")
        h2 = _stable_hash("user-2")
        assert h1 != h2

    def test_seed_changes_hash(self) -> None:
        h1 = _stable_hash("user-42", seed="flag_a")
        h2 = _stable_hash("user-42", seed="flag_b")
        assert h1 != h2

    def test_range_is_0_to_9999(self) -> None:
        for i in range(500):
            h = _stable_hash(f"user-{i}", seed=f"flag-{i % 10}")
            assert 0 <= h < 10_000


# ---------------------------------------------------------------------------
# FeatureFlag construction
# ---------------------------------------------------------------------------


class TestFeatureFlagConstruction:
    def test_basic_flag(self) -> None:
        f = FeatureFlag("my_flag", default=True, description="test flag")
        assert f.name == "my_flag"
        assert f.default is True
        assert f.description == "test flag"
        assert f.rollout_percentage == 100.0
        assert f.targeting_rules == []
        assert f.dependencies == []

    def test_name_must_be_valid_identifier(self) -> None:
        with pytest.raises(ValueError, match="valid identifier"):
            FeatureFlag("123bad")

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="valid identifier"):
            FeatureFlag("")

    def test_rollout_percentage_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="rollout_percentage"):
            FeatureFlag("flag", rollout_percentage=-1.0)
        with pytest.raises(ValueError, match="rollout_percentage"):
            FeatureFlag("flag", rollout_percentage=100.1)

    def test_default_false_when_not_specified(self) -> None:
        f = FeatureFlag("flag")
        assert f.default is False


# ---------------------------------------------------------------------------
# FlagEvaluator -- registration
# ---------------------------------------------------------------------------


class TestFlagRegistration:
    def test_register_and_retrieve(self, evaluator: FlagEvaluator) -> None:
        f = FeatureFlag("test")
        evaluator.register(f)
        assert evaluator.get_flag("test") is f

    def test_unregister(self, evaluator: FlagEvaluator) -> None:
        f = FeatureFlag("test")
        evaluator.register(f)
        evaluator.unregister("test")
        assert evaluator.get_flag("test") is None

    def test_unregister_nonexistent_does_not_raise(self, evaluator: FlagEvaluator) -> None:
        evaluator.unregister("nope")

    def test_list_flags_sorted(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag("z_flag"),
            FeatureFlag("a_flag"),
            FeatureFlag("m_flag"),
        )
        assert evaluator.list_flags() == ["a_flag", "m_flag", "z_flag"]

    def test_register_overwrites_existing(self, evaluator: FlagEvaluator) -> None:
        f1 = FeatureFlag("test", default=False)
        f2 = FeatureFlag("test", default=True)
        evaluator.register(f1)
        evaluator.register(f2)
        assert evaluator.get_flag("test") is f2


# ---------------------------------------------------------------------------
# FlagEvaluator -- evaluation
# ---------------------------------------------------------------------------


class TestFlagEvaluation:
    def test_default_true_flag(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("enabled_feat", default=True))
        result = evaluator.evaluate("enabled_feat", {})
        assert result.enabled is True
        assert "default value: True" in result.reason

    def test_default_false_flag(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("disabled_feat", default=False))
        result = evaluator.evaluate("disabled_feat", {})
        assert result.enabled is False
        assert "default value: False" in result.reason

    def test_unknown_flag_returns_false(self, evaluator: FlagEvaluator) -> None:
        result = evaluator.evaluate("nonexistent", {})
        assert result.enabled is False
        assert "not registered" in result.reason

    def test_is_enabled_convenience(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("feat", default=True))
        assert evaluator.is_enabled("feat", {}) is True
        assert evaluator.is_enabled("unknown", {}) is False

    def test_flag_with_conditions_enabled(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag("dad", default=True),
            FeatureFlag("child", default=False, dependencies=["dad"]),
        )
        assert evaluator.is_enabled("child", {"id": "u"}) is True

    def test_flag_without_conditions_uses_default(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("bare_true", default=True))
        evaluator.register(FeatureFlag("bare_false", default=False))
        assert evaluator.is_enabled("bare_true", {}) is True
        assert evaluator.is_enabled("bare_false", {}) is False


# ---------------------------------------------------------------------------
# percentage rollout
# ---------------------------------------------------------------------------


class TestPercentageRollout:
    def test_100_percent_always_enabled(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("full_rollout", default=True, rollout_percentage=100.0))
        for uid in range(100):
            assert evaluator.is_enabled("full_rollout", {}, entity_id=str(uid))

    def test_0_percent_never_enabled(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("no_rollout", default=True, rollout_percentage=0.0))
        for uid in range(100):
            assert not evaluator.is_enabled("no_rollout", {}, entity_id=str(uid))

    def test_50_percent_approximate_split(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("half_rollout", default=True, rollout_percentage=50.0))
        enabled = 0
        total = 500
        for uid in range(total):
            if evaluator.is_enabled("half_rollout", {}, entity_id=str(uid)):
                enabled += 1
        ratio = enabled / total
        assert 0.40 < ratio < 0.60, f"expected ~50%, got {ratio:.2%}"

    def test_deterministic_for_same_entity(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("deterministic", default=True, rollout_percentage=30.0))
        results = {evaluator.is_enabled("deterministic", {}, entity_id="user-99") for _ in range(20)}
        assert len(results) == 1

    def test_rollout_disabled_reason(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("limited", default=True, rollout_percentage=0.5))
        disabled = 0
        for uid in range(500):
            r = evaluator.evaluate("limited", {}, entity_id=str(uid))
            if not r.enabled and "rollout disabled" in r.reason:
                disabled += 1
        assert disabled > 200


# ---------------------------------------------------------------------------
# targeting rules
# ---------------------------------------------------------------------------


class TestTargetingRules:
    def test_eq_rule_matches(self) -> None:
        rule = TargetingRule("role", "admin", operator="eq")
        assert rule.matches({"role": "admin"})
        assert not rule.matches({"role": "user"})
        assert not rule.matches({})

    def test_neq_rule(self) -> None:
        rule = TargetingRule("env", "production", operator="neq")
        assert rule.matches({"env": "staging"})
        assert not rule.matches({"env": "production"})

    def test_in_rule(self) -> None:
        rule = TargetingRule("group", ["alpha", "beta"], operator="in")
        assert rule.matches({"group": "alpha"})
        assert rule.matches({"group": "beta"})
        assert not rule.matches({"group": "gamma"})

    def test_inverted_rule(self) -> None:
        rule = TargetingRule("role", "admin", operator="eq", invert=True)
        assert rule.matches({"role": "user"})
        assert not rule.matches({"role": "admin"})

    def test_regex_rule(self) -> None:
        rule = TargetingRule("email", r"@example\.com$", operator="regex")
        assert rule.matches({"email": "user@example.com"})
        assert not rule.matches({"email": "user@other.org"})

    def test_targeting_overrides_rollout(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag(
                "staff_only",
                default=False,
                rollout_percentage=100.0,
                targeting_rules=[TargetingRule("staff", True, operator="eq")],
            )
        )
        assert evaluator.is_enabled("staff_only", {"staff": True}, entity_id="any")
        assert not evaluator.is_enabled("staff_only", {"staff": False}, entity_id="any")

    def test_inverted_targeting_excludes(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag(
                "legacy",
                default=True,
                targeting_rules=[TargetingRule("tenant", "new_cloud", operator="eq", invert=True)],
            )
        )
        assert evaluator.is_enabled("legacy", {"tenant": "old_farm"})
        excluded = evaluator.evaluate("legacy", {"tenant": "new_cloud"})
        assert excluded.enabled is False
        assert excluded.reason == "no targeting rules matched"

    def test_targeting_enables_override_default_false(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag(
                "beta_users",
                default=False,
                targeting_rules=[TargetingRule("group", "beta", operator="eq")],
            )
        )
        assert evaluator.is_enabled("beta_users", {"group": "beta"})
        assert not evaluator.is_enabled("beta_users", {"group": "stable"})


# ---------------------------------------------------------------------------
# flag dependency chains
# ---------------------------------------------------------------------------


class TestFlagDependencyChains:
    def test_depends_on_all_enabled(self, populated_evaluator: FlagEvaluator) -> None:
        entity = {"id": "u1"}
        assert populated_evaluator.is_enabled("dark_mode", entity)
        assert populated_evaluator.is_enabled("beta_search", entity)

    def test_depends_on_all_disabled(self, populated_evaluator: FlagEvaluator) -> None:
        entity = {"id": "u1"}
        result = populated_evaluator.evaluate("advanced_analytics", entity)
        assert result.enabled is False
        assert "dependency" in result.reason.lower()

    def test_depends_on_any_sufficient(self, populated_evaluator: FlagEvaluator) -> None:
        entity = {"id": "u1"}
        result = populated_evaluator.evaluate("export_csv", entity)
        assert result.enabled is True

    def test_disabled_dependency_blocks(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag("parent", default=False),
            FeatureFlag("child", default=False, dependencies=["parent"], depends_on_all=True),
        )
        assert evaluator.is_enabled("parent", {"id": "x"}) is False
        assert evaluator.is_enabled("child", {"id": "x"}) is False
        reason = evaluator.evaluate("child", {"id": "x"}).reason
        assert "parent" in reason

    def test_transitive_dependency_block(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(
            FeatureFlag("a", default=False),
            FeatureFlag("b", default=False, dependencies=["a"]),
            FeatureFlag("c", default=False, dependencies=["b"], depends_on_all=True),
        )
        assert not evaluator.is_enabled("c", {"id": "x"})

    def test_resolve_chain_returns_ordered_results(self, populated_evaluator: FlagEvaluator) -> None:
        chain = populated_evaluator.resolve_chain("advanced_analytics", {"id": "u1"})
        names = [r.flag_name for r in chain]
        assert "dark_mode" in names
        assert "beta_search" in names
        assert "admin_panel" in names
        assert "advanced_analytics" in names


# ---------------------------------------------------------------------------
# evaluate_all
# ---------------------------------------------------------------------------


class TestEvaluateAll:
    def test_returns_all_flags(self, populated_evaluator: FlagEvaluator) -> None:
        results = populated_evaluator.evaluate_all({"id": "u1"})
        assert set(results.keys()) == {
            "enable_new_ui",
            "dark_mode",
            "admin_panel",
            "beta_search",
            "advanced_analytics",
            "export_csv",
        }
        assert results["dark_mode"].enabled is True

    def test_empty_evaluator(self, evaluator: FlagEvaluator) -> None:
        assert evaluator.evaluate_all({}) == {}


# ---------------------------------------------------------------------------
# entity ID fallback
# ---------------------------------------------------------------------------


class TestEntityIdFallback:
    def test_uses_explicit_entity_id(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("f", default=True, rollout_percentage=50.0))
        r1 = evaluator.evaluate("f", {"id": "x"}, entity_id="overridden")
        r2 = evaluator.evaluate("f", {"id": "x"}, entity_id="overridden")
        assert r1.enabled == r2.enabled

    def test_falls_back_to_entity_id_field(self, evaluator: FlagEvaluator) -> None:
        evaluator.register(FeatureFlag("f", default=True, rollout_percentage=50.0))
        r = evaluator.evaluate("f", {"id": "entity-77"})
        assert r.flag_name == "f"


# ---------------------------------------------------------------------------
# thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_registration_and_evaluation(self) -> None:
        evaluator = FlagEvaluator([FeatureFlag("shared", default=True)])
        errors: list[Exception] = []

        def writer() -> None:
            for i in range(100):
                try:
                    evaluator.register(FeatureFlag(f"flag_{i}", default=True))
                except Exception as e:
                    errors.append(e)

        def reader() -> None:
            for _ in range(100):
                try:
                    evaluator.is_enabled("shared", {"id": "r"})
                    evaluator.list_flags()
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# TargetingRule construction
# ---------------------------------------------------------------------------


class TestTargetingRuleConstruction:
    def test_unsupported_operator_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported operator"):
            TargetingRule("attr", "val", operator="gt")

    def test_supported_operators_do_not_raise(self) -> None:
        for op in ("eq", "in", "neq", "regex"):
            TargetingRule("attr", "val", operator=op)
