"""Unit tests for Ansible action policy and manifest generator."""

from __future__ import annotations

import os
import tempfile

import yaml

from agentic_harness.ansible.action_policy import (
    ActionManifest,
    ActionPolicyConfig,
    PolicyResult,
    validate_action,
)
from agentic_harness.ansible.manifest import generate_manifest


class TestActionPolicyConfig:
    def test_action_policy_allows_empty_disabled_lists(self):
        policy = ActionPolicyConfig()
        manifest = ActionManifest(
            playbook="test.yml",
            roles=["role_a"],
            collections=["community.general"],
            modules=["ansible.builtin.copy"],
            tags=["install"],
        )
        result = validate_action(policy, manifest)
        assert isinstance(result, PolicyResult)
        assert result.allowed is True
        assert result.denied_items == []
        assert result.reason == ""

    def test_action_policy_denies_disabled_collection(self):
        policy = ActionPolicyConfig(disabled_collections=["community.general"])
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=["community.general"],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "community.general" in result.denied_items
        assert "collection" in result.reason.lower() or "disabled" in result.reason.lower()

    def test_action_policy_denies_disabled_role(self):
        policy = ActionPolicyConfig(disabled_roles=["dangerous_role"])
        manifest = ActionManifest(
            playbook="test.yml",
            roles=["dangerous_role"],
            collections=[],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "dangerous_role" in result.denied_items

    def test_action_policy_denies_disabled_module(self):
        policy = ActionPolicyConfig(disabled_modules=["ansible.builtin.shell"])
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=[],
            modules=["ansible.builtin.shell"],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "ansible.builtin.shell" in result.denied_items

    def test_action_policy_denies_disabled_playbook(self):
        policy = ActionPolicyConfig(disabled_playbooks=["evil.yml"])
        manifest = ActionManifest(
            playbook="evil.yml",
            roles=[],
            collections=[],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False
        assert "evil.yml" in result.denied_items

    def test_action_policy_does_not_parse_shell_substrings(self):
        policy = ActionPolicyConfig(
            disabled_modules=["ansible.builtin.shell"],
            default_mode="allow",
        )
        manifest = ActionManifest(
            playbook="safe.yml",
            roles=[],
            collections=[],
            modules=["ansible.builtin.debug"],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is True

    def test_action_policy_default_mode_deny(self):
        policy = ActionPolicyConfig(default_mode="deny")
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=[],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed is False


class TestActionManifest:
    def test_action_manifest_includes_role_refs(self):
        manifest = ActionManifest(
            playbook="site.yml",
            roles=["web", "db"],
            collections=[],
            modules=[],
            tags=[],
        )
        assert "web" in manifest.roles
        assert "db" in manifest.roles

    def test_action_manifest_includes_module_refs(self):
        manifest = ActionManifest(
            playbook="site.yml",
            roles=[],
            collections=[],
            modules=["ansible.builtin.copy", "community.general.archive"],
            tags=[],
        )
        assert "ansible.builtin.copy" in manifest.modules
        assert "community.general.archive" in manifest.modules


class TestGenerateManifest:
    def test_generate_manifest_parses_playbook(self):
        playbook = [
            {
                "name": "Test play",
                "hosts": "localhost",
                "roles": ["webserver"],
                "tasks": [
                    {"name": "Copy config", "ansible.builtin.copy": {"src": "a", "dest": "b"}},
                    {"name": "Run archive", "community.general.archive": {"path": "/tmp/x"}},
                ],
                "tags": ["deploy"],
            }
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            manifest = generate_manifest(path)
            assert "webserver" in manifest.roles
            assert "ansible.builtin.copy" in manifest.modules
            assert "community.general.archive" in manifest.modules
            assert "deploy" in manifest.tags
        finally:
            os.unlink(path)

    def test_generate_manifest_extracts_fqcn_modules(self):
        playbook = [
            {
                "name": "FQCN test",
                "hosts": "localhost",
                "tasks": [
                    {"community.mysql.mysql_db": {"name": "testdb"}},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(playbook, f)
            path = f.name
        try:
            manifest = generate_manifest(path)
            assert "community.mysql.mysql_db" in manifest.modules
        finally:
            os.unlink(path)

    def test_generate_manifest_handles_empty_playbook(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump([], f)
            path = f.name
        try:
            manifest = generate_manifest(path)
            assert manifest.roles == []
            assert manifest.modules == []
            assert manifest.tags == []
        finally:
            os.unlink(path)
