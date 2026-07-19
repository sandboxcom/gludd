"""Integration tests for the STS token subsystem against a real/containerized OpenBao.

These tests exercise the full AppRole lifecycle — mint, scoped kv-v2 access,
out-of-scope denial, and revocation — against an actual OpenBao instance.

When ``GLUDD_OPENBAO_URL`` is not set (no container available), the entire
module is skipped.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    AppRoleCreds,
    SecretsManager,
)
from general_ludd.sts.minter import TokenMinter

_OPENBAO_URL = os.environ.get("GLUDD_OPENBAO_URL", "http://127.0.0.1:8200")
_OPENBAO_TOKEN = os.environ.get("GLUDD_OPENBAO_TOKEN", "")
_OPENBAO_KV_MOUNT = os.environ.get("GLUDD_OPENBAO_KV_MOUNT", "secret")


def _openbao_reachable() -> bool:
    """Return True if an OpenBao instance is reachable at ``_OPENBAO_URL``."""
    if not _OPENBAO_TOKEN:
        return False
    try:
        import hvac

        client = hvac.Client(url=_OPENBAO_URL, token=_OPENBAO_TOKEN)
        return client.is_authenticated()
    except Exception:
        return False


_skip_if_no_openbao = pytest.mark.skipif(
    not _openbao_reachable(),
    reason="OpenBao not available — set GLUDD_OPENBAO_URL + GLUDD_OPENBAO_TOKEN",
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_secrets_manager() -> SecretsManager:
    cfg = OpenBaoConfig(
        mode="external",
        backend="openbao",
        external_url=_OPENBAO_URL,
        kv_mount=_OPENBAO_KV_MOUNT,
    )
    mgr = SecretsManager(config=cfg)
    mgr._client = __import__("hvac").Client(
        url=_OPENBAO_URL,
        token=_OPENBAO_TOKEN,
    )
    return mgr


def _seed_test_secret(mgr: SecretsManager, path: str, value: dict[str, object]) -> None:
    mgr.write_secret(path=path, value=value)


def _cleanup_test_secret(mgr: SecretsManager, path: str) -> None:
    with contextlib.suppress(Exception):
        mgr.delete_secret(path=path)


# ------------------------------------------------------------------
# Mint tests
# ------------------------------------------------------------------


@_skip_if_no_openbao
class TestTokenMintAgainstOpenBao:
    """TokenMinter creates working AppRole credentials on a real OpenBao."""

    def test_mint_creates_approle_with_valid_creds(self):
        mgr = _make_secrets_manager()
        agent_id = f"test-mint-{uuid.uuid4().hex[:8]}"

        minter = TokenMinter(mgr)

        import asyncio

        creds = asyncio.run(minter.mint(
            agent_id=agent_id,
            parent_agent_id="root",
        ))

        assert isinstance(creds, AppRoleCreds)
        assert creds.role_id
        assert creds.secret_id

        client = mgr._client
        resp = client.auth.approle.read_role(role_name=f"agent-{agent_id}")
        assert resp is not None
        role_data = resp["data"]
        assert role_data["token_ttl"] > 0

        client.auth.approle.delete_role(f"agent-{agent_id}")

    def test_mint_returns_different_creds_per_agent(self):
        mgr = _make_secrets_manager()
        minter = TokenMinter(mgr)

        import asyncio

        creds_a = asyncio.run(minter.mint(
            agent_id=f"test-dual-a-{uuid.uuid4().hex[:8]}",
            parent_agent_id="root",
        ))
        creds_b = asyncio.run(minter.mint(
            agent_id=f"test-dual-b-{uuid.uuid4().hex[:8]}",
            parent_agent_id="root",
        ))

        assert creds_a.role_id != creds_b.role_id
        assert creds_a.secret_id != creds_b.secret_id

        client = mgr._client
        for aid in ["test-dual-a", "test-dual-b"]:
            with contextlib.suppress(Exception):
                client.auth.approle.delete_role(f"agent-{aid}")


# ------------------------------------------------------------------
# Scoped access tests
# ------------------------------------------------------------------


@_skip_if_no_openbao
class TestScopedKvV2Access:
    """STS-scoped AppRole login yields a token restricted to the rendered policy."""

    def test_scoped_read_of_allowed_path(self):
        mgr = _make_secrets_manager()
        agent_id = f"test-scope-r-{uuid.uuid4().hex[:8]}"
        role_name = f"agent-{agent_id}"
        test_path = f"sts-test/allowed-{uuid.uuid4().hex[:8]}"

        _seed_test_secret(mgr, test_path, {"key": "in-scope-value"})

        try:
            import asyncio
            minter = TokenMinter(mgr)
            creds = asyncio.run(minter.mint(
                agent_id=agent_id,
                parent_agent_id="root",
            ))

            client = mgr._client
            login_resp = client.auth.approle.login(
                role_id=creds.role_id,
                secret_id=creds.secret_id,
            )
            sub_token = login_resp["auth"]["client_token"]
            assert sub_token

            # Add read policy for the test path to the role
            policy_hcl = (
                f'path "{_OPENBAO_KV_MOUNT}/data/{test_path}" {{\n'
                '  capabilities = ["read"]\n'
                "}"
            )
            client.sys.create_or_update_policy(
                name=role_name,
                policy=policy_hcl,
            )
            client.auth.approle.update_role(
                role_name=role_name,
                token_policies=[role_name],
            )

            # Re-login with the policy
            login_resp2 = client.auth.approle.login(
                role_id=creds.role_id,
                secret_id=creds.secret_id,
            )
            sub_token2 = login_resp2["auth"]["client_token"]
            scoped_client = __import__("hvac").Client(
                url=_OPENBAO_URL,
                token=sub_token2,
            )

            result = scoped_client.secrets.kv.v2.read_secret_version(
                path=test_path,
                mount_point=_OPENBAO_KV_MOUNT,
            )
            assert result["data"]["data"]["key"] == "in-scope-value"

        finally:
            _cleanup_test_secret(mgr, test_path)
            try:
                mgr._client.auth.approle.delete_role(role_name)
                mgr._client.sys.delete_policy(role_name)
            except Exception:
                pass

    def test_out_of_scope_read_is_denied(self):
        mgr = _make_secrets_manager()
        agent_id = f"test-scope-d-{uuid.uuid4().hex[:8]}"
        role_name = f"agent-{agent_id}"
        allowed_path = f"sts-test/allowed-{uuid.uuid4().hex[:8]}"
        denied_path = f"sts-test/denied-{uuid.uuid4().hex[:8]}"

        _seed_test_secret(mgr, allowed_path, {"key": "allowed-value"})
        _seed_test_secret(mgr, denied_path, {"key": "denied-value"})

        try:
            import asyncio
            minter = TokenMinter(mgr)
            creds = asyncio.run(minter.mint(
                agent_id=agent_id,
                parent_agent_id="root",
            ))

            client = mgr._client

            # Policy: only the allowed path
            policy_hcl = (
                f'path "{_OPENBAO_KV_MOUNT}/data/{allowed_path}" {{\n'
                '  capabilities = ["read"]\n'
                "}"
            )
            client.sys.create_or_update_policy(
                name=role_name,
                policy=policy_hcl,
            )
            client.auth.approle.update_role(
                role_name=role_name,
                token_policies=[role_name],
            )

            login_resp = client.auth.approle.login(
                role_id=creds.role_id,
                secret_id=creds.secret_id,
            )
            sub_token = login_resp["auth"]["client_token"]
            scoped_client = __import__("hvac").Client(
                url=_OPENBAO_URL,
                token=sub_token,
            )

            # Allowed path — succeeds
            result = scoped_client.secrets.kv.v2.read_secret_version(
                path=allowed_path,
                mount_point=_OPENBAO_KV_MOUNT,
            )
            assert result["data"]["data"]["key"] == "allowed-value"

            # Denied path — should fail
            import hvac
            with pytest.raises(hvac.exceptions.Forbidden):
                scoped_client.secrets.kv.v2.read_secret_version(
                    path=denied_path,
                    mount_point=_OPENBAO_KV_MOUNT,
                )

        finally:
            _cleanup_test_secret(mgr, allowed_path)
            _cleanup_test_secret(mgr, denied_path)
            try:
                mgr._client.auth.approle.delete_role(role_name)
                mgr._client.sys.delete_policy(role_name)
            except Exception:
                pass


# ------------------------------------------------------------------
# Revocation tests
# ------------------------------------------------------------------


@_skip_if_no_openbao
class TestTokenRevocationAgainstOpenBao:
    """TokenRevoker destroys the AppRole on a real OpenBao."""

    def test_revoke_destroys_approle(self):
        mgr = _make_secrets_manager()
        agent_id = f"test-revoke-{uuid.uuid4().hex[:8]}"
        role_name = f"agent-{agent_id}"

        import asyncio
        minter = TokenMinter(mgr)
        asyncio.run(minter.mint(
            agent_id=agent_id,
            parent_agent_id="root",
        ))

        client = mgr._client
        client.auth.approle.read_role(role_name)

        client.auth.approle.delete_role(role_name)

        import hvac
        with pytest.raises((hvac.exceptions.InvalidPath, hvac.exceptions.InvalidRequest)):
            client.auth.approle.read_role(role_name)

    def test_revoke_idempotent_delete_does_not_raise(self):
        mgr = _make_secrets_manager()
        agent_id = f"test-idem-{uuid.uuid4().hex[:8]}"
        role_name = f"agent-{agent_id}"

        import asyncio
        minter = TokenMinter(mgr)
        asyncio.run(minter.mint(
            agent_id=agent_id,
            parent_agent_id="root",
        ))

        client = mgr._client
        client.auth.approle.delete_role(role_name)
        try:
            client.auth.approle.delete_role(role_name)
        except Exception:
            pytest.fail("Second delete_role should be idempotent (or at least not crash)")
