"""Unit tests for account/lifecycle_policy.py."""

from __future__ import annotations

import pytest

from general_ludd.account.lifecycle_policy import (
    LifecycleAction,
    PolicyConfig,
    evaluate_lifecycle,
)


class TestLifecycleAction:
    def test_create_value(self):
        assert LifecycleAction.CREATE.value == "create"

    def test_keep_value(self):
        assert LifecycleAction.KEEP.value == "keep"

    def test_delete_value(self):
        assert LifecycleAction.DELETE.value == "delete"

    def test_membership(self):
        assert set(LifecycleAction) == {
            LifecycleAction.CREATE,
            LifecycleAction.KEEP,
            LifecycleAction.DELETE,
        }


class TestPolicyConfig:
    def test_defaults(self):
        cfg = PolicyConfig()
        assert cfg.auto_delete_after_use is True
        assert cfg.retention_period_hours == 24
        assert cfg.budget_limit == 10.0

    def test_custom_values(self):
        cfg = PolicyConfig(
            auto_delete_after_use=False,
            retention_period_hours=48,
            budget_limit=5.0,
        )
        assert cfg.auto_delete_after_use is False
        assert cfg.retention_period_hours == 48
        assert cfg.budget_limit == 5.0

    def test_zero_retention_raises(self):
        with pytest.raises(ValueError, match="retention_period_hours must be > 0"):
            PolicyConfig(retention_period_hours=0)

    def test_negative_retention_raises(self):
        with pytest.raises(ValueError, match="retention_period_hours must be > 0"):
            PolicyConfig(retention_period_hours=-1)

    def test_negative_budget_raises(self):
        with pytest.raises(ValueError, match="budget_limit must be >= 0"):
            PolicyConfig(budget_limit=-0.01)

    def test_zero_budget_allowed(self):
        cfg = PolicyConfig(budget_limit=0.0)
        assert cfg.budget_limit == 0.0

    def test_to_dict(self):
        cfg = PolicyConfig(
            auto_delete_after_use=False,
            retention_period_hours=12,
            budget_limit=20.0,
        )
        d = cfg.to_dict()
        assert d["auto_delete_after_use"] is False
        assert d["retention_period_hours"] == 12
        assert d["budget_limit"] == 20.0

    def test_repr(self):
        cfg = PolicyConfig()
        r = repr(cfg)
        assert "PolicyConfig" in r
        assert "auto_delete_after_use=True" in r
        assert "retention_period_hours=24" in r
        assert "budget_limit=10.0" in r

    def test_supports_protocol(self):
        class CompatiblePolicy:
            auto_delete_after_use: bool = True
            retention_period_hours: int = 24

        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=CompatiblePolicy(),
            active=True,
            age_hours=10.0,
        )
        assert action is not None


class TestEvaluateLifecycle:
    def test_no_account_returns_create(self):
        action = evaluate_lifecycle(
            account_id=None,
            policy=PolicyConfig(),
            active=False,
            age_hours=0,
        )
        assert action == LifecycleAction.CREATE

    def test_auto_delete_off_returns_keep(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=False),
            active=True,
            age_hours=1000,
        )
        assert action == LifecycleAction.KEEP

    def test_inactive_returns_delete(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=False,
            age_hours=10,
        )
        assert action == LifecycleAction.DELETE

    def test_past_retention_returns_delete(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=True,
            age_hours=24,
        )
        assert action == LifecycleAction.DELETE

    def test_well_past_retention_returns_delete(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=True,
            age_hours=100,
        )
        assert action == LifecycleAction.DELETE

    def test_within_retention_returns_keep(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=True,
            age_hours=10,
        )
        assert action == LifecycleAction.KEEP

    def test_exactly_at_retention_deletes(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=True,
            age_hours=24.0,
        )
        assert action == LifecycleAction.DELETE

    def test_just_below_retention_keeps(self):
        action = evaluate_lifecycle(
            account_id="acct-1",
            policy=PolicyConfig(auto_delete_after_use=True, retention_period_hours=24),
            active=True,
            age_hours=23.999,
        )
        assert action == LifecycleAction.KEEP

    def test_account_id_none_overrides_inactive(self):
        action = evaluate_lifecycle(
            account_id=None,
            policy=PolicyConfig(auto_delete_after_use=True),
            active=False,
            age_hours=1000,
        )
        assert action == LifecycleAction.CREATE
