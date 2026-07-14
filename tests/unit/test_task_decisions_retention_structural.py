"""Structural tests for db/task_decisions_retention.py — retention cleanup."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.task_decisions_retention import (
    DEFAULT_RETENTION_DAYS,
    cleanup_old_task_decisions,
)


class TestCleanupOldTaskDecisions:
    @pytest.mark.asyncio
    async def test_negative_retention_days_raises(self):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(
                AsyncMock(spec=AsyncSession), retention_days=-1,
            )
        assert True  # pytest.raises confirms the error fired

    @pytest.mark.asyncio
    async def test_zero_retention_days_raises(self):
        with pytest.raises(ValueError, match="retention_days must be > 0"):
            await cleanup_old_task_decisions(
                AsyncMock(spec=AsyncSession), retention_days=0,
            )
        assert True

    @pytest.mark.asyncio
    async def test_dry_run_counts_rows(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 42
        session.execute.return_value = mock_result

        count = await cleanup_old_task_decisions(session, dry_run=True)
        assert count == 42

    @pytest.mark.asyncio
    async def test_dry_run_zero_rows(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        session.execute.return_value = mock_result

        count = await cleanup_old_task_decisions(session, dry_run=True)
        assert count == 0

    @pytest.mark.asyncio
    async def test_delete_rows(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        type(mock_result).rowcount = 15
        session.execute.return_value = mock_result

        deleted = await cleanup_old_task_decisions(session)
        assert deleted == 15

    @pytest.mark.asyncio
    async def test_delete_zero_rows(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        type(mock_result).rowcount = 0
        session.execute.return_value = mock_result

        deleted = await cleanup_old_task_decisions(session)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_custom_retention_days(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        type(mock_result).rowcount = 5
        session.execute.return_value = mock_result

        deleted = await cleanup_old_task_decisions(session, retention_days=7)
        assert deleted == 5

    @pytest.mark.asyncio
    async def test_custom_now_injection(self):
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 3
        session.execute.return_value = mock_result

        custom_now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        count = await cleanup_old_task_decisions(session, now=custom_now, dry_run=True)
        assert count == 3

    def test_default_retention_is_90(self):
        assert DEFAULT_RETENTION_DAYS == 90
