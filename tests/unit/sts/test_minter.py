"""Unit tests for TokenMinter."""

from unittest.mock import MagicMock

import pytest

from general_ludd.sts.minter import TokenMinter


def test_import():
    """TokenMinter class is importable."""
    assert TokenMinter is not None


@pytest.mark.asyncio
async def test_mint_calls_setup_approle():
    """mint() calls SecretsManager.setup_approle with the correct role name."""
    mock_secrets = MagicMock()
    mock_secrets.setup_approle.return_value = MagicMock(
        role_id="test-role-id",
        secret_id="test-secret-id",
    )

    minter = TokenMinter(mock_secrets)
    creds = await minter.mint(
        agent_id="agent-abc",
        parent_agent_id="parent-xyz",
        scope=None,
    )

    mock_secrets.setup_approle.assert_called_once_with("agent-agent-abc")
    assert creds.role_id == "test-role-id"
    assert creds.secret_id == "test-secret-id"
