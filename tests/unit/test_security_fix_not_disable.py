"""Unit tests for security/fix_not_disable.py — enforce repair-over-disable policy."""

from __future__ import annotations

from general_ludd.security.fix_not_disable import (
    ActionIntent,
    DISABLE_PATTERNS,
    FixNotDisablePolicy,
    default_fix_not_disable_policy,
    is_disabling_action,
)


class TestModuleImports:
    def test_all_exports_importable(self) -> None:
        assert ActionIntent is not None
        assert DISABLE_PATTERNS is not None
        assert FixNotDisablePolicy is not None
        assert default_fix_not_disable_policy is not None
        assert is_disabling_action is not None


class TestDisablePatterns:
    def test_is_frozenset(self) -> None:
        assert isinstance(DISABLE_PATTERNS, frozenset)

    def test_contains_core_disable_keywords(self) -> None:
        assert "skip" in DISABLE_PATTERNS
        assert "disable" in DISABLE_PATTERNS
        assert "stub" in DISABLE_PATTERNS
        assert "remove" in DISABLE_PATTERNS
        assert "delete" in DISABLE_PATTERNS
        assert "bypass" in DISABLE_PATTERNS

    def test_contains_lint_suppression(self) -> None:
        assert "# noqa" in DISABLE_PATTERNS

    def test_contains_test_skip_markers(self) -> None:
        assert "xfail" in DISABLE_PATTERNS
        assert "pytest.mark.skip" in DISABLE_PATTERNS

    def test_contains_mock_terms(self) -> None:
        assert "mock out" in DISABLE_PATTERNS
        assert "no-op" in DISABLE_PATTERNS
        assert "noop" in DISABLE_PATTERNS

    def test_non_empty(self) -> None:
        assert len(DISABLE_PATTERNS) >= 15


class TestIsDisablingAction:
    def test_exact_match_returns_true(self) -> None:
        assert is_disabling_action("disable the check") is True

    def test_substring_match_returns_true(self) -> None:
        assert is_disabling_action("we should delete the old code path") is True

    def test_case_insensitive_match(self) -> None:
        assert is_disabling_action("Skip this test") is True

    def test_no_match_returns_false(self) -> None:
        assert is_disabling_action("fix the broken assertion") is False

    def test_no_match_on_repair_language(self) -> None:
        assert is_disabling_action("implement the new feature") is False
        assert is_disabling_action("refactor the loop") is False
        assert is_disabling_action("add type annotations") is False

    def test_empty_string_returns_false(self) -> None:
        assert is_disabling_action("") is False

    def test_pattern_at_end_of_string(self) -> None:
        assert is_disabling_action("turn off") is True

    def test_noop_detected(self) -> None:
        assert is_disabling_action("make this a no-op") is True


class TestActionIntent:
    def test_create_with_all_fields(self) -> None:
        intent = ActionIntent(action_type="edit", target="guardrail", reason="fixes bug")
        assert intent.action_type == "edit"
        assert intent.target == "guardrail"
        assert intent.reason == "fixes bug"

    def test_create_minimal(self) -> None:
        intent = ActionIntent(action_type="", target="", reason="")
        assert intent.action_type == ""


class TestFixNotDisablePolicyDefaults:
    def test_fail_closed_default_is_true(self) -> None:
        policy = FixNotDisablePolicy()
        assert policy.fail_closed is True

    def test_allowed_repair_keywords_defaults(self) -> None:
        policy = FixNotDisablePolicy()
        assert isinstance(policy.allowed_repair_keywords, list)
        assert "fix" in policy.allowed_repair_keywords
        assert "repair" in policy.allowed_repair_keywords
        assert "implement" in policy.allowed_repair_keywords
        assert "refactor" in policy.allowed_repair_keywords

    def test_keywords_are_non_empty(self) -> None:
        policy = FixNotDisablePolicy()
        assert len(policy.allowed_repair_keywords) >= 5


class TestFixNotDisablePolicyCheckActionFailClosed:
    def test_disable_action_blocked(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, reason = policy.check_action("delete the guardrail")
        assert allowed is False
        assert "disabling" in reason

    def test_repair_action_allowed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, reason = policy.check_action("fix the broken test")
        assert allowed is True
        assert reason == "allowed"

    def test_disable_action_with_repair_keyword_still_blocked(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, reason = policy.check_action("fix by deleting the check")
        assert allowed is False

    def test_neutral_action_allowed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _ = policy.check_action("review the output")
        assert allowed is True


class TestFixNotDisablePolicyCheckActionFailOpen:
    def test_disable_without_repair_blocked(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, reason = policy.check_action("delete this file")
        assert allowed is False
        assert "disabling" in reason

    def test_disable_with_repair_keyword_allowed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("fix by removing the bypass")
        assert allowed is True

    def test_repair_only_allowed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("implement the feature")
        assert allowed is True


class TestDefaultFixNotDisablePolicy:
    def test_returns_fix_not_disable_policy(self) -> None:
        policy = default_fix_not_disable_policy()
        assert isinstance(policy, FixNotDisablePolicy)

    def test_default_has_fail_closed(self) -> None:
        policy = default_fix_not_disable_policy()
        assert policy.fail_closed is True

    def test_default_policy_blocks_disable_actions(self) -> None:
        policy = default_fix_not_disable_policy()
        allowed, reason = policy.check_action("skip the guardrail")
        assert allowed is False
        assert "disabling" in reason


class TestFixNotDisablePolicyFieldMutability:
    def test_can_override_fail_closed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        assert policy.fail_closed is False

    def test_can_override_repair_keywords(self) -> None:
        policy = FixNotDisablePolicy(allowed_repair_keywords=["enhance", "optimize"])
        assert policy.allowed_repair_keywords == ["enhance", "optimize"]

    def test_custom_keywords_override_defaults(self) -> None:
        policy = FixNotDisablePolicy(allowed_repair_keywords=["rewrite"])
        assert "fix" not in policy.allowed_repair_keywords
        assert "rewrite" in policy.allowed_repair_keywords

    def test_custom_keywords_affect_check_action(self) -> None:
        policy = FixNotDisablePolicy(
            fail_closed=False,
            allowed_repair_keywords=["rewrite"],
        )
        allowed, _ = policy.check_action("rewrite the delete logic")
        assert allowed is True


class TestCheckActionContextParameter:
    def test_context_accepted_but_unused_in_default(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, reason = policy.check_action(
            "fix the bug", context="triggered by commit abc123"
        )
        assert allowed is True
        assert reason == "allowed"

    def test_context_does_not_change_blocking(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, _ = policy.check_action(
            "skip the test", context="CI is red, need to unblock"
        )
        assert allowed is False
