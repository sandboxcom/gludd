"""Unit tests for TokenStore."""


from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.store import TokenStore


def test_import():
    """TokenStore class is importable."""
    assert TokenStore is not None


@pytest.mark.asyncio
async def test_store_and_get():
    """store() inserts a record; get() retrieves it by agent_id."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session_factory = MagicMock()
    mock_session_factory.begin.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    mock_model = MagicMock()
    mock_model.agent_id = "agent-abc"

    store = TokenStore(mock_session_factory)

    await store.store(mock_model)
    mock_session.add.assert_called_once_with(mock_model)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_model
    mock_session.execute.return_value = mock_result
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    record = await store.get("agent-abc")
    assert record is not None
    assert record.agent_id == "agent-abc"
