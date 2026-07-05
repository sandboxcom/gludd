"""Unit tests for G1 persistent-agent-memory wiring into EventLoop dispatch.

Proves that agent memories stored via MemoryRepository are injected into
system prompts during dispatch via EventLoop._build_memory_section.
"""

from __future__ import annotations

import pytest

from general_ludd.event_loop.loop import EventLoop


class _FakeTodo:
    def __init__(self, assigned_agent=None, work_type="code"):
        self.assigned_agent = assigned_agent
        self.work_type = work_type


class _FakeMemoryRecord:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeMemoryRepo:
    def __init__(self, records=None, should_fail=False):
        self.records = records or []
        self.should_fail = should_fail
        self.last_call = None

    async def list_by_namespace(self, agent_id, namespace="default", limit=50):
        self.last_call = (agent_id, namespace, limit)
        if self.should_fail:
            raise RuntimeError("simulated failure")
        return [r for r in self.records]


class TestG1MemoryWiring:
    """G1: persistent agent memories injected into dispatch prompts."""

    def _make_loop(self, memory_repo=None):
        return EventLoop(memory_repo=memory_repo)

    @pytest.mark.asyncio
    async def test_no_memory_repo_leaves_prompt_unchanged(self):
        loop = self._make_loop(memory_repo=None)
        out = await loop._build_memory_section(
            "ORIGINAL PROMPT", _FakeTodo()
        )
        assert out == "ORIGINAL PROMPT"

    @pytest.mark.asyncio
    async def test_empty_records_leaves_prompt_unchanged(self):
        repo = _FakeMemoryRepo(records=[])
        loop = self._make_loop(memory_repo=repo)
        out = await loop._build_memory_section(
            "ORIGINAL PROMPT", _FakeTodo()
        )
        assert out == "ORIGINAL PROMPT"

    @pytest.mark.asyncio
    async def test_records_injected_into_prompt(self):
        records = [
            _FakeMemoryRecord("setup_complete", "True"),
            _FakeMemoryRecord("last_branch", "feature/foo"),
        ]
        repo = _FakeMemoryRepo(records=records)
        loop = self._make_loop(memory_repo=repo)
        out = await loop._build_memory_section(
            "ORIGINAL PROMPT", _FakeTodo(assigned_agent="coder")
        )
        assert "ORIGINAL PROMPT" in out
        assert "## Agent Memory" in out
        assert "- **setup_complete**: True" in out
        assert "- **last_branch**: feature/foo" in out

    @pytest.mark.asyncio
    async def test_assigned_agent_used_as_agent_id(self):
        repo = _FakeMemoryRepo(
            records=[_FakeMemoryRecord("k", "v")]
        )
        loop = self._make_loop(memory_repo=repo)
        await loop._build_memory_section(
            "P", _FakeTodo(assigned_agent="planner")
        )
        assert repo.last_call[0] == "planner"

    @pytest.mark.asyncio
    async def test_work_type_fallback_when_no_assigned_agent(self):
        repo = _FakeMemoryRepo(
            records=[_FakeMemoryRecord("k", "v")]
        )
        loop = self._make_loop(memory_repo=repo)
        await loop._build_memory_section(
            "P", _FakeTodo(assigned_agent=None, work_type="review")
        )
        assert repo.last_call[0] == "review"

    @pytest.mark.asyncio
    async def test_null_prompt_with_records_returns_standalone_section(self):
        repo = _FakeMemoryRepo(
            records=[_FakeMemoryRecord("k", "v")]
        )
        loop = self._make_loop(memory_repo=repo)
        out = await loop._build_memory_section(
            None, _FakeTodo()
        )
        assert out == "## Agent Memory\n- **k**: v"

    @pytest.mark.asyncio
    async def test_repo_failure_returns_prompt_unchanged(self):
        repo = _FakeMemoryRepo(should_fail=True)
        loop = self._make_loop(memory_repo=repo)
        out = await loop._build_memory_section(
            "ORIGINAL", _FakeTodo()
        )
        assert out == "ORIGINAL"
