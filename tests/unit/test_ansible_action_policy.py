"""TDD: action policy validation — allow/deny gating of playbooks, roles, collections, modules."""

from __future__ import annotations

from general_ludd.ansible.action_policy import (
    ActionManifest,
    ActionPolicyConfig,
    PolicyResult,
    validate_action,
)
from general_ludd.ansible.isolation import ProcessIsolationConfig


class TestValidateActionEnabledFlag:
    def test_disabled_policy_allows_everything(self) -> None:
        policy = ActionPolicyConfig(enabled=False)
        manifest = ActionManifest(playbook="risky.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert result.allowed is True
        assert result.denied_items == []
        assert result.reason == ""

    def test_disabled_policy_allows_blocked_playbook(self) -> None:
        policy = ActionPolicyConfig(
            enabled=False,
            disabled_playbooks=["risky.yml"],
        )
        manifest = ActionManifest(playbook="risky.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is True


class TestValidateActionDefaultMode:
    def test_default_mode_allow_passes_clean_manifest(self) -> None:
        policy = ActionPolicyConfig(default_mode="allow")
        manifest = ActionManifest(playbook="deploy.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_default_mode_deny_blocks_all(self) -> None:
        policy = ActionPolicyConfig(default_mode="deny")
        manifest = ActionManifest(playbook="deploy.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "deploy.yml" in result.denied_items
        assert "Default mode is deny" in result.reason

    def test_default_mode_deny_takes_precedence_over_disabled_lists(self) -> None:
        policy = ActionPolicyConfig(
            default_mode="deny",
            disabled_playbooks=["other.yml"],
        )
        manifest = ActionManifest(playbook="deploy.yml", modules=["copy"])
        result = validate_action(policy, manifest)
        assert result.allowed is False


class TestValidateActionDisabledPlaybooks:
    def test_blocked_playbook_is_denied(self) -> None:
        policy = ActionPolicyConfig(disabled_playbooks=["blocked.yml"])
        manifest = ActionManifest(playbook="blocked.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "blocked.yml" in result.denied_items
        assert "disabled" in result.reason

    def test_unlisted_playbook_passes(self) -> None:
        policy = ActionPolicyConfig(disabled_playbooks=["blocked.yml"])
        manifest = ActionManifest(playbook="allowed.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_multiple_disabled_playbooks(self) -> None:
        policy = ActionPolicyConfig(
            disabled_playbooks=["a.yml", "b.yml"],
        )
        manifest = ActionManifest(playbook="b.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is False


class TestValidateActionDisabledRoles:
    def test_blocked_role_is_denied(self) -> None:
        policy = ActionPolicyConfig(disabled_roles=["shell_exec"])
        manifest = ActionManifest(playbook="ok.yml", roles=["shell_exec"])
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "shell_exec" in result.denied_items
        assert "disabled" in result.reason

    def test_one_blocked_role_among_many(self) -> None:
        policy = ActionPolicyConfig(disabled_roles=["shell_exec"])
        manifest = ActionManifest(
            playbook="ok.yml",
            roles=["common", "shell_exec", "web"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "shell_exec" in result.denied_items
        assert "common" not in result.denied_items

    def test_no_roles_in_manifest_passes(self) -> None:
        policy = ActionPolicyConfig(disabled_roles=["shell_exec"])
        manifest = ActionManifest(playbook="ok.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is True


class TestValidateActionDisabledCollections:
    def test_blocked_collection_is_denied(self) -> None:
        policy = ActionPolicyConfig(
            disabled_collections=["community.general"],
        )
        manifest = ActionManifest(
            playbook="ok.yml",
            collections=["community.general"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "community.general" in result.denied_items

    def test_unlisted_collection_passes(self) -> None:
        policy = ActionPolicyConfig(
            disabled_collections=["community.general"],
        )
        manifest = ActionManifest(
            playbook="ok.yml",
            collections=["ansible.builtin"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True


class TestValidateActionDisabledModules:
    def test_blocked_module_is_denied(self) -> None:
        policy = ActionPolicyConfig(disabled_modules=["shell"])
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "shell" in result.denied_items
        assert "disabled" in result.reason

    def test_one_blocked_module_among_many(self) -> None:
        policy = ActionPolicyConfig(disabled_modules=["shell"])
        manifest = ActionManifest(
            playbook="ok.yml",
            modules=["copy", "shell", "template"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "shell" in result.denied_items
        assert "copy" not in result.denied_items
        assert "template" not in result.denied_items

    def test_all_modules_allowed_when_none_disabled(self) -> None:
        policy = ActionPolicyConfig()
        manifest = ActionManifest(
            playbook="ok.yml",
            modules=["copy", "template", "file"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True


class TestValidateActionProcessIsolation:
    def test_isolation_blocks_shell_module(self) -> None:
        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "shell" in result.denied_items
        assert "process isolation" in result.reason

    def test_isolation_allows_non_blocked_module(self) -> None:
        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(playbook="ok.yml", modules=["copy"])
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_isolation_disabled_does_not_block(self) -> None:
        iso = ProcessIsolationConfig(
            enabled=False,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_isolation_none_does_not_block(self) -> None:
        policy = ActionPolicyConfig(process_isolation=None)
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_isolation_blocks_file_write_module(self) -> None:
        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            block_local_tools=["file_write"],
        )
        policy = ActionPolicyConfig(process_isolation=iso)
        manifest = ActionManifest(playbook="ok.yml", modules=["copy"])
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "copy" in result.denied_items
        assert "process isolation" in result.reason


class TestValidateActionCombinedViolations:
    def test_multiple_categories_blocked_together(self) -> None:
        policy = ActionPolicyConfig(
            disabled_playbooks=["bad.yml"],
            disabled_roles=["dangerous"],
            disabled_modules=["shell"],
        )
        manifest = ActionManifest(
            playbook="bad.yml",
            roles=["dangerous"],
            modules=["shell"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert len(result.denied_items) == 3
        assert "bad.yml" in result.denied_items
        assert "dangerous" in result.denied_items
        assert "shell" in result.denied_items
        assert "disabled" in result.reason

    def test_isolation_plus_disabled_list(self) -> None:
        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            block_local_tools=["bash"],
        )
        policy = ActionPolicyConfig(
            disabled_modules=["shell"],
            process_isolation=iso,
        )
        manifest = ActionManifest(playbook="ok.yml", modules=["shell", "command"])
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert len(result.denied_items) == 2


class TestValidateActionEdgeCases:
    def test_empty_manifest_passes(self) -> None:
        policy = ActionPolicyConfig(
            disabled_playbooks=["x.yml"],
            disabled_roles=["r"],
            disabled_collections=["c"],
            disabled_modules=["m"],
        )
        manifest = ActionManifest(playbook="ok.yml")
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_empty_disabled_lists_pass(self) -> None:
        policy = ActionPolicyConfig()
        manifest = ActionManifest(
            playbook="ok.yml",
            roles=["r1", "r2"],
            collections=["c1"],
            modules=["m1"],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_policy_result_denied_items_is_list(self) -> None:
        policy = ActionPolicyConfig(disabled_modules=["shell"])
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert isinstance(result.denied_items, list)

    def test_policy_result_string_reason_on_deny(self) -> None:
        policy = ActionPolicyConfig(disabled_modules=["shell"])
        manifest = ActionManifest(playbook="ok.yml", modules=["shell"])
        result = validate_action(policy, manifest)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_policy_result_empty_reason_on_allow(self) -> None:
        policy = ActionPolicyConfig()
        manifest = ActionManifest(playbook="ok.yml")
        result = validate_action(policy, manifest)
        assert result.reason == ""
        assert result.denied_items == []


class TestActionPolicyConfigDefaults:
    def test_enabled_defaults_to_true(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.enabled is True

    def test_default_mode_defaults_to_allow(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.default_mode == "allow"

    def test_validate_before_run_defaults_to_true(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.validate_before_run is True

    def test_audit_after_run_defaults_to_true(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.audit_after_run is True

    def test_deny_unknown_playbooks_defaults_to_false(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.deny_unknown_playbooks is False

    def test_disabled_lists_default_to_empty(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.disabled_playbooks == []
        assert cfg.disabled_roles == []
        assert cfg.disabled_collections == []
        assert cfg.disabled_modules == []

    def test_process_isolation_defaults_to_none(self) -> None:
        cfg = ActionPolicyConfig()
        assert cfg.process_isolation is None


class TestActionManifest:
    def test_defaults_are_empty_lists(self) -> None:
        m = ActionManifest(playbook="p.yml")
        assert m.roles == []
        assert m.collections == []
        assert m.modules == []
        assert m.tags == []

    def test_fields_populated(self) -> None:
        m = ActionManifest(
            playbook="p.yml",
            roles=["r1"],
            collections=["c1"],
            modules=["m1"],
            tags=["t1"],
        )
        assert m.playbook == "p.yml"
        assert m.roles == ["r1"]
        assert m.collections == ["c1"]
        assert m.modules == ["m1"]
        assert m.tags == ["t1"]


class TestPolicyResult:
    def test_allow_default_values(self) -> None:
        r = PolicyResult(allowed=True)
        assert r.allowed is True
        assert r.denied_items == []
        assert r.reason == ""

    def test_deny_with_details(self) -> None:
        r = PolicyResult(
            allowed=False,
            denied_items=["shell"],
            reason="Module 'shell' is disabled",
        )
        assert r.allowed is False
        assert r.denied_items == ["shell"]
        assert "disabled" in r.reason
