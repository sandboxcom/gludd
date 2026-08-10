"""Deep edge-case tests for the fix-not-disable security policy."""

from __future__ import annotations

import pytest

from general_ludd.security.fix_not_disable import (
    DISABLE_PATTERNS,
    ActionIntent,
    FixNotDisablePolicy,
    default_fix_not_disable_policy,
    is_disabling_action,
)


class TestIsDisablingActionDeep:
    """Edge-case tests for is_disabling_action() — substring, whitespace, unicode."""

    def test_empty_string_false(self) -> None:
        assert not is_disabling_action("")

    def test_whitespace_only_false(self) -> None:
        assert not is_disabling_action("   ")
        assert not is_disabling_action("\n\t")
        assert not is_disabling_action("\r\n\t   ")

    def test_leading_trailing_whitespace(self) -> None:
        assert is_disabling_action("  skip  ")
        assert is_disabling_action("\tskip\t")

    def test_number_only_false(self) -> None:
        assert not is_disabling_action("12345")
        assert not is_disabling_action("0")

    @pytest.mark.parametrize(
        "word",
        [
            "skippable",
            "skipping",
            "skipped",
            "bypasses",
            "bypassing",
            "bypassed",
            "deletionist",
            "deletions",
            "deleting",
            "disabled",
            "disabler",
            "deactivated",
            "workarounds",
            "workaroundable",
            "bypasser",
        ],
    )
    def test_inflected_forms_match(self, word: str) -> None:
        assert is_disabling_action(word), f"'{word}' should match its base pattern"

    @pytest.mark.parametrize(
        "phrase",
        [
            "noskip",
            "nodisable",
            "prebypass",
            "aremove",
            "unbypass",
            "pseudodelete",
        ],
    )
    def test_substring_match_anywhere(self, phrase: str) -> None:
        assert is_disabling_action(phrase), f"'{phrase}' contains disable pattern"

    @pytest.mark.parametrize(
        "action",
        [
            "turn OFF the switch",
            "Turn Off the alarm",
            "TURN OFF everything",
        ],
    )
    def test_turn_off_case_variants(self, action: str) -> None:
        assert is_disabling_action(action)

    @pytest.mark.parametrize(
        "action",
        [
            "skIp tEsTs",
            "DeLeTe FiLeS",
            "bYpAsS gAtE",
            "ReMoVe CoDe",
            "dIsAbLe HoOk",
        ],
    )
    def test_mixed_case_matches(self, action: str) -> None:
        assert is_disabling_action(action)

    def test_unicode_non_ascii_preserved(self) -> None:
        assert is_disabling_action("skip — the test")
        assert is_disabling_action("« delete » this module")
        assert not is_disabling_action("repair 🛠️ the broken module")

    def test_emoji_only_false(self) -> None:
        assert not is_disabling_action("🛠️ 🔧 🧪")

    def test_multiple_disable_patterns(self) -> None:
        result = is_disabling_action("skip the test, then delete the file, then bypass the gate")
        assert result is True

    def test_patterns_at_string_boundaries(self) -> None:
        assert is_disabling_action("skip")
        assert is_disabling_action("  skip  ")
        assert is_disabling_action("\nskip\t")

    def test_very_long_string(self) -> None:
        prefix = "fix implement repair " * 2000
        long_str = f"{prefix} skip"
        assert is_disabling_action(long_str)

    def test_repeated_patterns(self) -> None:
        assert is_disabling_action("skip skip skip skip")
        assert is_disabling_action("delete delete delete")

    def test_all_patterns_are_lowercase(self) -> None:
        for pattern in DISABLE_PATTERNS:
            assert pattern == pattern.lower(), f"'{pattern}' is not lowercase"

    def test_disable_patterns_is_frozenset(self) -> None:
        assert isinstance(DISABLE_PATTERNS, frozenset)
        with pytest.raises((TypeError, AttributeError)):
            DISABLE_PATTERNS.add("new")  # type: ignore[attr-defined]

    def test_no_repair_matches_as_disable(self) -> None:
        """A string with repair keywords but also a disable pattern is still disabling."""
        assert is_disabling_action("fix the skip")
        assert is_disabling_action("repair and delete")


class TestPolicyCheckActionDeep:
    """Deep edge-case tests for FixNotDisablePolicy.check_action()."""

    def test_empty_description(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, reason = policy.check_action("")
        assert allowed is True
        assert reason == "allowed"

    def test_whitespace_only_description(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, _reason = policy.check_action("   \t\n  ")
        assert allowed is True

    def test_empty_context_still_blocks_disable(self) -> None:
        policy = FixNotDisablePolicy()
        allowed, reason = policy.check_action("skip test", context="")
        assert allowed is False
        assert "'skip test'" in reason

    def test_context_presence_does_not_affect_check(self) -> None:
        """Context is accepted but the check decision is based on action_description only."""
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("skip test", context="fix repair implement")
        assert allowed is False

    def test_context_in_reason(self) -> None:
        policy = FixNotDisablePolicy()
        _, reason = policy.check_action("delete the module", context="module_refactor")
        assert "'delete the module'" in reason

    def test_fail_closed_blocks_regardless_of_repair(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=True)
        for repair_kw in policy.allowed_repair_keywords:
            allowed, _ = policy.check_action(f"skip test and {repair_kw} it")
            assert allowed is False, f"fail_closed should block repair='{repair_kw}'"

    def test_fail_open_allows_if_any_repair_present(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        combinations = [
            "skip test and fix it",
            "delete and repair the module",
            "bypass then implement properly",
            "remove and refactor",
            "stub and improve later",
            "comment out but correct the logic",
            "deactivate and restore when ready",
            "turn off and enable after audit",
            "workaround and add real fix",
            "mock out and update the schema",
        ]
        for action in combinations:
            allowed, _ = policy.check_action(action)
            assert allowed is True, f"fail_open should allow: '{action}'"

    def test_fail_open_blocks_no_repair(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        for disable_word in ["skip", "delete", "bypass", "remove", "stub"]:
            allowed, _ = policy.check_action(f"{disable_word} the thing")
            assert allowed is False, f"fail_open should block: '{disable_word}'"

    def test_fail_open_disallow_only_non_repair_keywords(self) -> None:
        """A word like 'analyze' is not in allowed_repair_keywords — still blocks."""
        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("skip the test and analyze the result")
        assert allowed is False

    def test_repair_keyword_anywhere_in_string(self) -> None:
        """Repair keyword at start, middle, or end all work."""
        policy = FixNotDisablePolicy(fail_closed=False)
        assert policy.check_action("fix the skip")[0] is True
        assert policy.check_action("skip and fix")[0] is True
        assert policy.check_action("skip fix delete")[0] is True

    def test_custom_policy_with_extra_repair_keywords(self) -> None:
        policy = FixNotDisablePolicy(
            fail_closed=False,
            allowed_repair_keywords=["refine", "optimize", "enhance"],
        )
        assert policy.check_action("skip and refine")[0] is True
        assert policy.check_action("delete and enhance")[0] is True
        assert policy.check_action("skip and fix")[0] is False  # "fix" not in custom list

    def test_custom_policy_no_repair_keywords(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False, allowed_repair_keywords=[])
        assert policy.check_action("skip and fix")[0] is False

    def test_custom_policy_all_repair_keywords(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False, allowed_repair_keywords=["fix"])
        assert policy.check_action("skip and fix")[0] is True

    def test_mutating_keywords_list_after_construction(self) -> None:
        policy = FixNotDisablePolicy(fail_closed=False)
        original = list(policy.allowed_repair_keywords)
        policy.allowed_repair_keywords.append("newkeyword")
        assert policy.check_action("skip and newkeyword")[0] is True
        policy.allowed_repair_keywords = original

    def test_deny_reason_contains_description(self) -> None:
        policy = FixNotDisablePolicy()
        _, reason = policy.check_action("delete the entire database")
        assert "delete the entire database" in reason

    def test_deny_reason_mentions_policy(self) -> None:
        policy = FixNotDisablePolicy()
        _, reason = policy.check_action("bypass auth")
        assert "Policy" in reason or "repair" in reason.lower()

    def test_non_disable_non_repair_is_allowed(self) -> None:
        policy = FixNotDisablePolicy()
        neutral = [
            "read the config",
            "check the log",
            "verify the output",
            "measure the latency",
            "log the event",
            "audit the system",
        ]
        for phrase in neutral:
            allowed, _ = policy.check_action(phrase)
            assert allowed is True, f"neutral phrase should be allowed: '{phrase}'"

    def test_only_disable_pattern_long_string(self) -> None:
        policy = FixNotDisablePolicy()
        long_disable = "fix refactor improve " * 500 + " skip"
        allowed, _ = policy.check_action(long_disable)
        assert allowed is False

    @pytest.mark.parametrize(
        "description",
        [
            "I will skip but not delete",
            "skip (but also fix)",
            "SKIP",
            "[skip]",
            "{delete}",
            "disable|bypass",
        ],
    )
    def test_pattern_in_punctuation_context(self, description: str) -> None:
        """Disable patterns wrapped in punctuation or symbols still match."""
        assert is_disabling_action(description), f"'{description}' should be disabling"


class TestActionIntentDeep:
    def test_equality(self) -> None:
        a = ActionIntent(action_type="edit", target="foo.py", reason="fix")
        b = ActionIntent(action_type="edit", target="foo.py", reason="fix")
        assert a == b

    def test_inequality(self) -> None:
        a = ActionIntent(action_type="edit", target="foo.py", reason="fix")
        b = ActionIntent(action_type="delete", target="foo.py", reason="fix")
        assert a != b

    def test_default_factory_fields(self) -> None:
        intent = ActionIntent(action_type="write", target="/tmp/x.py", reason="")
        assert intent.action_type == "write"
        assert intent.reason == ""

    def test_action_intent_is_instance(self) -> None:
        intent = ActionIntent(action_type="edit", target="src/x.py", reason="fix bug")
        assert isinstance(intent, ActionIntent)
        assert intent.action_type
        assert intent.target
        assert isinstance(intent.reason, str)


class TestDefaultPolicyDeep:
    def test_default_is_fail_closed(self) -> None:
        policy = default_fix_not_disable_policy()
        assert policy.fail_closed is True

    def test_default_has_ten_keywords(self) -> None:
        policy = default_fix_not_disable_policy()
        assert len(policy.allowed_repair_keywords) == 10

    def test_default_factory_returns_new_instance(self) -> None:
        a = default_fix_not_disable_policy()
        b = default_fix_not_disable_policy()
        assert a is not b
        assert a.fail_closed == b.fail_closed
        assert a.allowed_repair_keywords == b.allowed_repair_keywords

    def test_default_policy_blocks_all_disable_patterns(self) -> None:
        policy = default_fix_not_disable_policy()
        for pattern in DISABLE_PATTERNS:
            allowed, _ = policy.check_action(f"{pattern} the thing")
            assert allowed is False, f"default policy should block '{pattern}'"

    def test_default_policy_allows_all_repair_keywords(self) -> None:
        policy = default_fix_not_disable_policy()
        for kw in policy.allowed_repair_keywords:
            allowed, _ = policy.check_action(f"{kw} the handler")
            assert allowed is True, f"default policy should allow repair keyword '{kw}'"
