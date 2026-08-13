"""Unit tests for StsAuditPipeline (Phase P4 — audit event pipeline)."""

import json as _json
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.audit import StsAuditPipeline


class TestStsAuditPipeline:
    @pytest.mark.asyncio
    async def test_record_mint_appends_event(self):
        """record_mint() appends a 'mint' event to the StsAuditModel row."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0
        mock_row.last_used_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_mint(
            token_id="tok-abc",
            issuer_agent_id="parent-1",
            subject_agent_id="agent-1",
            scope_actions=["read", "write"],
        )

        events = _json.loads(mock_row.events)
        assert len(events) == 1
        assert events[0]["action"] == "mint"
        assert events[0]["agent_id"] == "agent-1"
        assert events[0]["parent_agent_id"] == "parent-1"
        assert "scope_hash" in events[0]
        assert "timestamp" in events[0]
        assert mock_row.use_count == 1

    @pytest.mark.asyncio
    async def test_record_use_appends_event(self):
        """record_use() appends a 'use' event."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_use(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )

        events = _json.loads(mock_row.events)
        assert len(events) == 1
        assert events[0]["action"] == "use"

    @pytest.mark.asyncio
    async def test_record_renew_appends_event(self):
        """record_renew() appends a 'renew' event."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_renew(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )

        events = _json.loads(mock_row.events)
        assert events[0]["action"] == "renew"

    @pytest.mark.asyncio
    async def test_record_revoke_appends_event(self):
        """record_revoke() appends a 'revoke' event."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_revoke(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )

        events = _json.loads(mock_row.events)
        assert events[0]["action"] == "revoke"

    @pytest.mark.asyncio
    async def test_record_revive_appends_event(self):
        """record_revive() appends a 'revive' event."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_revive(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )

        events = _json.loads(mock_row.events)
        assert events[0]["action"] == "revive"

    @pytest.mark.asyncio
    async def test_multiple_events_cumulative(self):
        """Multiple events append to the same row."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_mint("tok-abc", "p", "a")
        await pipeline.record_use("tok-abc", "a", "p")

        events = _json.loads(mock_row.events)
        assert len(events) == 2
        assert events[0]["action"] == "mint"
        assert events[1]["action"] == "use"
        assert mock_row.use_count == 2

    @pytest.mark.asyncio
    async def test_no_row_is_noop(self):
        """When no StsAuditModel row exists, event recording is a no-op."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)
        await pipeline.record_mint("tok-nonexistent", "p", "a")

        mock_session.add.assert_not_called()

    def test_scope_hash_deterministic(self):
        """_scope_hash() produces the same hash for the same input."""
        pipeline = StsAuditPipeline(MagicMock())
        h1 = pipeline._scope_hash(["read", "write"])
        h2 = pipeline._scope_hash(["read", "write"])
        assert h1 == h2
        assert len(h1) == 16

    def test_scope_hash_different_for_different_input(self):
        """_scope_hash() differs for different action sets."""
        pipeline = StsAuditPipeline(MagicMock())
        h1 = pipeline._scope_hash(["read"])
        h2 = pipeline._scope_hash(["read", "write"])
        assert h1 != h2

    def test_scope_hash_none_returns_empty(self):
        """_scope_hash() returns empty string for None input."""
        pipeline = StsAuditPipeline(MagicMock())
        assert pipeline._scope_hash(None) == ""

    def test_event_dict_shape(self):
        """_event_dict() produces correct shape."""
        pipeline = StsAuditPipeline(MagicMock())
        event = pipeline._event_dict(
            action="mint",
            agent_id="a1",
            parent_agent_id="p1",
            scope_hash="abc123",
        )
        assert event["action"] == "mint"
        assert event["agent_id"] == "a1"
        assert event["parent_agent_id"] == "p1"
        assert event["scope_hash"] == "abc123"
        assert isinstance(event["timestamp"], float)

    def test_import(self):
        """StsAuditPipeline class is importable."""
        assert StsAuditPipeline is not None
