"""Deep feature flag engine tests — overrides, gradual rollouts, audit.

Covers: OverrideStore resolution (user/group/global), precedence, removal,
GradualRollout stage progression, FlagAuditLog queries and stats,
FlagEngine warm-up, batch evaluation, and edge cases.
"""

from __future__ import annotations

import threading
import time

import pytest

from general_ludd.feature_flags import FeatureFlag, FlagEvaluator, TargetingRule
from general_ludd.feature_flags.engine import (
    AuditEntry,
    FlagAuditLog,
    FlagEngine,
    GradualRollout,
    Override,
    OverrideLevel,
    OverrideStore,
    RolloutStage,
)


@pytest.fixture
def override_store() -> OverrideStore:
    return OverrideStore()


@pytest.fixture
def audit_log() -> FlagAuditLog:
    return FlagAuditLog()


@pytest.fixture
def flag_engine() -> FlagEngine:
    evaluator = FlagEvaluator(
        [
            FeatureFlag("dark_mode", default=True),
            FeatureFlag("new_search", default=False, rollout_percentage=50.0),
            FeatureFlag(
                "admin_tools",
                default=False,
                targeting_rules=[TargetingRule("role", "admin", operator="eq")],
            ),
            FeatureFlag("export", default=False, dependencies=["dark_mode"]),
        ]
    )
    return FlagEngine(evaluator=evaluator)


class TestOverrideStore:
    def test_global_override_resolves_for_any_entity(self, override_store: OverrideStore) -> None:
        override_store.set(Override("dark_mode", False, OverrideLevel.GLOBAL, reason="global kill"))
        assert override_store.resolve("dark_mode", {"id": "u1"}) is False
        assert override_store.resolve("dark_mode", {"id": "u2"}) is False
        assert override_store.resolve("dark_mode", {}) is False

    def test_user_override_only_matches_target(self, override_store: OverrideStore) -> None:
        override_store.set(Override("new_search", True, OverrideLevel.USER, target="user-42"))
        assert override_store.resolve("new_search", {"id": "user-42"}) is True
        assert override_store.resolve("new_search", {"id": "user-99"}) is None

    def test_group_override_matches_group_attribute(self, override_store: OverrideStore) -> None:
        override_store.set(Override("feature_z", True, OverrideLevel.GROUP, target="beta_testers"))
        assert override_store.resolve("feature_z", {"group": "beta_testers"}) is True
        assert override_store.resolve("feature_z", {"group": "prod"}) is None

    def test_higher_level_precedence(self, override_store: OverrideStore) -> None:
        override_store.set(Override("flag", True, OverrideLevel.GLOBAL, reason="on"))
        override_store.set(Override("flag", False, OverrideLevel.USER, target="user-1", reason="off for 1"))
        assert override_store.resolve("flag", {"id": "user-1"}) is False
        assert override_store.resolve("flag", {"id": "user-2"}) is True

    def test_resolve_returns_none_when_no_overrides(self, override_store: OverrideStore) -> None:
        assert override_store.resolve("nonexistent", {"id": "x"}) is None

    def test_remove_all_overrides_for_flag(self, override_store: OverrideStore) -> None:
        override_store.set(Override("flag", True, OverrideLevel.GLOBAL))
        override_store.set(Override("flag", False, OverrideLevel.USER, target="u1"))
        removed = override_store.remove("flag")
        assert removed == 2
        assert override_store.resolve("flag", {"id": "u1"}) is None

    def test_remove_by_target(self, override_store: OverrideStore) -> None:
        override_store.set(Override("flag", True, OverrideLevel.USER, target="u1"))
        override_store.set(Override("flag", False, OverrideLevel.USER, target="u2"))
        removed = override_store.remove("flag", target="u1")
        assert removed == 1
        assert override_store.resolve("flag", {"id": "u2"}) is False

    def test_user_id_field_fallback(self, override_store: OverrideStore) -> None:
        override_store.set(Override("flag", True, OverrideLevel.USER, target="abc-123"))
        assert override_store.resolve("flag", {"user_id": "abc-123"}) is True

    def test_clear_removes_all(self, override_store: OverrideStore) -> None:
        override_store.set(Override("a", True, OverrideLevel.GLOBAL))
        override_store.set(Override("b", False, OverrideLevel.GLOBAL))
        override_store.clear()
        assert override_store.resolve("a", {}) is None
        assert override_store.resolve("b", {}) is None

    def test_list_for_flag(self, override_store: OverrideStore) -> None:
        o1 = Override("flag", True, OverrideLevel.GLOBAL)
        o2 = Override("flag", False, OverrideLevel.USER, target="u1")
        override_store.set(o1)
        override_store.set(o2)
        listed = override_store.list_for_flag("flag")
        assert len(listed) == 2
        assert listed[0].level == OverrideLevel.USER


class TestGradualRollout:
    def test_stages_progression_manual(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [
            (RolloutStage.CANARY, 5.0),
            (RolloutStage.BETA, 25.0),
            (RolloutStage.STABLE, 100.0),
        ]
        rollout = GradualRollout("my_flag", stages, progression_condition="manual")
        assert rollout.current_stage == RolloutStage.CANARY
        assert rollout.current_percentage == 5.0
        assert rollout.advance() is False

    def test_force_stage_jumps_correctly(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [
            (RolloutStage.CANARY, 10.0),
            (RolloutStage.BETA, 50.0),
            (RolloutStage.STABLE, 100.0),
        ]
        rollout = GradualRollout("my_flag", stages, progression_condition="manual")
        rollout.force_stage(RolloutStage.STABLE)
        assert rollout.current_stage == RolloutStage.STABLE
        assert rollout.current_percentage == 100.0

    def test_force_stage_unknown_raises(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [(RolloutStage.CANARY, 10.0)]
        rollout = GradualRollout("my_flag", stages, progression_condition="manual")
        with pytest.raises(ValueError, match="not in rollout stages"):
            rollout.force_stage(RolloutStage.BETA)

    def test_days_elapsed_no_progression_before_time(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [
            (RolloutStage.CANARY, 10.0),
            (RolloutStage.BETA, 50.0),
        ]
        rollout = GradualRollout("my_flag", stages, progression_condition="days_elapsed", progression_value=7.0)
        assert rollout.advance() is False

    def test_days_elapsed_progression_after_time(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [
            (RolloutStage.CANARY, 10.0),
            (RolloutStage.BETA, 50.0),
        ]
        rollout = GradualRollout("my_flag", stages, progression_condition="days_elapsed", progression_value=0.0)
        assert rollout.advance() is True
        assert rollout.current_stage == RolloutStage.BETA
        assert rollout.current_percentage == 50.0

    def test_at_final_stage_advance_returns_false(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [(RolloutStage.STABLE, 100.0)]
        rollout = GradualRollout("my_flag", stages, progression_condition="days_elapsed", progression_value=0.0)
        assert rollout.advance() is False

    def test_empty_stages_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one rollout stage"):
            GradualRollout("flag", [])

    def test_to_feature_flag_respects_current_percentage(self) -> None:
        stages: list[tuple[RolloutStage, float]] = [(RolloutStage.BETA, 30.0)]
        rollout = GradualRollout("my_flag", stages)
        ff = rollout.to_feature_flag(default=True)
        assert ff.name == "my_flag"
        assert ff.rollout_percentage == 30.0
        assert ff.default is True


class TestFlagAuditLog:
    def test_record_and_query_all(self, audit_log: FlagAuditLog) -> None:
        audit_log.record(AuditEntry("f1", "u1", True, "ok"))
        audit_log.record(AuditEntry("f2", "u2", False, "disabled"))
        all_entries = audit_log.query()
        assert len(all_entries) == 2

    def test_query_by_flag_name(self, audit_log: FlagAuditLog) -> None:
        audit_log.record(AuditEntry("dark", "u1", True, "on"))
        audit_log.record(AuditEntry("light", "u2", False, "off"))
        dark = audit_log.query(flag_name="dark")
        assert len(dark) == 1
        assert dark[0].flag_name == "dark"

    def test_query_by_entity_id(self, audit_log: FlagAuditLog) -> None:
        audit_log.record(AuditEntry("flag", "alice", True, "ok"))
        audit_log.record(AuditEntry("flag", "bob", False, "nope"))
        alice = audit_log.query(entity_id="alice")
        assert len(alice) == 1
        assert alice[0].entity_id == "alice"

    def test_query_by_since_timestamp(self, audit_log: FlagAuditLog) -> None:
        t0 = time.time()
        audit_log.record(AuditEntry("f", "u1", True, "ok", timestamp=t0 - 100))
        audit_log.record(AuditEntry("f", "u2", False, "nope", timestamp=t0 + 100))
        recent = audit_log.query(since=t0)
        assert len(recent) == 1
        assert recent[0].entity_id == "u2"

    def test_stats_calculates_ratio(self, audit_log: FlagAuditLog) -> None:
        audit_log.record(AuditEntry("f", "u1", True, "1"))
        audit_log.record(AuditEntry("f", "u2", True, "2"))
        audit_log.record(AuditEntry("f", "u3", False, "3"))
        audit_log.record(AuditEntry("f", "u4", False, "4"))
        s = audit_log.stats(flag_name="f")
        assert s["total"] == 4
        assert s["enabled"] == 2
        assert s["disabled"] == 2
        assert s["ratio"] == 0.5

    def test_stats_empty_returns_zeroes(self, audit_log: FlagAuditLog) -> None:
        s = audit_log.stats()
        assert s["total"] == 0

    def test_clear_removes_all(self, audit_log: FlagAuditLog) -> None:
        audit_log.record(AuditEntry("f", "u1", True, "ok"))
        audit_log.clear()
        assert audit_log.query() == []

    def test_max_entries_trimming(self) -> None:
        small_log = FlagAuditLog(max_entries=3)
        for i in range(5):
            small_log.record(AuditEntry("f", f"u{i}", True, str(i)))
        entries = small_log.query()
        assert len(entries) == 3
        assert entries[0].entity_id == "u2"


class TestFlagEngine:
    def test_override_takes_priority_in_evaluate(self, flag_engine: FlagEngine) -> None:
        flag_engine.overrides.set(Override("dark_mode", False, OverrideLevel.GLOBAL, reason="kill switch"))
        result = flag_engine.evaluate("dark_mode", {"id": "u1"})
        assert result.enabled is False
        assert "override" in result.reason.lower()

    def test_evaluate_falls_back_to_evaluator(self, flag_engine: FlagEngine) -> None:
        result = flag_engine.evaluate("dark_mode", {"id": "u1"})
        assert result.enabled is True

    def test_evaluate_records_audit(self, flag_engine: FlagEngine) -> None:
        flag_engine.evaluate("dark_mode", {"id": "u42"})
        entries = flag_engine.audit_log.query(entity_id="u42")
        assert len(entries) == 1
        assert entries[0].flag_name == "dark_mode"

    def test_warm_up_batch_evaluation(self, flag_engine: FlagEngine) -> None:
        entities = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        result = flag_engine.warm_up(entities)
        assert set(result.keys()) == {"a", "b", "c"}
        for eid in ("a", "b", "c"):
            assert "dark_mode" in result[eid]
            assert "new_search" in result[eid]

    def test_register_rollout_and_evaluate(self, flag_engine: FlagEngine) -> None:
        stages: list[tuple[RolloutStage, float]] = [(RolloutStage.CANARY, 100.0)]
        rollout = GradualRollout("canary_feat", stages)
        flag_engine.register_rollout(rollout)
        result = flag_engine.evaluate("canary_feat", {"id": "u1"})
        assert result.enabled is True
        assert "flag enabled" in result.reason

    def test_advance_rollouts_updates_percentage(self, flag_engine: FlagEngine) -> None:
        evaluator = FlagEvaluator([FeatureFlag("staged", default=True, rollout_percentage=5.0)])
        engine = FlagEngine(evaluator=evaluator)
        stages: list[tuple[RolloutStage, float]] = [
            (RolloutStage.CANARY, 5.0),
            (RolloutStage.BETA, 40.0),
        ]
        rollout = GradualRollout("staged", stages, progression_condition="days_elapsed", progression_value=0.0)
        engine.register_rollout(rollout)
        results = engine.advance_rollouts()
        assert results["staged"] is True
        assert engine.is_enabled("staged", {"id": "u1"}) is True

    def test_evaluate_all_includes_rollouts(self, flag_engine: FlagEngine) -> None:
        stages: list[tuple[RolloutStage, float]] = [(RolloutStage.CANARY, 100.0)]
        flag_engine.register_rollout(GradualRollout("extra", stages))
        results = flag_engine.evaluate_all({"id": "u1"})
        assert "dark_mode" in results
        assert "extra" in results

    def test_thread_safe_override_concurrent_access(self) -> None:
        store = OverrideStore()
        engine = FlagEngine(overrides=store)
        engine.evaluator.register(FeatureFlag("shared", default=True))
        errors: list[Exception] = []

        def setter() -> None:
            for i in range(200):
                try:
                    store.set(Override("shared", i % 2 == 0, OverrideLevel.USER, target=f"user-{i}"))
                except Exception as e:
                    errors.append(e)

        def reader() -> None:
            for _ in range(200):
                try:
                    engine.is_enabled("shared", {"id": "user-50"})
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=setter), threading.Thread(target=setter), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestOverrideConstruction:
    def test_override_fields(self) -> None:
        o = Override("flag", True, OverrideLevel.USER, target="u99", reason="test")
        assert o.flag_name == "flag"
        assert o.value is True
        assert o.level == OverrideLevel.USER
        assert o.target == "u99"
        assert o.reason == "test"

    def test_matches_non_matching_flag(self) -> None:
        o = Override("flag_a", True, OverrideLevel.GLOBAL)
        assert o.matches("flag_b", {}) is False

    def test_override_level_ordering(self) -> None:
        assert OverrideLevel.GLOBAL < OverrideLevel.USER


class TestFlagEngineEdgeCases:
    def test_empty_entity_warm_up(self) -> None:
        engine = FlagEngine()
        result = engine.warm_up([])
        assert result == {}

    def test_override_remove_nonexistent_flag(self) -> None:
        store = OverrideStore()
        assert store.remove("nope") == 0

    def test_audit_query_combined_filters(self) -> None:
        log = FlagAuditLog()
        t0 = time.time()
        log.record(AuditEntry("f1", "u1", True, "ok", timestamp=t0 + 10))
        log.record(AuditEntry("f1", "u2", False, "nope", timestamp=t0 + 20))
        log.record(AuditEntry("f2", "u1", True, "yep", timestamp=t0 + 30))
        results = log.query(flag_name="f1", entity_id="u1")
        assert len(results) == 1
        assert results[0].enabled is True
