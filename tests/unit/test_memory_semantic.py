"""Unit tests for SemanticMemoryStore — semantic memory (facts and concepts)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.memory.semantic import (
    SEMANTIC_NAMESPACE,
    Fact,
    SemanticMemoryStore,
    _dict_to_fact,
    _fact_to_dict,
)


class TestFactDataclass:
    def test_default_construction(self):
        fact = Fact()
        assert fact.id
        assert fact.key == ""
        assert fact.value == ""
        assert fact.confidence == 1.0
        assert fact.tags == []

    def test_custom_fields(self):
        fact = Fact(
            key="api_endpoint",
            value="POST /api/v1/tasks",
            confidence=0.9,
            source="code_analysis",
            category="api_contract",
            tags=["api", "tasks"],
        )
        assert fact.key == "api_endpoint"
        assert fact.value == "POST /api/v1/tasks"
        assert fact.confidence == 0.9
        assert fact.tags == ["api", "tasks"]

    def test_id_is_unique(self):
        f1 = Fact()
        f2 = Fact()
        assert f1.id != f2.id

    def test_created_at_is_iso(self):
        fact = Fact()
        assert "T" in fact.created_at

    def test_updated_at_default_empty(self):
        fact = Fact()
        assert fact.updated_at == ""

    def test_access_count_default_zero(self):
        fact = Fact()
        assert fact.access_count == 0


class TestSerialization:
    def test_round_trip(self):
        fact = Fact(key="test_key", value="test_value", confidence=0.8)
        d = _fact_to_dict(fact)
        restored = _dict_to_fact(d)
        assert restored.key == "test_key"
        assert restored.value == "test_value"
        assert restored.confidence == 0.8

    def test_dict_to_fact_defaults(self):
        restored = _dict_to_fact({})
        assert restored.key == ""
        assert restored.confidence == 1.0

    def test_json_serializable(self):
        fact = Fact(key="k", value="v")
        d = _fact_to_dict(fact)
        json.dumps(d)


class TestSemanticMemoryStore:
    def make_repo(self):
        repo = MagicMock()
        repo.set = AsyncMock()
        repo.get = AsyncMock()
        repo.delete = AsyncMock()
        repo.list_by_namespace = AsyncMock(return_value=[])
        return repo

    @pytest.mark.asyncio
    async def test_upsert_fact_new(self):
        repo = self.make_repo()
        store = SemanticMemoryStore(repo)
        fact = Fact(key="new_key", value="new_value")
        fid = await store.upsert_fact(fact)
        assert fid == fact.id
        repo.set.assert_awaited_once()
        call_args = repo.set.call_args
        assert call_args.kwargs["agent_id"] == "system"
        assert call_args.kwargs["namespace"] == SEMANTIC_NAMESPACE

    @pytest.mark.asyncio
    async def test_upsert_fact_existing_updates(self):
        repo = self.make_repo()
        existing = Fact(
            id="existing-id",
            key="my_key",
            value="old",
            confidence=0.5,
            access_count=3,
        )
        r = MagicMock()
        r.value = json.dumps(_fact_to_dict(existing))
        repo.list_by_namespace = AsyncMock(return_value=[r])
        repo.set = AsyncMock()

        store = SemanticMemoryStore(repo)
        new_fact = Fact(key="my_key", value="new_value", confidence=0.9)
        fid = await store.upsert_fact(new_fact)
        assert fid == "existing-id"
        call_val = json.loads(repo.set.call_args.kwargs["value"])
        assert call_val["value"] == "new_value"
        assert call_val["confidence"] == 0.9
        assert call_val["access_count"] == 3

    @pytest.mark.asyncio
    async def test_get_fact_found(self):
        repo = self.make_repo()
        fact = Fact(key="key1", value="val1")
        row = MagicMock()
        row.value = json.dumps(_fact_to_dict(fact))
        repo.get = AsyncMock(return_value=row)

        store = SemanticMemoryStore(repo)
        result = await store.get_fact(fact.id)
        assert result is not None
        assert result.key == "key1"
        assert result.access_count == 1

    @pytest.mark.asyncio
    async def test_get_fact_not_found(self):
        repo = self.make_repo()
        repo.get = AsyncMock(return_value=None)
        store = SemanticMemoryStore(repo)
        result = await store.get_fact("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_fact_by_key_found(self):
        repo = self.make_repo()
        fact = Fact(key="target", value="found")
        r = MagicMock()
        r.value = json.dumps(_fact_to_dict(fact))
        repo.list_by_namespace = AsyncMock(return_value=[r])

        store = SemanticMemoryStore(repo)
        result = await store.get_fact_by_key("target")
        assert result is not None
        assert result.value == "found"

    @pytest.mark.asyncio
    async def test_get_fact_by_key_not_found(self):
        repo = self.make_repo()
        repo.list_by_namespace = AsyncMock(return_value=[])

        store = SemanticMemoryStore(repo)
        result = await store.get_fact_by_key("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_facts(self):
        repo = self.make_repo()
        f1 = Fact(key="k1", value="v1")
        f2 = Fact(key="k2", value="v2", category="cat")
        rows = []
        for f in [f1, f2]:
            r = MagicMock()
            r.value = json.dumps(_fact_to_dict(f))
            rows.append(r)
        repo.list_by_namespace = AsyncMock(return_value=rows)

        store = SemanticMemoryStore(repo)
        results = await store.list_facts()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_facts_filter_by_category(self):
        repo = self.make_repo()
        f1 = Fact(key="k1", category="cat_a")
        f2 = Fact(key="k2", category="cat_b")
        rows = []
        for f in [f1, f2]:
            r = MagicMock()
            r.value = json.dumps(_fact_to_dict(f))
            rows.append(r)
        repo.list_by_namespace = AsyncMock(return_value=rows)

        store = SemanticMemoryStore(repo)
        results = await store.list_facts(category="cat_a")
        assert len(results) == 1
        assert results[0].key == "k1"

    @pytest.mark.asyncio
    async def test_list_facts_filter_by_confidence(self):
        repo = self.make_repo()
        f1 = Fact(key="k1", confidence=0.3)
        f2 = Fact(key="k2", confidence=0.9)
        rows = []
        for f in [f1, f2]:
            r = MagicMock()
            r.value = json.dumps(_fact_to_dict(f))
            rows.append(r)
        repo.list_by_namespace = AsyncMock(return_value=rows)

        store = SemanticMemoryStore(repo)
        results = await store.list_facts(min_confidence=0.5)
        assert len(results) == 1
        assert results[0].key == "k2"

    @pytest.mark.asyncio
    async def test_search_facts_matches(self):
        repo = self.make_repo()
        f1 = Fact(key="api_endpoint", value="POST /tasks", category="api")
        f2 = Fact(key="config_flag", value="debug_mode", category="config")
        rows = []
        for f in [f1, f2]:
            r = MagicMock()
            r.value = json.dumps(_fact_to_dict(f))
            rows.append(r)
        repo.list_by_namespace = AsyncMock(return_value=rows)

        store = SemanticMemoryStore(repo)
        results = await store.search_facts("api tasks")
        assert len(results) >= 1
        assert results[0][0].key == "api_endpoint"

    @pytest.mark.asyncio
    async def test_search_facts_no_match(self):
        repo = self.make_repo()
        f1 = Fact(key="k1", value="v1")
        r = MagicMock()
        r.value = json.dumps(_fact_to_dict(f1))
        repo.list_by_namespace = AsyncMock(return_value=[r])

        store = SemanticMemoryStore(repo)
        results = await store.search_facts("zzz_nonexistent_term_zzz")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_delete_fact_found(self):
        repo = self.make_repo()
        row = MagicMock()
        row.value = json.dumps(_fact_to_dict(Fact(key="k1")))
        repo.get = AsyncMock(return_value=row)
        repo.delete = AsyncMock()

        store = SemanticMemoryStore(repo)
        result = await store.delete_fact("some_id")
        assert result is True
        repo.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_fact_not_found(self):
        repo = self.make_repo()
        repo.get = AsyncMock(return_value=None)

        store = SemanticMemoryStore(repo)
        result = await store.delete_fact("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_consolidate_from_consolidated(self):
        repo = self.make_repo()
        repo.set = AsyncMock()

        consolidator = MagicMock()
        consolidator.get_consolidated = AsyncMock(
            return_value=[
                {
                    "task_type": "deploy",
                    "episode_count": 10,
                    "outcomes": {"success": 8, "failure": 2},
                    "error_patterns": ["timeout"],
                    "key_takeaways": ["use retry"],
                }
            ]
        )

        store = SemanticMemoryStore(repo)
        count = await store.consolidate_from_consolidated(
            consolidator,
            "agent-1",
        )
        assert count == 1
        repo.set.assert_awaited_once()
        call_val = json.loads(repo.set.call_args.kwargs["value"])
        assert call_val["key"] == "task_pattern:deploy"
        assert call_val["category"] == "task_pattern"

    @pytest.mark.asyncio
    async def test_consolidate_skips_empty_task_type(self):
        repo = self.make_repo()
        consolidator = MagicMock()
        consolidator.get_consolidated = AsyncMock(
            return_value=[
                {"task_type": "", "episode_count": 1},
            ]
        )

        store = SemanticMemoryStore(repo)
        count = await store.consolidate_from_consolidated(
            consolidator,
            "agent-1",
        )
        assert count == 0
