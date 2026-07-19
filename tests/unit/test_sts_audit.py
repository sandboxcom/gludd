"""Unit tests for StsAuditPipeline."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.audit import StsAuditPipeline


def _session_factory():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute.return_value = result
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock()
    sf.begin = MagicMock()
    sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.begin.return_value.__aexit__ = AsyncMock()
    return sf


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

    def test_scope_hash_empty_list_returns_consistent(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._scope_hash([])
        canonical = json.dumps([], sort_keys=True)
        expected = hashlib.md5(canonical.encode(), usedforsecurity=False).hexdigest()[:16]
        assert result == expected
        assert len(result) == 16

    def test_scope_hash_sorted_canonical_same_result(self):
        pipeline = StsAuditPipeline(MagicMock())
        a = pipeline._scope_hash(["write", "read"])
        b = pipeline._scope_hash(["read", "write"])
        assert a == b

    def test_scope_hash_different_actions_different_result(self):
        pipeline = StsAuditPipeline(MagicMock())
        a = pipeline._scope_hash(["read"])
        b = pipeline._scope_hash(["write"])
        assert a != b


class TestEventDict:
    def test_event_dict_all_fields(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._event_dict(
            action="mint",
            agent_id="agent-1",
            parent_agent_id="issuer-1",
            scope_hash="abc",
        )
        assert result["action"] == "mint"
        assert result["agent_id"] == "agent-1"
        assert result["parent_agent_id"] == "issuer-1"
        assert result["scope_hash"] == "abc"
        assert "timestamp" in result
        assert isinstance(result["timestamp"], float)

    def test_event_dict_defaults_empty_strings(self):
        pipeline = StsAuditPipeline(MagicMock())
        result = pipeline._event_dict(action="use", agent_id="a")
        assert result["parent_agent_id"] == ""
        assert result["scope_hash"] == ""


@pytest.mark.asyncio
class TestRecordMethods:
    async def test_record_mint_calls_session_execute(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._scope_hash = MagicMock(return_value="abc123")

        await pipeline.record_mint(
            token_id="tok-1",
            issuer_agent_id="issuer",
            subject_agent_id="subject",
            scope_actions=["read"],
        )
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.assert_awaited_once()
        session.add.assert_not_called()

    async def test_record_use_creates_correct_event_shape(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)

        await pipeline.record_use(
            token_id="tok-1", agent_id="sub", parent_agent_id="parent",
        )
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.assert_awaited_once()

    async def test_record_renew_calls_session(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)

        await pipeline.record_renew(
            token_id="tok-1", agent_id="sub", parent_agent_id="parent",
        )
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.assert_awaited_once()

    async def test_record_revoke_calls_session(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)

        await pipeline.record_revoke(
            token_id="tok-1", agent_id="sub", parent_agent_id="parent",
        )
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.assert_awaited_once()

    async def test_record_revive_calls_session(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)

        await pipeline.record_revive(
            token_id="tok-1", agent_id="sub", parent_agent_id="parent",
        )
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.assert_awaited_once()


@pytest.mark.asyncio
class TestFlushOnTick:
    async def test_flush_empty_pending_returns_zero(self):
        pipeline = StsAuditPipeline(MagicMock())
        assert await pipeline.flush_on_tick() == 0

    async def test_flush_with_events_commits(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)
        pipeline._pending_events = [
            {"token_id": "tok-1", "action": "mint", "agent_id": "a1"},
        ]
        row = MagicMock()
        row.use_count = 0
        row.events = "[]"
        row.last_used_at = None
        row_raw = MagicMock()
        row_raw.scalar_one_or_none = MagicMock(return_value=row)
        session = sf.return_value.__aenter__.return_value
        session.execute.return_value = row_raw

        result = await pipeline.flush_on_tick()
        assert result == 1
        assert pipeline._pending_events == []
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
class TestAppendEvent:
    async def test_append_event_no_matching_row_noop(self):
        sf = _session_factory()
        pipeline = StsAuditPipeline(sf)
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session = sf.begin.return_value.__aenter__.return_value
        session.execute.return_value = result

        await pipeline._append_event("tok-nonexistent", {"action": "mint"})
        session.execute.assert_awaited_once()
        session.add.assert_not_called()
