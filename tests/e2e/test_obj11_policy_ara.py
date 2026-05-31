from __future__ import annotations

import os
import tempfile

import pytest
import yaml
from pydantic import ValidationError

from agentic_harness.ansible.action_policy import (
    ActionManifest,
    ActionPolicyConfig,
    validate_action,
)
from agentic_harness.ansible.ara import ARAConfig
from agentic_harness.ansible.manifest import generate_manifest


class TestActionPolicyE2E:
    def test_allow_with_empty_deny_lists(self):
        policy = ActionPolicyConfig(enabled=True)
        manifest = ActionManifest(
            playbook="noop.yml",
            roles=[],
            collections=[],
            modules=["ansible.builtin.debug"],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed

    def test_deny_disabled_collection(self):
        policy = ActionPolicyConfig(
            enabled=True,
            disabled_collections=["community.aws"],
        )
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=["community.aws"],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert not result.allowed
        assert "community.aws" in result.reason

    def test_deny_disabled_module(self):
        policy = ActionPolicyConfig(
            enabled=True,
            disabled_modules=["ansible.builtin.raw"],
        )
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=[],
            modules=["ansible.builtin.raw", "ansible.builtin.copy"],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert not result.allowed

    def test_deny_disabled_role(self):
        policy = ActionPolicyConfig(
            enabled=True,
            disabled_roles=["external.untrusted"],
        )
        manifest = ActionManifest(
            playbook="test.yml",
            roles=["external.untrusted"],
            collections=[],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert not result.allowed

    def test_deny_disabled_playbook(self):
        policy = ActionPolicyConfig(
            enabled=True,
            disabled_playbooks=["playbooks/destructive.yml"],
        )
        manifest = ActionManifest(
            playbook="playbooks/destructive.yml",
            roles=[],
            collections=[],
            modules=[],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert not result.allowed

    def test_manifest_generation_from_playbook(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump([
                {
                    "name": "Test playbook",
                    "hosts": "localhost",
                    "tasks": [
                        {"ansible.builtin.debug": {"msg": "hello"}},
                        {"ansible.builtin.copy": {"dest": "/tmp/x", "content": "y"}},
                    ],
                }
            ], f)
            f.flush()
            manifest = generate_manifest(f.name)
        assert manifest is not None
        assert manifest.playbook
        assert "ansible.builtin.debug" in manifest.modules or len(manifest.modules) >= 1
        os.unlink(f.name)

    def test_policy_disabled_allows_everything(self):
        policy = ActionPolicyConfig(enabled=False)
        manifest = ActionManifest(
            playbook="test.yml",
            roles=[],
            collections=["community.aws"],
            modules=["ansible.builtin.raw"],
            tags=[],
        )
        result = validate_action(policy, manifest)
        assert result.allowed

    def test_ara_config_postgresql_backend(self):
        config = ARAConfig(backend="postgresql", connection_string="postgresql://localhost/ara")
        assert config.backend == "postgresql"
        assert config.connection_string == "postgresql://localhost/ara"

    def test_ara_config_rejects_invalid_backend(self):
        with pytest.raises(ValidationError):
            ARAConfig(backend="invalid_backend")

    def test_action_policy_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        for pb in ["action_policy_validate.yml", "ara_setup.yml"]:
            assert os.path.exists(os.path.join(repo_root, "playbooks", pb))
