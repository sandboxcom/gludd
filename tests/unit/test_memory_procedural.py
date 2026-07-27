"""Unit tests for ProceduralMemoryStore — procedural memory (how-to knowledge)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.memory.procedural import (
    PROCEDURAL_NAMESPACE,
    ProceduralMemoryStore,
    Procedure,
    _dict_to_procedure,
    _procedure_to_dict,
)


class TestProcedureDataclass:
    def test_default_construction(self):
        proc = Procedure()
        assert proc.id
        assert proc.name == ""
        assert proc.steps == []
        assert proc.success_count == 0
        assert proc.failure_count == 0
        assert proc.tags == []

    def test_custom_fields(self):
        proc = Procedure(
            name="deploy_k8s",
            description="Deploy to Kubernetes",
            trigger="k8s deploy",
            steps=[{"tool": "kubectl", "task_type": "deploy"}],
            expected_outcome="success",
            tool_chain=["kubectl", "helm"],
            success_count=5,
            failure_count=1,
            tags=["k8s", "deploy"],
        )
        assert proc.name == "deploy_k8s"
        assert len(proc.steps) == 1
        assert proc.tool_chain == ["kubectl", "helm"]
        assert proc.tags == ["k8s", "deploy"]

    def test_success_rate_all_success(self):
        proc = Procedure(success_count=10, failure_count=0)
        assert proc.success_rate == 1.0

    def test_success_rate_all_failure(self):
        proc = Procedure(success_count=0, failure_count=5)
        assert proc.success_rate == 0.0

    def test_success_rate_mixed(self):
        proc = Procedure(success_count=7, failure_count=3)
        assert proc.success_rate == 0.7

    def test_success_rate_no_attempts(self):
        proc = Procedure(success_count=0, failure_count=0)
        assert proc.success_rate == 0.0

    def test_created_at_is_iso(self):
        proc = Procedure()
        assert "T" in proc.created_at

    def test_id_is_unique(self):
        p1 = Procedure()
        p2 = Procedure()
        assert p1.id != p2.id


class TestSerialization:
    def test_round_trip(self):
        proc = Procedure(name="test", trigger="trigger", steps=[{"a": "b"}])
        d = _procedure_to_dict(proc)
        restored = _dict_to_procedure(d)
        assert restored.name == "test"
        assert restored.trigger == "trigger"
        assert restored.steps == [{"a": "b"}]

    def test_dict_to_procedure_defaults(self):
        restored = _dict_to_procedure({})
        assert restored.id == ""
        assert restored.name == ""

    def test_json_serializable(self):
        proc = Procedure(name="json_test")
        d = _procedure_to_dict(proc)
        json.dumps(d)


class TestProceduralMemoryStore:
    def make_repo(self):
        repo = MagicMock()
        repo.set = AsyncMock()
        repo.get = AsyncMock()
        repo.list_by_namespace = AsyncMock(return_value=[])
        return repo

    @pytest.mark.asyncio
    async def test_store_procedure(self):
        repo = self.make_repo()
        store = ProceduralMemoryStore(repo)
        proc = Procedure(name="test")
        pid = await store.store_procedure(proc)
        assert pid == proc.id
        repo.set.assert_awaited_once()
        call_args = repo.set.call_args
        assert call_args.kwargs["agent_id"] == "system"
        assert call_args.kwargs["namespace"] == PROCEDURAL_NAMESPACE

    @pytest.mark.asyncio
    async def test_get_procedure_found(self):
        repo = self.make_repo()
        proc = Procedure(name="test", trigger="deploy")
        row = MagicMock()
        row.value = json.dumps(_procedure_to_dict(proc))
        repo.get = AsyncMock(return_value=row)

        store = ProceduralMemoryStore(repo)
        result = await store.get_procedure(proc.id)
        assert result is not None
        assert result.name == "test"
        assert result.trigger == "deploy"

    @pytest.mark.asyncio
    async def test_get_procedure_not_found(self):
        repo = self.make_repo()
        repo.get = AsyncMock(return_value=None)
        store = ProceduralMemoryStore(repo)
        result = await store.get_procedure("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_procedures(self):
        repo = self.make_repo()
        p1 = Procedure(name="proc1")
        p2 = Procedure(name="proc2")
        rows = []
        for p in [p1, p2]:
            r = MagicMock()
            r.value = json.dumps(_procedure_to_dict(p))
            rows.append(r)
        repo.list_by_namespace = AsyncMock(return_value=rows)

        store = ProceduralMemoryStore(repo)
        results = await store.list_procedures()
        assert len(results) == 2
        assert results[0].name == "proc1"

    @pytest.mark.asyncio
    async def test_list_procedures_handles_invalid_json(self):
        repo = self.make_repo()
        r1 = MagicMock()
        r1.value = '{"name": "valid"}'
        r2 = MagicMock()
        r2.value = "not json"
        repo.list_by_namespace = AsyncMock(return_value=[r1, r2])

        store = ProceduralMemoryStore(repo)
        results = await store.list_procedures()
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_find_by_trigger_exact_match(self):
        repo = self.make_repo()
        proc = Procedure(name="deploy", trigger="k8s_deploy", success_count=10)
        r = MagicMock()
        r.value = json.dumps(_procedure_to_dict(proc))
        repo.list_by_namespace = AsyncMock(return_value=[r])

        store = ProceduralMemoryStore(repo)
        results = await store.find_by_trigger("k8s_deploy")
        assert len(results) == 1
        assert results[0].name == "deploy"

    @pytest.mark.asyncio
    async def test_find_by_trigger_partial_match(self):
        repo = self.make_repo()
        proc = Procedure(
            name="deploy",
            trigger="kubernetes deploy production",
            description="Handle k8s deployments",
            tags=["k8s", "infra"],
            success_count=5,
        )
        r = MagicMock()
        r.value = json.dumps(_procedure_to_dict(proc))
        repo.list_by_namespace = AsyncMock(return_value=[r])

        store = ProceduralMemoryStore(repo)
        results = await store.find_by_trigger("k8s")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_find_by_trigger_no_match(self):
        repo = self.make_repo()
        proc = Procedure(name="deploy", trigger="docker_build")
        r = MagicMock()
        r.value = json.dumps(_procedure_to_dict(proc))
        repo.list_by_namespace = AsyncMock(return_value=[r])

        store = ProceduralMemoryStore(repo)
        results = await store.find_by_trigger("nonexistent")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_record_success_increments(self):
        repo = self.make_repo()
        proc = Procedure(name="test", success_count=3, failure_count=1)
        row = MagicMock()
        row.value = json.dumps(_procedure_to_dict(proc))
        repo.get = AsyncMock(return_value=row)
        repo.set = AsyncMock()

        store = ProceduralMemoryStore(repo)
        await store.record_success(proc.id)
        repo.set.assert_awaited()
        call_val = json.loads(repo.set.call_args.kwargs["value"])
        assert call_val["success_count"] == 4

    @pytest.mark.asyncio
    async def test_record_failure_increments(self):
        repo = self.make_repo()
        proc = Procedure(name="test", success_count=3, failure_count=1)
        row = MagicMock()
        row.value = json.dumps(_procedure_to_dict(proc))
        repo.get = AsyncMock(return_value=row)
        repo.set = AsyncMock()

        store = ProceduralMemoryStore(repo)
        await store.record_failure(proc.id)
        repo.set.assert_awaited()
        call_val = json.loads(repo.set.call_args.kwargs["value"])
        assert call_val["failure_count"] == 2

    @pytest.mark.asyncio
    async def test_record_success_missing_procedure_noop(self):
        repo = self.make_repo()
        repo.get = AsyncMock(return_value=None)
        store = ProceduralMemoryStore(repo)
        await store.record_success("nonexistent")
        repo.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consolidate_from_episodes(self):
        repo = self.make_repo()
        repo.set = AsyncMock()
        store = ProceduralMemoryStore(repo)

        ep1 = MagicMock()
        ep1.task_type = "deploy"
        ep1.outcome = "success"
        ep1.takeaway = "use helm for k8s"
        ep1.tools_used = ["kubectl"]
        ep1.context = {}

        ep2 = MagicMock()
        ep2.task_type = "deploy"
        ep2.outcome = "success"
        ep2.takeaway = "check node status first"
        ep2.tools_used = ["helm"]
        ep2.context = {}

        recorder = MagicMock()
        recorder.list_episodes = AsyncMock(return_value=[ep1, ep2])

        count = await store.consolidate_from_episodes(recorder, "agent-1")
        assert count == 1
        repo.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consolidate_below_min_skips(self):
        repo = self.make_repo()
        store = ProceduralMemoryStore(repo)

        ep1 = MagicMock()
        ep1.task_type = "deploy"
        ep1.outcome = "success"
        ep1.takeaway = "tip"
        ep1.tools_used = []
        ep1.context = {}

        recorder = MagicMock()
        recorder.list_episodes = AsyncMock(return_value=[ep1])

        count = await store.consolidate_from_episodes(
            recorder,
            "agent-1",
            min_success_count=2,
        )
        assert count == 0
