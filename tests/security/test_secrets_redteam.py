"""Red-team regression tests for secrets resolution (finding S-1).

S-1 (MED/HIGH): EnvSecretsManager.resolve previously returned
os.environ.get(alias) for ANY name. A caller — or a hot-reloaded model
profile whose credential_alias is attacker-controlled — could therefore
exfiltrate arbitrary process env vars: GLUDD_AUTH_PSK, cloud provider keys
(AWS_SECRET_ACCESS_KEY, etc.). ProjectSecretsManager.resolve simply
delegated to the base manager, so it was not project-scoped either.

FIX: restrict ambient-env resolution to an allowlist — explicitly
registered overrides, recognized credential naming conventions
(*_API_KEY, *_API_BASE, *_BASE_URL, *_API_URL, *_AUTH_TOKEN, GLUDD_SECRET_*),
or an explicit per-instance allow-set. Non-allowlisted names (GLUDD_AUTH_PSK,
PATH, HOME, AWS_SECRET_ACCESS_KEY) resolve to None. Legitimate model
gateway api-key/api-base aliases still resolve because they match the
naming convention.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretsManager
from general_ludd.secrets.migration import migrate_profile_secrets
from general_ludd.secrets.project_secrets import ProjectSecretsManager


class TestEnvSecretsAllowlist:
    def test_gludd_psk_not_resolvable(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "super-secret-psk"}):
            mgr = EnvSecretsManager()
            assert mgr.resolve("GLUDD_AUTH_PSK") is None, (
                "S-1: GLUDD_AUTH_PSK must NEVER resolve through the secrets manager."
            )

    def test_arbitrary_env_var_not_resolvable(self):
        for name in ("PATH", "HOME", "AWS_SECRET_ACCESS_KEY", "SHELL"):
            with patch.dict(os.environ, {name: "leak-me"}):
                mgr = EnvSecretsManager()
                assert mgr.resolve(name) is None, (
                    f"S-1: arbitrary env var {name} must not be exfiltratable."
                )

    def test_legit_api_key_alias_still_resolves(self):
        # The model gateway resolves credential_alias values like these.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-xyz"}):  # pragma: allowlist secret
            mgr = EnvSecretsManager()
            assert mgr.resolve("OPENAI_API_KEY") == "sk-openai-xyz"
        with patch.dict(os.environ, {"ZAI_API_KEY": "sk-zai-xyz"}):  # pragma: allowlist secret
            mgr = EnvSecretsManager()
            assert mgr.resolve("ZAI_API_KEY") == "sk-zai-xyz"

    def test_legit_api_base_alias_still_resolves(self):
        with patch.dict(os.environ, {"ZAI_BASE_URL": "https://api.example/v4"}):
            mgr = EnvSecretsManager()
            assert mgr.resolve("ZAI_BASE_URL") == "https://api.example/v4"
        with patch.dict(os.environ, {"OPENAI_API_BASE": "https://api.example"}):
            mgr = EnvSecretsManager()
            assert mgr.resolve("OPENAI_API_BASE") == "https://api.example"

    def test_slurm_infra_aliases_still_resolve(self):
        # routers/slurm.py resolves these literal aliases.
        with patch.dict(
            os.environ,
            {"slurm_api_url": "http://slurm", "slurm_auth_token": "tok"},
        ):
            mgr = EnvSecretsManager()
            assert mgr.resolve("slurm_api_url") == "http://slurm"
            assert mgr.resolve("slurm_auth_token") == "tok"

    def test_override_always_resolvable(self):
        mgr = EnvSecretsManager(overrides={"ANY_NAME": "from-override"})
        assert mgr.resolve("ANY_NAME") == "from-override"

    def test_explicit_allow_set_resolves(self):
        with patch.dict(os.environ, {"MY_CUSTOM_CRED": "val"}):
            mgr = EnvSecretsManager(allow={"MY_CUSTOM_CRED"})
            assert mgr.resolve("MY_CUSTOM_CRED") == "val"


class TestProjectSecretsScoping:
    def test_project_resolve_does_not_leak_psk(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "super-secret-psk"}):
            base = EnvSecretsManager()
            pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
            assert pmgr.resolve("GLUDD_AUTH_PSK") is None, (
                "S-1: project-scoped resolve must not leak GLUDD_AUTH_PSK either."
            )

    def test_project_resolve_prefers_scoped_secret(self):
        base = MagicMock()
        base.read_secret.return_value = {"value": "scoped-val"}
        pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
        result = pmgr.resolve("db_password")
        # Looked up under the project-scoped path, not the global namespace.
        scoped_call = base.read_secret.call_args[0][0]
        assert "proj-x" in scoped_call
        assert result == "scoped-val"


class TestProjectKvPathContainment:
    """S-1 (KV path containment): _scoped_path must reject traversal."""

    def test_project_id_with_slash_rejected(self):
        base = MagicMock()
        with pytest.raises(ValueError):
            ProjectSecretsManager(base_manager=base, project_id="proj/../other")

    def test_project_id_with_dotdot_rejected(self):
        base = MagicMock()
        with pytest.raises(ValueError):
            ProjectSecretsManager(base_manager=base, project_id="..")

    def test_scoped_path_stays_under_project_prefix(self):
        base = MagicMock()
        pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
        scoped = pmgr._scoped_path("db/password")
        assert scoped == "projects/proj-x/db/password"
        assert scoped.startswith("projects/proj-x/")

    def test_path_traversal_in_path_arg_rejected(self):
        base = MagicMock()
        pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
        # A traversal in the per-call path must not escape the project prefix.
        with pytest.raises(ValueError):
            pmgr._scoped_path("../../other-project/secret")

    def test_write_with_traversal_path_blocked(self):
        base = MagicMock()
        pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
        with pytest.raises(ValueError):
            pmgr.write_secret("../escape", {"value": "x"})
        base.write_secret.assert_not_called()


class TestMigrationAllowlist:
    """S-1: migrate_profile_secrets must honor the EnvSecretsManager allowlist."""

    def test_psk_alias_skipped_not_migrated(self):
        mgr = MagicMock()
        profiles = [
            {"model_profile_id": "p1", "credential_alias": "GLUDD_AUTH_PSK"},
        ]
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "super-secret-psk"}):
            result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 0
        assert "GLUDD_AUTH_PSK" in result["skipped"]
        mgr.write_secret.assert_not_called()

    def test_arbitrary_env_aliases_skipped(self):
        mgr = MagicMock()
        profiles = [
            {"model_profile_id": "p1", "credential_alias": "PATH"},
            {"model_profile_id": "p2", "credential_alias": "AWS_SECRET_ACCESS_KEY"},
        ]
        with patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "AWS_SECRET_ACCESS_KEY": "leak"},  # pragma: allowlist secret
        ):
            result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 0
        assert "PATH" in result["skipped"]
        assert "AWS_SECRET_ACCESS_KEY" in result["skipped"]
        mgr.write_secret.assert_not_called()

    def test_api_key_alias_still_migrates(self):
        mgr = MagicMock()
        profiles = [
            {"model_profile_id": "p1", "credential_alias": "OPENAI_API_KEY"},
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-real"}):  # pragma: allowlist secret
            result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 1
        assert "OPENAI_API_KEY" in result["aliases"]
        mgr.write_secret.assert_called_once()
        # The value written is the resolved env value, not None.
        written_value = mgr.write_secret.call_args[0][1]
        assert written_value == {"value": "sk-real"}


class TestLocalContainerHardening:
    """H-1: dev container must bind loopback + use a per-boot random token."""

    def _start(self, mgr: SecretsManager) -> tuple[str | None, list[str]]:
        captured: dict[str, list[str]] = {}

        async def fake_exec(*args: str, **kwargs: object):
            captured["args"] = list(args)
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"container-abc\n", b""))
            return proc

        resolver = MagicMock()
        resolver.get_container_runtime.return_value = "docker"
        with patch(
            "general_ludd.secrets.manager.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ), patch("platform.system", return_value="Linux"):
            cid = asyncio.run(mgr.start_local_container(binary_resolver=resolver))
        return cid, captured.get("args", [])

    def test_no_root_token_literal(self):
        mgr = SecretsManager(config=OpenBaoConfig(mode="auto"))
        cid, args = self._start(mgr)
        assert cid == "container-abc"
        joined = " ".join(args)
        assert "-dev-root-token-id=root" not in joined
        assert "root" not in args  # no bare 'root' literal token

    def test_binds_loopback_only(self):
        mgr = SecretsManager(config=OpenBaoConfig(mode="auto"))
        _cid, args = self._start(mgr)
        joined = " ".join(args)
        assert "0.0.0.0:8200" not in joined
        assert "-dev-listen-address=127.0.0.1:8200" in args
        assert "127.0.0.1:8200:8200" in args

    def test_per_boot_token_surfaced_on_bootstrap_result(self):
        mgr = SecretsManager(config=OpenBaoConfig(mode="auto"))
        _cid, args = self._start(mgr)
        # The minted token must appear in the dev-root-token-id arg AND on the
        # bootstrap result so connect() can authenticate to it.
        token_arg = next(a for a in args if a.startswith("-dev-root-token-id="))
        token = token_arg.split("=", 1)[1]
        assert token and token != "root"
        assert mgr._local_bootstrap_result is not None
        assert mgr._local_bootstrap_result.token == token
        assert mgr._local_bootstrap_result.container_token == token

    def test_token_is_per_boot_random(self):
        mgr1 = SecretsManager(config=OpenBaoConfig(mode="auto"))
        mgr2 = SecretsManager(config=OpenBaoConfig(mode="auto"))
        _c1, args1 = self._start(mgr1)
        _c2, args2 = self._start(mgr2)
        t1 = next(a for a in args1 if a.startswith("-dev-root-token-id=")).split("=", 1)[1]
        t2 = next(a for a in args2 if a.startswith("-dev-root-token-id=")).split("=", 1)[1]
        assert t1 != t2

    def test_external_mode_refuses_local_container(self):
        mgr = SecretsManager(
            config=OpenBaoConfig(
                mode="external",
                external_url="https://bao.example.com:8200",
                external_token="s.ext",
            )
        )
        resolver = MagicMock()
        resolver.get_container_runtime.return_value = "docker"
        with pytest.raises(RuntimeError):
            asyncio.run(mgr.start_local_container(binary_resolver=resolver))


class TestAppRoleRotation:
    """W5.1: rotate_approle_secret_id mints new + destroys old; bounded TTLs."""

    def _connected_mgr(self) -> SecretsManager:
        mgr = SecretsManager(config=OpenBaoConfig())
        mgr._client = MagicMock()
        return mgr

    def test_setup_approle_uses_bounded_ttls(self):
        mgr = self._connected_mgr()
        mgr._client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "rid-1"}
        }
        mgr._client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "sid-1", "secret_id_accessor": "acc-1"}
        }
        mgr.setup_approle("role-x")
        kwargs = mgr._client.auth.approle.create_role.call_args[1]
        assert kwargs.get("secret_id_ttl")
        assert kwargs.get("token_ttl")

    def test_rotate_mints_new_and_destroys_old(self):
        mgr = self._connected_mgr()
        mgr._client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "rid-1"}
        }
        # First secret_id (issued at setup) tracked under accessor acc-1.
        mgr._client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "sid-1", "secret_id_accessor": "acc-1"}
        }
        mgr.setup_approle("role-x")
        assert mgr._secret_id_accessors["role-x"] == ["acc-1"]

        # Rotation mints sid-2 (accessor acc-2) and destroys acc-1.
        mgr._client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "sid-2", "secret_id_accessor": "acc-2"}
        }
        new_sid = mgr.rotate_approle_secret_id("role-x")
        assert new_sid == "sid-2"
        mgr._client.auth.approle.destroy_secret_id_accessor.assert_called_once_with(
            "role-x", "acc-1"
        )
        # Old accessor dropped; only the freshly minted one remains tracked.
        assert mgr._secret_id_accessors["role-x"] == ["acc-2"]

    def test_rotate_without_client_raises(self):
        mgr = SecretsManager(config=OpenBaoConfig())
        with pytest.raises(RuntimeError):
            mgr.rotate_approle_secret_id("role-x")
