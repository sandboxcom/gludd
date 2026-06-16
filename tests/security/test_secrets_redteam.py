"""Red-team regression tests for secrets resolution (finding S-1).

S-1 (MED/HIGH): EnvSecretsManager.resolve previously returned
os.environ.get(alias) for ANY name. A caller — or a hot-reloaded model
profile whose credential_alias is attacker-controlled — could therefore
exfiltrate arbitrary process env vars: GLUDD_PSK, cloud provider keys
(AWS_SECRET_ACCESS_KEY, etc.). ProjectSecretsManager.resolve simply
delegated to the base manager, so it was not project-scoped either.

FIX: restrict ambient-env resolution to an allowlist — explicitly
registered overrides, recognized credential naming conventions
(*_API_KEY, *_API_BASE, *_BASE_URL, *_API_URL, *_AUTH_TOKEN, GLUDD_SECRET_*),
or an explicit per-instance allow-set. Non-allowlisted names (GLUDD_PSK,
PATH, HOME, AWS_SECRET_ACCESS_KEY) resolve to None. Legitimate model
gateway api-key/api-base aliases still resolve because they match the
naming convention.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.project_secrets import ProjectSecretsManager


class TestEnvSecretsAllowlist:
    def test_gludd_psk_not_resolvable(self):
        with patch.dict(os.environ, {"GLUDD_PSK": "super-secret-psk"}):
            mgr = EnvSecretsManager()
            assert mgr.resolve("GLUDD_PSK") is None, (
                "S-1: GLUDD_PSK must NEVER resolve through the secrets manager."
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
        with patch.dict(os.environ, {"GLUDD_PSK": "super-secret-psk"}):
            base = EnvSecretsManager()
            pmgr = ProjectSecretsManager(base_manager=base, project_id="proj-x")
            assert pmgr.resolve("GLUDD_PSK") is None, (
                "S-1: project-scoped resolve must not leak GLUDD_PSK either."
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
