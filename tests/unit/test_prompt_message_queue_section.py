"""Unit tests for PART 4: prompt integration of the message-queue + facts
availability section (general_ludd.prompts.registry.render_message_queue_section).

The section must:
  - appear (with the unread count + senders + module names) when enabled
  - be empty (prompts unchanged) when the feature is off
"""

from __future__ import annotations

import pytest

from general_ludd.prompts.registry import render_message_queue_section


class TestRenderMessageQueueSection:
    def test_enabled_with_unread_messages_mentions_availability(self):
        text = render_message_queue_section(
            role="coder", unread_count=3, senders=["planner", "reviewer"], enabled=True
        )
        assert "you are agent 'coder'" in text
        assert "3 unread message" in text
        assert "from planner, reviewer" in text
        assert "gludd_message(receive)" in text
        assert "gludd_facts" in text

    def test_singular_message_phrasing(self):
        text = render_message_queue_section(role="coder", unread_count=1, enabled=True)
        assert "1 unread message" in text
        # No trailing "from" clause when no senders given.
        assert "from" not in text.split("gludd_message")[0].split("message")[1] or "from " not in text

    def test_zero_unread_still_announces_availability(self):
        text = render_message_queue_section(role="reviewer", unread_count=0, enabled=True)
        assert "0 unread message" in text
        assert "gludd_facts" in text

    def test_disabled_returns_empty_string(self):
        text = render_message_queue_section(
            role="coder", unread_count=5, senders=["planner"], enabled=False
        )
        assert text == ""

    def test_disabled_is_default(self):
        # Without enabled=True the section is suppressed (prompts unchanged).
        assert render_message_queue_section(role="coder", unread_count=2) == ""

    def test_senders_deduped_and_sorted(self):
        text = render_message_queue_section(
            role="coder", unread_count=2, senders=["b", "a", "b"], enabled=True
        )
        assert "from a, b" in text


class _FakeTodo:
    def __init__(self, assigned_agent=None, work_type="code"):
        self.assigned_agent = assigned_agent
        self.work_type = work_type


class TestEventLoopMessageQueueWiring:
    """PART 4 dispatch-path wiring: EventLoop._append_message_queue_section."""

    def _make_loop(self, config):
        from general_ludd.event_loop.loop import EventLoop

        return EventLoop(config=config)

    @pytest.mark.asyncio
    async def test_disabled_leaves_prompt_unchanged(self):
        loop = self._make_loop(config={})  # flag off by default
        out = await loop._append_message_queue_section(
            "ORIGINAL PROMPT", _FakeTodo(assigned_agent="coder"), None
        )
        assert out == "ORIGINAL PROMPT"

    @pytest.mark.asyncio
    async def test_enabled_appends_section_no_db(self):
        loop = self._make_loop(config={"message_queue_prompt": True})
        out = await loop._append_message_queue_section(
            "ORIGINAL PROMPT", _FakeTodo(assigned_agent="coder"), None
        )
        assert "ORIGINAL PROMPT" in out
        assert "you are agent 'coder'" in out
        assert "gludd_facts" in out

    @pytest.mark.asyncio
    async def test_enabled_counts_unread_from_db(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from general_ludd.db.models import Base
        from general_ludd.db.repository import AgentMessageRepository

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            repo = AgentMessageRepository(session)
            await repo.send({"sender": "planner", "recipient": "coder", "topic": "t"})
            await session.commit()

        loop = self._make_loop(config={"message_queue_prompt": True})
        loop._session_factory = factory
        out = await loop._append_message_queue_section(
            "P", _FakeTodo(assigned_agent="coder"), None
        )
        assert "1 unread message" in out
        assert "from planner" in out
        await engine.dispose()
