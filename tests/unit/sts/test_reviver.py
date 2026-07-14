"""Unit tests for TokenReviver (Phase P3)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.secrets.manager import AppRoleCreds
from general_ludd.sts.reviver import TokenRevivalError, TokenReviver


class TestTokenReviver:
    @pytest.mark.asyncio
    async def test_revive_mints_fresh_secret_id(self):
        """revive() returns AppRoleCreds with the stored role_id and a fresh secret_id."""
        mock_secrets = MagicMock()
        mock_secrets.rotate_approle_secret_id.return_value = "fresh-secret-42"

        mock_record = MagicMock()
        mock_record.role_name = "agent-agent-abc"
        mock_record.role_id = "stored-role-id"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.increment_hydration.return_value = None

        reviver = TokenReviver(mock_secrets, mock_store)
        creds = await reviver.revive("agent-abc")

        mock_store.get.assert_called_once_with("agent-abc")
        mock_secrets.rotate_approle_secret_id.assert_called_once_with(
            "agent-agent-abc"
        )
        mock_store.increment_hydration.assert_called_once_with("agent-abc")
        assert isinstance(creds, AppRoleCreds)
        assert creds.role_id == "stored-role-id"
        assert creds.secret_id == "fresh-secret-42"

    @pytest.mark.asyncio
    async def test_revive_increments_hydration_count(self):
        """revive() increments hydration_count via the store."""
        mock_secrets = MagicMock()
        mock_secrets.rotate_approle_secret_id.return_value = "new-secret"

        mock_record = MagicMock()
        mock_record.role_name = "agent-agent-xyz"
        mock_record.role_id = "role-xyz"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.increment_hydration.return_value = None

        reviver = TokenReviver(mock_secrets, mock_store)
        await reviver.revive("agent-xyz")

        mock_store.increment_hydration.assert_called_once_with("agent-xyz")

    @pytest.mark.asyncio
    async def test_revive_nonexistent_role_raises_error(self):
        """revive() raises TokenRevivalError when no record exists."""
        mock_secrets = MagicMock()
        mock_store = AsyncMock()
        mock_store.get.return_value = None

        reviver = TokenReviver(mock_secrets, mock_store)

        with pytest.raises(TokenRevivalError, match="No token record"):
            await reviver.revive("nonexistent")

    @pytest.mark.asyncio
    async def test_revive_after_revoke_raises_error(self):
        """revive() raises TokenRevivalError when the token is already revoked."""
        mock_secrets = MagicMock()

        mock_record = MagicMock()
        mock_record.revoked_at = datetime(2026, 7, 14, tzinfo=UTC)
        mock_record.role_name = "agent-agent-zzz"
        mock_record.role_id = "role-zzz"

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record

        reviver = TokenReviver(mock_secrets, mock_store)

        with pytest.raises(TokenRevivalError, match="revoked"):
            await reviver.revive("agent-zzz")

    @pytest.mark.asyncio
    async def test_revive_secrets_rotation_failure_raises_error(self):
        """revive() wraps SecretsManager errors in TokenRevivalError."""
        mock_secrets = MagicMock()
        mock_secrets.rotate_approle_secret_id.side_effect = RuntimeError(
            "AppRole gone"
        )

        mock_record = MagicMock()
        mock_record.role_name = "agent-agent-gone"
        mock_record.role_id = "role-gone"
        mock_record.revoked_at = None

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record

        reviver = TokenReviver(mock_secrets, mock_store)

        with pytest.raises(TokenRevivalError):
            await reviver.revive("agent-gone")
