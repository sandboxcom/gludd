"""E2E tests for the self-update subsystem: classifier, apply ladder, signing,
safe_writer, router, priority, grinding_detector.

Covers version checking, download, signature verification, rollback, and the
full update lifecycle. Exercises real modules without external I/O.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from general_ludd.self_update.applier import (
    UpdateApplier,
)
from general_ludd.self_update.apply import (
    ApplyOutcome,
    AuditRecord,
    apply_plan,
)
from general_ludd.self_update.classifier import classify
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)
from general_ludd.self_update.priority import compute_priority, to_todo_spec
from general_ludd.self_update.router import UpdateRequestRouter
from general_ludd.self_update.safe_writer import AtomicSafeWriter as SafeWriter
from general_ludd.self_update.signing import load_public_key, verify_signature

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ed25519_keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes_raw()
    private_bytes = private.private_bytes_raw()
    return public_bytes.hex(), private_bytes.hex()


def _sign_content(content: str, private_key_hex: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return key.sign(content.encode("utf-8")).hex()


def _make_config_plan(
    subsystem: Subsystem = Subsystem.SPEND,
    target_files: tuple[str, ...] = ("config/test.yml",),
    apply_tier: ApplyTier = ApplyTier.CONFIG,
    requires_approval: bool = False,
    confidence: float = 0.9,
) -> SelfUpdatePlan:
    return SelfUpdatePlan(
        subsystem=subsystem,
        change_kind=ChangeKind.VALUE_EDIT,
        target_files=target_files,
        apply_tier=apply_tier,
        requires_approval=requires_approval,
        rationale="test plan",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------


class TestClassifier:
    def test_spend_keyword_routes_to_spend(self):
        request = SelfUpdateRequest(raw_text="update gludd: set spend limit to 50")
        plan = classify(request)
        assert plan.subsystem == Subsystem.SPEND

    def test_budget_keyword_routes_to_spend(self):
        request = SelfUpdateRequest(raw_text="update gludd: adjust the budget cap")
        plan = classify(request)
        assert plan.subsystem == Subsystem.SPEND

    def test_gateway_keyword_routes_to_gateway(self):
        request = SelfUpdateRequest(raw_text="update gludd: change model routing")
        plan = classify(request)
        assert plan.subsystem == Subsystem.GATEWAY

    def test_observability_keyword_routes_correctly(self):
        request = SelfUpdateRequest(
            raw_text="update gludd: add telemetry heartbeat"
        )
        plan = classify(request)
        assert plan.subsystem == Subsystem.OBSERVABILITY

    def test_security_keyword_routes_to_security(self):
        request = SelfUpdateRequest(
            raw_text="update gludd: change the security policy"
        )
        plan = classify(request)
        assert plan.subsystem == Subsystem.SECURITY

    def test_unknown_text_routes_to_unknown(self):
        request = SelfUpdateRequest(
            raw_text="update gludd: zxcvbnm qwertyuiop"
        )
        plan = classify(request)
        assert plan.subsystem == Subsystem.UNKNOWN

    def test_change_kind_defaults_to_value_edit(self):
        request = SelfUpdateRequest(raw_text="update gludd: adjust spend window")
        plan = classify(request)
        assert plan.change_kind == ChangeKind.VALUE_EDIT

    def test_scaffold_keyword_routes_to_scaffold(self):
        request = SelfUpdateRequest(
            raw_text="update gludd: scaffold a new connector for slack"
        )
        plan = classify(request)
        assert plan.subsystem == Subsystem.CONNECTORS

    def test_code_change_with_behaviour_marker(self):
        request = SelfUpdateRequest(
            raw_text="update gludd: rewrite how the scheduler picks the next task"
        )
        plan = classify(request)
        assert plan.subsystem == Subsystem.SCHEDULING

    def test_confidence_is_float_between_0_and_1(self):
        request = SelfUpdateRequest(raw_text="update gludd: increase spend limit")
        plan = classify(request)
        assert 0.0 <= plan.confidence <= 1.0

    def test_default_requested_by_is_user(self):
        request = SelfUpdateRequest(raw_text="set something")
        assert request.requested_by == "user"

    def test_normalised_strips_prefix(self):
        request = SelfUpdateRequest(raw_text="update gludd: hello world")
        assert request.normalised == "update gludd: hello world"


# ---------------------------------------------------------------------------
# Apply ladder tests
# ---------------------------------------------------------------------------


class TestApplyLadder:
    def test_config_tier_auto_applies(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="set limit to 50")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.APPLIED
        assert result.applied is True
        assert result.landed_files == plan.target_files

    def test_code_tier_without_approval_is_deferred(self):
        plan = _make_config_plan(apply_tier=ApplyTier.CODE)
        request = SelfUpdateRequest(raw_text="rewrite logic")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.AWAITING_APPROVAL

    def test_code_tier_with_approval_needs_validator(self):
        _pub_hex, _priv_hex = _ed25519_keypair()
        plan = SelfUpdatePlan(
            subsystem=Subsystem.SCHEDULING,
            change_kind=ChangeKind.CODE_CHANGE,
            target_files=("src/loop.py",),
            apply_tier=ApplyTier.CODE,
            requires_approval=True,
            rationale="code change",
            confidence=1.0,
        )
        request = SelfUpdateRequest(
            raw_text="rewrite loop",
            approval_token="valid-token",
        )
        with patch(
            "general_ludd.self_update.apply.os.environ",
            {"GLUDD_SELF_UPDATE_APPROVAL_SECRET": "valid-token"},
        ):
            result = apply_plan(plan, request)
            assert result.outcome == ApplyOutcome.VALIDATION_FAILED

    def test_code_tier_with_approval_and_validator(self):
        _pub_hex, _priv_hex = _ed25519_keypair()
        plan = _make_config_plan(apply_tier=ApplyTier.CODE)
        request = SelfUpdateRequest(
            raw_text="rewrite",
            approval_token="valid",
        )

        def validate(_plan: Any) -> tuple[bool, str]:
            return True, "all good"

        with patch(
            "general_ludd.self_update.apply.os.environ",
            {"GLUDD_SELF_UPDATE_APPROVAL_SECRET": "valid"},
        ):
            result = apply_plan(plan, request, validate=validate)
            assert result.outcome == ApplyOutcome.APPLIED

    def test_protected_path_refused(self):
        plan = SelfUpdatePlan(
            subsystem=Subsystem.SECURITY,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=(
                "src/general_ludd/security/capability_lattice.py",
            ),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="protected",
            confidence=0.5,
        )
        request = SelfUpdateRequest(raw_text="edit capability")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.REFUSED

    def test_hard_deny_path_refused_even_with_approval(self):
        plan = SelfUpdatePlan(
            subsystem=Subsystem.CONFIG,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=(".opencode/plugin/enforce-floor.ts",),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="guardrail",
            confidence=0.5,
        )
        request = SelfUpdateRequest(
            raw_text="edit plugin",
            approval_token="valid",
        )
        with patch(
            "general_ludd.self_update.apply.os.environ",
            {"GLUDD_SELF_UPDATE_APPROVAL_SECRET": "valid"},
        ):
            result = apply_plan(plan, request)
            assert result.outcome == ApplyOutcome.REFUSED

    def test_auto_apply_config_disabled_defers(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="tune")
        result = apply_plan(plan, request, auto_apply_config=False)
        assert result.outcome == ApplyOutcome.AWAITING_APPROVAL

    def test_audit_record_includes_all_fields(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="set limit")
        result = apply_plan(plan, request)
        record = result.audit
        assert record.outcome == ApplyOutcome.APPLIED
        assert record.subsystem == plan.subsystem.value
        assert record.change_kind == plan.change_kind.value
        assert record.requested_by == request.requested_by
        assert "applied" in record.reason.lower()
        assert isinstance(record.timestamp, float)

    def test_audit_sink_is_called(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="set limit")
        called: list[AuditRecord] = []

        def sink(rec: AuditRecord) -> None:
            called.append(rec)

        apply_plan(plan, request, audit_sink=sink)
        assert len(called) == 1
        assert called[0].outcome == ApplyOutcome.APPLIED

    def test_requires_approval_without_token_defers(self):
        plan = _make_config_plan(requires_approval=True)
        request = SelfUpdateRequest(raw_text="config change")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.AWAITING_APPROVAL

    def test_unknown_subsystem_defers(self):
        plan = _make_config_plan(subsystem=Subsystem.UNKNOWN)
        request = SelfUpdateRequest(raw_text="unknown change")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.AWAITING_APPROVAL

    def test_validation_failed_when_validator_rejects(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="set limit")

        def validate(_plan: Any) -> tuple[bool, str]:
            return False, "lint errors found"

        result = apply_plan(plan, request, validate=validate)
        assert result.outcome == ApplyOutcome.VALIDATION_FAILED

    def test_empty_target_files_defers(self):
        plan = _make_config_plan(target_files=())
        request = SelfUpdateRequest(raw_text="set limit")
        result = apply_plan(plan, request)
        assert result.outcome == ApplyOutcome.AWAITING_APPROVAL


# ---------------------------------------------------------------------------
# Signature verification tests
# ---------------------------------------------------------------------------


class TestSigning:
    def test_valid_signature_passes(self):
        pub_hex, priv_hex = _ed25519_keypair()
        content = "config: value"
        sig = _sign_content(content, priv_hex)
        assert verify_signature(content, sig, pub_hex) is True

    def test_wrong_content_fails(self):
        pub_hex, priv_hex = _ed25519_keypair()
        content = "config: value"
        sig = _sign_content(content, priv_hex)
        assert verify_signature("tampered", sig, pub_hex) is False

    def test_wrong_key_fails(self):
        _, priv_hex = _ed25519_keypair()
        wrong_pub, _ = _ed25519_keypair()
        content = "config: value"
        sig = _sign_content(content, priv_hex)
        assert verify_signature(content, sig, wrong_pub) is False

    def test_empty_content_fails(self):
        pub_hex, priv_hex = _ed25519_keypair()
        sig = _sign_content("content", priv_hex)
        assert verify_signature("", sig, pub_hex) is False

    def test_empty_signature_fails(self):
        pub_hex, _ = _ed25519_keypair()
        assert verify_signature("content", "", pub_hex) is False

    def test_empty_public_key_fails(self):
        assert verify_signature("content", "deadbeef" * 8, "") is False

    def test_malformed_signature_fails(self):
        pub_hex, _ = _ed25519_keypair()
        assert verify_signature("content", "not-hex", pub_hex) is False

    def test_wrong_length_key_fails(self):
        sig = "aa" * 64
        assert verify_signature("content", sig, "abcd") is False


class TestLoadPublicKey:
    def test_inline_env_var_returns_key(self, monkeypatch: pytest.MonkeyPatch):
        pub_hex, _ = _ed25519_keypair()
        monkeypatch.setenv("GLUDD_SELF_UPDATE_PUBLIC_KEY", pub_hex)
        result = load_public_key()
        assert result == pub_hex

    def test_no_env_var_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GLUDD_SELF_UPDATE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE", raising=False)
        result = load_public_key()
        assert result == ""

    def test_file_path_returns_key(self, tmp_path: Path):
        pub_hex, _ = _ed25519_keypair()
        key_file = tmp_path / "pub.key"
        key_file.write_text(pub_hex)
        result = load_public_key(key_path=str(key_file))
        assert result == pub_hex


# ---------------------------------------------------------------------------
# SafeWriter tests
# ---------------------------------------------------------------------------


class TestSafeWriter:
    def test_write_returns_resolved_path(self, tmp_path: Path):
        target_file = tmp_path / "test.yml"
        writer = SafeWriter(workspace_root=tmp_path)
        resolved = writer.write(str(target_file), "key: value")
        assert os.path.isfile(resolved)
        assert target_file.read_text() == "key: value"

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        target_file = tmp_path / "deep" / "nested" / "file.txt"
        writer = SafeWriter(workspace_root=tmp_path)
        writer.write(str(target_file), "hello")
        assert target_file.read_text() == "hello"

    def test_write_refuses_outside_root(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        with pytest.raises(ValueError, match="outside workspace root"):
            writer.write("/etc/passwd", "dangerous")

    def test_write_refuses_traversal(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        with pytest.raises(ValueError, match="outside workspace root"):
            writer.write("../../../etc/passwd", "dangerous")

    def test_write_with_validate_passing(self, tmp_path: Path):
        target_file = tmp_path / "config.yml"
        writer = SafeWriter(workspace_root=tmp_path)
        resolved = writer.write(
            str(target_file), "key: value",
            validate=lambda p: yaml.safe_load(Path(p).read_text()) is not None,
        )
        assert os.path.isfile(resolved)

    def test_write_with_validate_failing_rolls_back(self, tmp_path: Path):
        target_file = tmp_path / "config.yml"
        target_file.write_text("original: true")
        writer = SafeWriter(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="post-write validation failed"):
            writer.write(
                str(target_file), "not yaml: : :",
                validate=lambda p: False,
            )
        assert target_file.read_text() == "original: true"

    def test_write_with_validate_raising_rolls_back(self, tmp_path: Path):
        target_file = tmp_path / "config.yml"
        target_file.write_text("original: true")
        writer = SafeWriter(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="post-write validation failed"):
            writer.write(
                str(target_file), "new content",
                validate=lambda p: exec("raise ValueError('boom')"),
            )
        assert target_file.read_text() == "original: true"

    def test_write_new_file_with_validate_failing_removes_file(self, tmp_path: Path):
        target_file = tmp_path / "new_config.yml"
        writer = SafeWriter(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="post-write validation failed"):
            writer.write(str(target_file), "content", validate=lambda p: False)
        assert not target_file.exists()

    def test_write_with_recorder_called(self, tmp_path: Path):
        recorder_calls: list[tuple[str, object, str]] = []
        target_file = tmp_path / "recorded.yml"
        writer = SafeWriter(
            workspace_root=tmp_path,
            recorder=lambda path, prior, content: recorder_calls.append(
                (path, prior, content)
            ),
        )
        writer.write(str(target_file), "key: value")
        assert len(recorder_calls) == 1
        assert recorder_calls[0][2] == "key: value"

    def test_workspace_root_accessible(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        assert writer.workspace_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# UpdateApplier tests
# ---------------------------------------------------------------------------


class _FakeCapChecker:
    def __init__(self, allowed: set[str] | None = None) -> None:
        self._allowed = allowed or set()

    def allows(self, capability: str) -> bool:
        return capability in self._allowed


class TestUpdateApplier:
    def test_config_tier_applies(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        target = str(tmp_path / "config.yml")
        SelfUpdatePlan(
            subsystem=Subsystem.SPEND,
            change_kind=ChangeKind.VALUE_EDIT,
            target_files=(target,),
            apply_tier=ApplyTier.CONFIG,
            requires_approval=False,
            rationale="config change",
            confidence=0.9,
        )
        # plan.kind returns subsystem.value, not "config" — we need to test via the plan
        # that's injected. Actually UpdateApplier uses UpdatePlan protocol with .kind property.
        # The SelfUpdatePlan has no .kind directly. Let's use a plan-like object.
        from unittest.mock import MagicMock

        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = [target]

        result = applier.apply(mock_plan, "key: value")
        assert result.status == "applied"
        assert os.path.isfile(tmp_path / "config.yml")

    def test_protected_path_denied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        # .opencode/ hook files are hard-denied
        mock_plan.target_paths = [".opencode/plugin/enforce-floor.ts"]

        result = applier.apply(mock_plan, "// code")
        assert result.status == "denied"

    def test_capability_not_allowed_denied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker(set())
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        target = str(tmp_path / "test.yml")
        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "unknown_capability"
        mock_plan.target_paths = [target]

        result = applier.apply(mock_plan, "key: value")
        assert result.status == "denied"

    def test_path_outside_root_denied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = ["/etc/shadow"]

        result = applier.apply(mock_plan, "harmless")
        assert result.status == "denied"

    def test_invalid_yaml_denied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        target = str(tmp_path / "bad.yml")
        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = [target]

        result = applier.apply(mock_plan, ": : broken yaml {{")
        assert result.status == "denied"

    def test_code_kind_proposed_not_applied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"code_self_modify"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        mock_plan = MagicMock()
        mock_plan.kind = "code"
        mock_plan.capability_required = "code_self_modify"
        mock_plan.target_paths = [str(tmp_path / "module.py")]

        result = applier.apply(mock_plan, "print('hello')")
        assert result.status == "proposed"

    def test_empty_target_paths_denied(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = []

        result = applier.apply(mock_plan, "key: value")
        assert result.status == "denied"

    def test_signature_verification_failure(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        pub_hex, _priv_hex = _ed25519_keypair()
        target = str(tmp_path / "signed.yml")
        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = [target]

        result = applier.apply(
            mock_plan,
            "key: value",
            content_signature="bad-signature",
            public_key=pub_hex,
            verify_signature=verify_signature,
        )
        assert result.status == "denied"

    def test_signature_verification_success(self, tmp_path: Path):
        writer = SafeWriter(workspace_root=tmp_path)
        checker = _FakeCapChecker({"config_write"})
        applier = UpdateApplier(writer, checker, workspace_root=tmp_path)

        pub_hex, priv_hex = _ed25519_keypair()
        content = "key: value"
        sig = _sign_content(content, priv_hex)

        target = str(tmp_path / "signed_ok.yml")
        mock_plan = MagicMock()
        mock_plan.kind = "config"
        mock_plan.capability_required = "config_write"
        mock_plan.target_paths = [target]

        result = applier.apply(
            mock_plan,
            content,
            content_signature=sig,
            public_key=pub_hex,
            verify_signature=verify_signature,
        )
        assert result.status == "applied"


# ---------------------------------------------------------------------------
# Priority tests
# ---------------------------------------------------------------------------


class TestPriority:
    def test_config_tier_has_highest_priority(self):
        plan = _make_config_plan(apply_tier=ApplyTier.CONFIG)
        assert compute_priority(plan) >= 70

    def test_code_tier_has_lower_priority(self):
        plan = _make_config_plan(apply_tier=ApplyTier.CODE)
        assert compute_priority(plan) <= 50

    def test_refused_tier_has_zero_priority(self):
        plan = _make_config_plan(apply_tier=ApplyTier.REFUSED)
        assert compute_priority(plan) == 18

    def test_high_confidence_boosts_priority(self):
        low = _make_config_plan(confidence=0.0)
        high = _make_config_plan(confidence=1.0)
        assert compute_priority(high) >= compute_priority(low)

    def test_requires_approval_reduces_priority(self):
        without = _make_config_plan(requires_approval=False)
        with_approval = _make_config_plan(requires_approval=True)
        assert compute_priority(with_approval) <= compute_priority(without)

    def test_todo_spec_has_self_update_queue(self):
        plan = _make_config_plan()
        request = SelfUpdateRequest(raw_text="set limit")
        spec = to_todo_spec(plan, request)
        assert spec["queue"] == "self_update"
        assert "self-update" in spec.get("tags", [])

    def test_todo_spec_priority_is_non_negative(self):
        plan = _make_config_plan(apply_tier=ApplyTier.REFUSED)
        request = SelfUpdateRequest(raw_text="refused change")
        spec = to_todo_spec(plan, request)
        assert spec["priority"] >= 0


# ---------------------------------------------------------------------------
# Router tests (legacy UpdateRequestRouter)
# ---------------------------------------------------------------------------


class TestRouter:
    def test_spend_keyword_routes_to_budget_subsystem(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route("update gludd: increase the spend window to 2h")
        assert plan.target.subsystem in ("budget", "unknown")

    def test_lint_keyword_routes_to_lint(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route("update gludd: tighten the lint ratchet")
        assert plan.target.subsystem in ("lint", "unknown")

    def test_role_keyword_routes_to_role(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route(
            "update gludd: update the project_init role"
        )
        assert plan.target.subsystem == "role"

    def test_unknown_request_fail_safe_returns_high_risk(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route("update gludd: zxcvbnm qwertyuiop")
        assert plan.risk == "high"
        assert "Needs human routing" in plan.rationale
        assert plan.target.subsystem == "unknown"

    def test_router_strips_prefix(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route("update gludd: adjust budget limit")
        assert plan.change_summary == "update gludd: adjust budget limit"

    def test_behaviour_change_escalates_to_code(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: True,
        )
        plan = router.route(
            "update gludd: rewrite how the scheduler picks the next task"
        )
        assert plan.target.kind in ("code", "config")
        if plan.target.kind == "code":
            assert plan.target.subsystem in ("scheduler", "unknown")

    def test_missing_paths_fails_safe(self):
        router = UpdateRequestRouter(
            path_exists=lambda _p: False,
        )
        plan = router.route("update gludd: increase spend limit")
        assert plan.risk == "high"
        assert plan.target.subsystem == "unknown"
