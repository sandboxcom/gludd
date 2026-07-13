"""Unit tests for the fix-not-disable security policy."""

from __future__ import annotations

from general_ludd.security.fix_not_disable import (
    DISABLE_PATTERNS,
    ActionIntent,
    FixNotDisablePolicy,
    default_fix_not_disable_policy,
    is_disabling_action,
)


class TestActionIntent:
    def test_fields(self) -> None:
        intent = ActionIntent(
            action_type="edit",
            target="src/main.py",
            reason="fix format",
        )
        assert intent.action_type == "edit"
        assert intent.target == "src/main.py"
        assert intent.reason == "fix format"


class TestDisablePatterns:
    def test_skip_is_disable(self) -> None:
        assert is_disabling_action("skip the failing test")
        assert is_disabling_action("SKIP this step")

    def test_disable_is_disable(self) -> None:
        assert is_disabling_action("disable the check")
        assert is_disabling_action("I will disable that feature")

    def test_stub_is_disable(self) -> None:
        assert is_disabling_action("stub out the database call")

    def test_remove_is_disable(self) -> None:
        assert is_disabling_action("remove the guardrail")

    def test_bypass_is_disable(self) -> None:
        assert is_disabling_action("bypass the validation")

    def test_comment_out_is_disable(self) -> None:
        assert is_disabling_action("comment out the failing line")

    def test_xfail_is_disable(self) -> None:
        assert is_disabling_action("xfail the test")

    def test_pytest_mark_skip_is_disable(self) -> None:
        assert is_disabling_action("pytest.mark.skip this test")

    def test_delete_is_disable(self) -> None:
        assert is_disabling_action("delete the unused function")

    def test_deactivate_is_disable(self) -> None:
        assert is_disabling_action("deactivate the hook")

    def test_turn_off_is_disable(self) -> None:
        assert is_disabling_action("turn off the enforcement")

    def test_workaround_is_disable(self) -> None:
        assert is_disabling_action("workaround the auth check")

    def test_mock_out_is_disable(self) -> None:
        assert is_disabling_action("mock out the database")

    def test_noop_is_disable(self) -> None:
        assert is_disabling_action("noop the validator")
        assert is_disabling_action("make this a no-op")

    def test_repair_is_not_disable(self) -> None:
        assert not is_disabling_action("fix the test")
        assert not is_disabling_action("repair the broken module")
        assert not is_disabling_action("implement the feature")
        assert not is_disabling_action("refactor the handler")

    def test_empty_string(self) -> None:
        assert not is_disabling_action("")

    def test_case_insensitive(self) -> None:
        assert is_disabling_action("SKIP THE TEST")

    def test_disable_patterns_frozenset_contains_keywords(self) -> None:
        assert "skip" in DISABLE_PATTERNS
        assert "disable" in DISABLE_PATTERNS
        assert "remove" in DISABLE_PATTERNS
        assert "bypass" in DISABLE_PATTERNS
        assert "xfail" in DISABLE_PATTERNS
        assert "delete" in DISABLE_PATTERNS


class TestFixNotDisablePolicy:
    def test_default_policy_fail_closed(self) -> None:
        policy = FixNotDisablePolicy()
        assert policy.fail_closed is True

    def test_disable_pattern_blocked(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, reason = policy.check_action("skip the test")
        assert allowed is False
        assert "skip" in reason.lower()

    def test_disable_with_repair_blocked_when_fail_closed(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _reason = policy.check_action("skip the test but also fix the bug")
        assert allowed is False

    def test_disable_with_repair_allowed_when_fail_open(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("skip the broken test and fix the validator")
        assert allowed is True

    def test_disable_without_repair_blocked_when_fail_open(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("skip the test")
        assert allowed is False

    def test_repair_only_allowed(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, reason = policy.check_action("fix the bug in handler.py")
        assert allowed is True
        assert reason == "allowed"

    def test_repair_keywords_recognized(self) -> None:
        policy = FixNotDisablePolicy()
        for kw in ["fix", "repair", "implement", "refactor", "improve",
                    "correct", "restore", "enable", "add", "update"]:
            allowed, _ = policy.check_action(f"{kw} something")
            assert allowed is True, f"keyword '{kw}' should be allowed"

    def test_action_without_disable_or_repair_passes(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, _reason = policy.check_action("read the log file")
        assert allowed is True

    def test_context_is_descriptive_in_deny_reason(self) -> None:
        policy = FixNotDisablePolicy()
        _, reason = policy.check_action("delete the guardrail")
        assert "'delete the guardrail'" in reason

    def test_default_factory_returns_fail_closed(self) -> None:
        policy = default_fix_not_disable_policy()
        assert policy.fail_closed is True
        assert len(policy.allowed_repair_keywords) > 0
