"""Tests for PG-3 fix-not-disable policy."""
from __future__ import annotations

from general_ludd.security.fix_not_disable import (
    FixNotDisablePolicy,
    default_fix_not_disable_policy,
    is_disabling_action,
)


class TestFixNotDisablePolicy:
    def test_repair_action_allowed(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("fix the broken connection")
        assert allowed is True
        assert msg == "allowed"

    def test_disable_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("disable the failing test")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_skip_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("skip this test case")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_xfail_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("mark as xfail")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_stub_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("stub out the API call")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_bypass_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("bypass the security check")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_case_insensitive(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("DISABLE the feature")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_empty_string(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("")
        assert allowed is True
        assert msg == "allowed"

    def test_no_keywords_repair(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("refactor the database layer")
        assert allowed is True
        assert msg == "allowed"

    def test_remove_action_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, msg = policy.check_action("remove the test")
        assert allowed is False
        assert "disabling pattern" in msg

    def test_is_disabling_action_true(self) -> None:
        assert is_disabling_action("skip this") is True

    def test_is_disabling_action_false(self) -> None:
        assert is_disabling_action("fix the bug") is False

    def test_policy_instantiation(self) -> None:
        policy = FixNotDisablePolicy()
        assert policy.fail_closed is True
        assert "fix" in policy.allowed_repair_keywords
        assert "repair" in policy.allowed_repair_keywords

    def test_fail_closed_blocks_ambiguous(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        # "fix and disable" — contains both repair AND disable keywords
        # fail_closed=True should block because disable pattern is present
        allowed, msg = policy.check_action("fix and disable the old handler")
        assert allowed is False
        assert "disabling pattern" in msg


class TestDefaultPolicy:
    def test_default_policy_returns_instance(self) -> None:
        policy = default_fix_not_disable_policy()
        assert isinstance(policy, FixNotDisablePolicy)
        assert policy.fail_closed is True
