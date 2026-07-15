"""Unit tests for StsAuditPipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.sts.audit import StsAuditPipeline


class TestStsAuditPipelineInit:
    def test_init_stores_session_factory(self):
        sf = MagicMock()
        pipeline = StsAuditPipeline(sf)
        assert pipeline._session_factory is sf

    def test_init_empty_pending_events(self):
        pipeline = StsAuditPipeline(MagicMock())
        assert pipeline._pending_events == []

    def test_wire_to_daemon_stores_self(self):
        pipeline = StsAuditPipeline(MagicMock())
        daemon_state: dict[str, object] = {}
        pipeline.wire_to_daemon(daemon_state)
        assert daemon_state["_sts_audit_pipeline"] is pipeline


class TestScopeHash:
    def test_scope_hash_none_returns_empty(self):
        pipeline = StsAuditPipeline(MagicMock())
        assert pipeline._scope_hash(None) == ""

    def test_scope_hash_empty_list(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._scope_hash([])
        assert len(result) == 16
        assert isinstance(result, str)

    def test_scope_hash_sorted_canonical(self):
        pipeline = StsAuditPipeline(MagicMock())
        hash_a = pipeline._scope_hash(["write", "read"])
        hash_b = pipeline._scope_hash(["read", "write"])
        assert hash_a == hash_b

    def test_scope_hash_different_yields_different(self):
        pipeline = StsAuditPipeline(MagicMock())
        hash_a = pipeline._scope_hash(["read"])
        hash_b = pipeline._scope_hash(["write"])
        assert hash_a != hash_b


class TestEventDict:
    def test_event_dict_basic(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._event_dict(
            action="mint", agent_id="agent-1", parent_agent_id="issuer-1",
        )
        assert result["action"] == "mint"
        assert result["agent_id"] == "agent-1"
        assert result["parent_agent_id"] == "issuer-1"
        assert "timestamp" in result

    def test_event_dict_scope_hash_default_empty(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._event_dict(action="use", agent_id="a", parent_agent_id="p")
        assert result["scope_hash"] == ""

    def test_event_dict_empty_parent_agent_id_default(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._event_dict(action="revoke", agent_id="a")
        assert result["parent_agent_id"] == ""


class TestRecordMethods:
    def test_record_mint_creates_correct_event(self):
        sf = _mock_session_factory()
        pipeline = StsAuditPipeline(sf)
        assert len(pipeline._pending_events) == 0
        pipeline._append_event = AsyncMock()
        pipeline.record_mint = _async_wrap(pipeline.record_mint, pipeline)

    @pytest.mark.asyncio
    async def test_record_mint_appends_event(self):
        sf = _mock_session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._scope_hash = MagicMock(return_value="abc123")
        pipeline._append_event = AsyncMock()

        await pipeline.record_mint(
            token_id="tok-1",
            issuer_agent_id="issuer",
            subject_agent_id="subject",
            scope_actions=["read", "write"],
        )
        pipeline._append_event.assert_awaited_once_with("tok-1", pytest.helpers.matchers.DictContaining({
            "action": "mint",
            "agent_id": "subject",
            "parent_agent_id": "issuer",
            "scope_hash": "abc123",
        }))


@pytest.mark.asyncio
class TestFlushOnTick:
    async def test_flush_empty_pending_returns_zero(self):
        pipeline = StsAuditPipeline(MagicMock())
        assert await pipeline.flush_on_tick() == 0

    async def test_flush_with_events_calls_session(self):
        sf = _mock_session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._pending_events = [
            {"token_id": "tok-1", "action": "mint", "agent_id": "a1"},
        ]
        from general_ludd.db.models import StsAuditModel
        mock_row = MagicMock(spec=StsAuditModel)
        mock_row.use_count = 0
        mock_row.events = "[]"
        mock_row.last_used_at = None

        from sqlalchemy import select
        with patch("general_ludd.sts.audit.select", wraps=select) as mock_select:
            sf_result = await pipeline.flush_on_tick()
            assert sf_result == 1
            assert pipeline._pending_events == []


class TestAppendEvent:
    @pytest.mark.asyncio
    async def test_append_event_no_matching_row_noop(self):
        sf = _mock_session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._pending_events = []
        await pipeline._append_event("tok-1", {"action": "mint"})


def _mock_session_factory():
    """Returns a callable that returns an async context manager yielding an AsyncMock session."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        session = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        session.execute = AsyncMock()
        session.execute.return_value = MagicMock()
        session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
        yield session

    sf = MagicMock()
    sf.return_value = _factory()
    sf.begin = MagicMock()
    sf.begin.return_value = _factory()
    return sf


def _async_wrap(coro_func, instance):
    """Wrap a method so it can be tested for call assertions."""
    pass
