from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
import yaml

from agentic_harness.secrets.config import OpenBaoConfig
from agentic_harness.secrets.env import EnvSecretsManager
from agentic_harness.secrets.manager import SecretsManager


class TestOpenBaoE2E:
    def test_external_config_wins_over_local_bootstrap(self):
        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.ext-test-token",
        )
        assert config.mode == "external"
        assert config.external_url == "https://bao.example.com:8200"
        mgr = SecretsManager(config=config)
        assert mgr.is_external_configured()

    def test_local_bootstrap_when_no_external(self):
        config = OpenBaoConfig(mode="auto")
        assert not config.external_url
        mgr = SecretsManager(config=config)
        assert not mgr.is_external_configured()
        result = mgr.bootstrap_local()
        assert result.initialized
        assert result.url == "http://localhost:8200"

    def test_image_digest_pinning(self):
        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.pin_image_digest("ghcr.io/openbao/openbao", "sha256:abc123def456")
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()
        call_kwargs = mock_client.secrets.kv.v2.create_or_update_secret.call_args
        assert call_kwargs.kwargs["secret"]["pinned_digest"] == "sha256:abc123def456"

    def test_weekly_image_scan_detects_update(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": {
                    "image_ref": "ghcr.io/openbao/openbao",
                    "pinned_digest": "sha256:old_digest",
                }
            }
        }
        config = OpenBaoConfig(mode="auto", local_image="ghcr.io/openbao/openbao")
        mgr = SecretsManager(client=mock_client, config=config)
        candidate = mgr.scan_for_image_updates()
        assert candidate is not None
        assert candidate.current_digest == "sha256:old_digest"
        assert candidate.candidate_digest != "sha256:old_digest"

    def test_secrets_not_logged(self, capfd):
        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.write_secret("test/secret_key", {"value": "super-secret-abc123"})
        captured = capfd.readouterr()
        assert "super-secret-abc123" not in captured.out
        assert "super-secret-abc123" not in captured.err

    def test_env_secrets_manager_resolves_for_job_scope(self):
        mgr = EnvSecretsManager(overrides={"ZAI_API_KEY": "sk-test-123"})
        assert mgr.resolve("ZAI_API_KEY") == "sk-test-123"
        assert mgr.resolve("NONEXISTENT") is None

    def test_connect_with_mocked_client(self):
        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(config=config)
        mgr.bootstrap_local()
        mgr._client = mock_client
        mgr.write_secret("test/key", {"val": "data"})
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()

    def test_openbao_playbooks_exist(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        for pb in ["openbao_bootstrap.yml", "openbao_image_update_scan.yml"]:
            path = os.path.join(repo_root, "playbooks", pb)
            assert os.path.exists(path), f"Missing playbook: {pb}"
            with open(path) as f:
                data = yaml.safe_load(f)
            assert data is not None

    def test_connect_without_bootstrap_raises(self):
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(config=config)
        with pytest.raises(RuntimeError):
            mgr.connect()
