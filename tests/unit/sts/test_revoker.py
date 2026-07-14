"""Unit tests for TokenRevoker (Phase P3/P4)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.revoker import TokenRevoker


class TestTokenRevoker:
    @pytest.mark.asyncio
    async def test_revoke_destroys_approle(self):
        """revoke() calls delete_role on the OpenBao AppRole."""
        mock_client = MagicMock()
        mock_client.auth.approle.delete_role.return_value = None

        mock_secrets = MagicMock()
        mock_secrets._client = mock_client

        mock_record = MagicMock()
        mock_record.token_id = "tok-abc"
        mock_record.role_name = "agent-agent-abc"
        mock_record.role_id = "role-abc"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.revoke.return_value = None

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("agent-abc")

        mock_client.auth.approle.delete_role.assert_called_once_with(
            "agent-agent-abc"
        )

    @pytest.mark.asyncio
    async def test_revoke_marks_revoked_at(self):
        """revoke() marks the token record as revoked in the store."""
        mock_client = MagicMock()
        mock_client.auth.approle.delete_role.return_value = None

        mock_secrets = MagicMock()
        mock_secrets._client = mock_client

        mock_record = MagicMock()
        mock_record.token_id = "tok-xyz"
        mock_record.role_name = "agent-agent-xyz"
        mock_record.role_id = "role-xyz"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.revoke.return_value = None

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("agent-xyz")

        mock_store.revoke.assert_called_once_with("tok-xyz")

    @pytest.mark.asyncio
    async def test_revoke_no_record_is_noop(self):
        """revoke() with no token record is a no-op (warning, no exception)."""
        mock_secrets = MagicMock()
        mock_store = AsyncMock()
        mock_store.get.return_value = None

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("nonexistent")

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_is_noop(self):
        """revoke() on an already-revoked token is a no-op."""
        mock_record = MagicMock()
        mock_record.revoked_at = datetime(2026, 7, 14, tzinfo=UTC)
        mock_record.token_id = "tok-done"
        mock_record.role_name = "agent-agent-done"

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record

        mock_secrets = MagicMock()

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("agent-done")

        mock_store.revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_approle_delete_failure_is_warned(self):
        """revoke() warns but still marks revoked when delete_role fails."""
        mock_client = MagicMock()
        mock_client.auth.approle.delete_role.side_effect = Exception("gone")

        mock_secrets = MagicMock()
        mock_secrets._client = mock_client

        mock_record = MagicMock()
        mock_record.token_id = "tok-fail"
        mock_record.role_name = "agent-agent-fail"
        mock_record.role_id = "role-fail"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.revoke.return_value = None

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("agent-fail")

        mock_store.revoke.assert_called_once_with("tok-fail")

    @pytest.mark.asyncio
    async def test_revoke_no_client_raises_error(self):
        """revoke() raises RuntimeError when SecretsManager has no client."""
        mock_secrets = MagicMock()
        mock_secrets._client = None

        mock_record = MagicMock()
        mock_record.token_id = "tok-nc"
        mock_record.role_name = "agent-agent-nc"
        mock_record.role_id = "role-nc"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record

        revoker = TokenRevoker(mock_secrets, mock_store)

        with pytest.raises(RuntimeError, match="not connected"):
            await revoker.revoke("agent-nc")
