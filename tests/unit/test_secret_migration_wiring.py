"""Tests for secret migration wired into daemon startup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


class TestSecretMigrationInDaemonStartup:
    def test_migrate_called_when_openbao_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_MIGRATE_KEY", "sk-migrated-12345")

        profiles_dir = tmp_path / "model_profiles"
        profiles_dir.mkdir()
        (profiles_dir / "test.yml").write_text(
            "model_profile_id: test_prof\n"
            "credential_alias: TEST_MIGRATE_KEY\n"
            "provider: openai\n"
            "model_name: gpt-4\n"
        )

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_profiles").symlink_to(profiles_dir)

        with patch("general_ludd.daemon.build_secrets_resolver") as mock_resolver:
            mock_mgr = MagicMock()
            mock_mgr.write_secret.return_value = None
            mock_mgr.register_alias.return_value = None
            mock_resolver.return_value = mock_mgr

            with patch(
                "general_ludd.daemon.migrate_profile_secrets"
            ) as mock_migrate:
                mock_migrate.return_value = {
                    "migrated": 1,
                    "aliases": ["TEST_MIGRATE_KEY"],
                    "skipped": [],
                }

                with patch(
                    "general_ludd.daemon.AnsibleRunnerAdapter",
                    return_value=MagicMock(),
                ):
                    app = create_daemon_app(
                        tick_interval=0.01,
                        config_dir=str(config_dir),
                    )
                    with TestClient(app):
                        pass

        profiles_arg = mock_migrate.call_args[0][1]
        assert any(
            p.get("credential_alias") == "TEST_MIGRATE_KEY"
            for p in profiles_arg
        )

    def test_migrate_not_called_when_env_resolver(self, tmp_path):
        with patch("general_ludd.daemon.build_secrets_resolver") as mock_resolver:
            from general_ludd.secrets.env import EnvSecretsManager

            mock_resolver.return_value = EnvSecretsManager()

            with patch(
                "general_ludd.daemon.migrate_profile_secrets"
            ) as mock_migrate:
                with patch(
                    "general_ludd.daemon.AnsibleRunnerAdapter",
                    return_value=MagicMock(),
                ):
                    app = create_daemon_app(tick_interval=0.01)
                    with TestClient(app):
                        pass

                mock_migrate.assert_not_called()
