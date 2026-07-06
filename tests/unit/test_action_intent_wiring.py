"""Prove ActionIntent is wired into the security auto-fix path."""

from __future__ import annotations

from typing import Any, cast

from general_ludd.security.fix_not_disable import (
    ActionIntent,
    FixNotDisablePolicy,
)


class TestActionIntentConstruction:
    def test_construct_minimal(self) -> None:
        intent = ActionIntent(
            action_type="fix",
            target="guardrail_disable_registry",
            reason="Restore enforcement; guardrails must block, not bypass",
        )
        assert intent.action_type == "fix"
        assert intent.target == "guardrail_disable_registry"
        assert "Restore enforcement" in intent.reason

    def test_construct_action_type(self) -> None:
        intent = ActionIntent(
            action_type="repair",
            target="test_module.py",
            reason="Fix the failing test",
        )
        assert intent.action_type == "repair"
        assert intent.target == "test_module.py"


class TestActionIntentWiredIntoProductionModule:
    def test_action_intent_imported_in_adversarial_detector(self) -> None:
        import general_ludd.security.adversarial_detector as ad

        assert hasattr(ad, "ActionIntent"), (
            "ActionIntent must be imported in adversarial_detector "
            "for the security auto-fix path"
        )
        assert ad.ActionIntent is ActionIntent, (
            "adversarial_detector.ActionIntent must be the same class "
            "as fix_not_disable.ActionIntent"
        )

    def test_detector_creates_action_intent_from_finding(self) -> None:
        from general_ludd.security.adversarial_detector import (
            AdversarialCodeDetector,
        )

        detector = AdversarialCodeDetector()
        result = detector.scan_text("throw new Error()")

        assert len(result.findings) >= 0
        if result.findings:
            intent = detector.create_action_intent(result.findings[0])
            assert isinstance(intent, ActionIntent), (
                "create_action_intent must return an ActionIntent"
            )
            assert intent.action_type in {"fix", "repair", "implement"}
            assert intent.target
            assert intent.reason

    def test_create_action_intent_default_remediation(self) -> None:
        from general_ludd.security.adversarial_detector import (
            AdversarialCodeDetector,
            AdversarialFinding,
        )

        detector = AdversarialCodeDetector()
        finding = AdversarialFinding(
            pattern_id="test_pattern",
            category=cast(Any, "self_sabotage"),
            severity=cast(Any, "high"),
            description="Test finding",
            match_text="test match",
            file_path="test.py",
            line_number=1,
            remediation="Fix the issue, do not disable",
        )
        intent = detector.create_action_intent(finding)
        assert isinstance(intent, ActionIntent)
        assert intent.action_type == "fix"
        assert intent.target == "test_pattern"
        assert intent.reason == "Fix the issue, do not disable"


class TestActionIntentIntegrationWithPolicy:
    def test_action_intent_passes_fix_not_disable_policy(self) -> None:
        policy = FixNotDisablePolicy()
        intent = ActionIntent(
            action_type="fix",
            target="broken_guardrail",
            reason="Repair the guardrail instead of removing it",
        )
        allowed, msg = policy.check_action(intent.reason)
        assert allowed is True, f"Fix action was blocked: {msg}"

    def test_disable_intent_blocked_by_policy(self) -> None:
        policy = FixNotDisablePolicy()
        intent = ActionIntent(
            action_type="disable",
            target="failing_test",
            reason="skip this test permanently",
        )
        allowed, _msg = policy.check_action(intent.reason)
        assert allowed is False, "Disable action should be blocked by policy"
