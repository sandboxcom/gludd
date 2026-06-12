"""W2.9 (H17): secrets `mode: auto` tries OpenBao with a bounded health check,
falls back to env on failure, and a migrated secret reads back after the env var
is deleted.

The shipped default is mode=auto. Before this, auto mode fell straight to env
(no OpenBao attempt) or connected without verifying reachability. These tests
pin: (a) auto with no URL -> env; (b) auto with an unreachable URL -> env
fallback (health check fails, daemon does not hang); (c) read-back: migrate a
secret into the (mocked) vault, delete the env var, resolution still returns it.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from general_ludd.daemon import build_secrets_resolver
from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretsManager
from general_ludd.secrets.migration import migrate_profile_secrets


class TestAutoModeFallback:
    def test_auto_no_url_uses_env(self):
        cfg = OpenBaoConfig(mode="auto")  # no external_url
        resolver = build_secrets_resolver(openbao_config=cfg, env_overrides={"K": "v"})
        # Env-backed resolver resolves the override.
        assert resolver.resolve("K") == "v"

    def test_auto_unreachable_url_falls_back_to_env(self):
        cfg = OpenBaoConfig(
            mode="auto",
            external_url="http://127.0.0.1:1",  # nothing listening
            external_token="t",  # test token
        )
        # Even if connect() builds a client, the bounded health check must fail
        # and we fall back to env — never raise, never hang.
        resolver = build_secrets_resolver(openbao_config=cfg, env_overrides={"K2": "fromenv"})
        assert resolver.resolve("K2") == "fromenv"

    def test_auto_reachable_url_uses_openbao(self):
        cfg = OpenBaoConfig(
            mode="auto",
            external_url="http://vault.local:8200",
            external_token="t",  # test token
        )
        fake_client = MagicMock()
        fake_client.is_authenticated.return_value = True
        with patch("general_ludd.secrets.manager.hvac.Client", return_value=fake_client):
            resolver = build_secrets_resolver(openbao_config=cfg)
        # The OpenBao-backed manager (not EnvSecretsManager) was chosen.
        assert not isinstance(resolver, EnvSecretsManager)


class TestMigratedSecretReadBack:
    def test_migrated_secret_resolves_after_env_var_deleted(self):
        # In-memory KV behind a real SecretsManager via a mock hvac client.
        store: dict[str, dict] = {}

        fake_client = MagicMock()
        fake_client.is_authenticated.return_value = True

        def _create(path, secret, mount_point):
            store[path] = dict(secret)

        def _read(path, mount_point):
            if path not in store:
                raise KeyError(path)
            return {"data": {"data": store[path]}}

        fake_client.secrets.kv.v2.create_or_update_secret.side_effect = _create
        fake_client.secrets.kv.v2.read_secret_version.side_effect = _read

        mgr = SecretsManager(client=fake_client, config=OpenBaoConfig(kv_mount="secret"))

        env_var = "GLUDD_TEST_AUTO_KEY"
        os.environ[env_var] = "supersecret"  # test value
        try:
            profiles = [{"model_profile_id": "p1", "credential_alias": env_var}]
            result = migrate_profile_secrets(mgr, profiles)
            assert result["migrated"] == 1
        finally:
            # Delete the env var: resolution must now come from the vault.
            os.environ.pop(env_var, None)

        # Read-back: the alias resolves from OpenBao even with the env var gone.
        assert mgr.resolve(env_var) == "supersecret"
