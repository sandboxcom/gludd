"""Structural tests for memory/episodic.py — Episode, EpisodicMemoryRecorder."""

from __future__ import annotations

import sys

import pytest

from general_ludd.memory.episodic import (
    EPISODIC_NAMESPACE,
    Episode,
    EpisodicMemoryRecorder,
    _dict_to_episode,
    _episode_to_dict,
)


class TestModuleExports:
    def test_module_importable(self) -> None:
        assert "general_ludd.memory.episodic" in sys.modules

    def test_episodic_namespace_constant(self) -> None:
        assert EPISODIC_NAMESPACE == "episodic"
        assert isinstance(EPISODIC_NAMESPACE, str)

    def test_episode_class_exists(self) -> None:
        assert Episode is not None
        assert callable(Episode)

    def test_episodic_memory_recorder_class_exists(self) -> None:
        assert EpisodicMemoryRecorder is not None
        assert callable(EpisodicMemoryRecorder)

    def test_private_helpers_exported(self) -> None:
        assert callable(_episode_to_dict)
        assert callable(_dict_to_episode)


class TestEpisodeDefaults:
    def test_construction_with_no_args(self) -> None:
        ep = Episode()
        assert isinstance(ep.id, str)
        assert len(ep.id) == 12
        assert ep.agent_id == ""
        assert ep.task_type == ""
        assert ep.work_type == ""
        assert ep.priority == "medium"
        assert ep.outcome == "unknown"
        assert ep.context == {}
        assert ep.tools_used == []
        assert ep.takeaway == ""
        assert ep.error_message == ""
        assert ep.duration_seconds == 0.0
        assert isinstance(ep.created_at, str)

    def test_construction_with_fields(self) -> None:
        ep = Episode(
            agent_id="agent-1",
            task_type="code_review",
            work_type="review",
            priority="high",
            outcome="success",
            takeaway="Found 3 bugs",
            duration_seconds=42.5,
        )
        assert ep.agent_id == "agent-1"
        assert ep.task_type == "code_review"
        assert ep.work_type == "review"
        assert ep.priority == "high"
        assert ep.outcome == "success"
        assert ep.takeaway == "Found 3 bugs"
        assert ep.duration_seconds == 42.5

    def test_id_is_unique_per_instance(self) -> None:
        e1 = Episode()
        e2 = Episode()
        assert e1.id != e2.id

    def test_id_is_12_char_hex(self) -> None:
        ep = Episode()
        assert len(ep.id) == 12
        assert all(c in "0123456789abcdef" for c in ep.id)

    def test_context_default_is_empty_dict(self) -> None:
        ep = Episode()
        assert ep.context == {}
        assert isinstance(ep.context, dict)

    def test_tools_used_default_is_empty_list(self) -> None:
        ep = Episode()
        assert ep.tools_used == []
        assert isinstance(ep.tools_used, list)

    def test_context_and_tools_used_mutation_safe(self) -> None:
        e1 = Episode()
        e2 = Episode()
        e1.context["key"] = "value"
        e1.tools_used.append("tool_x")
        assert e2.context == {}
        assert e2.tools_used == []

    def test_created_at_is_iso_format(self) -> None:
        ep = Episode()
        assert "T" in ep.created_at


class TestSerializationRoundtrip:
    def test_dict_to_episode_and_back(self) -> None:
        original = Episode(
            agent_id="a1",
            task_type="test",
            work_type="code",
            priority="high",
            outcome="success",
            context={"file": "foo.py"},
            tools_used=["read", "write"],
            takeaway="All good",
            error_message="",
            duration_seconds=15.0,
        )
        d = _episode_to_dict(original)
        restored = _dict_to_episode(d)

        assert restored.id == original.id
        assert restored.agent_id == original.agent_id
        assert restored.task_type == original.task_type
        assert restored.work_type == original.work_type
        assert restored.priority == original.priority
        assert restored.outcome == original.outcome
        assert restored.context == original.context
        assert restored.tools_used == original.tools_used
        assert restored.takeaway == original.takeaway
        assert restored.error_message == original.error_message
        assert restored.duration_seconds == original.duration_seconds
        assert restored.created_at == original.created_at

    def test_episode_to_dict_keys(self) -> None:
        ep = Episode()
        d = _episode_to_dict(ep)
        expected_keys = {
            "id", "agent_id", "task_type", "work_type", "priority",
            "outcome", "context", "tools_used", "takeaway", "error_message",
            "duration_seconds", "created_at",
        }
        assert set(d.keys()) == expected_keys

    def test_dict_to_episode_missing_keys_fallback(self) -> None:
        ep = _dict_to_episode({})
        assert ep.agent_id == ""
        assert ep.task_type == ""
        assert ep.priority == "medium"
        assert ep.outcome == "unknown"
        assert ep.context == {}
        assert ep.tools_used == []
        assert ep.takeaway == ""
        assert ep.error_message == ""
        assert ep.duration_seconds == 0.0
        assert ep.created_at == ""

    def test_dict_to_episode_partial_keys(self) -> None:
        ep = _dict_to_episode({
            "id": "abc123",
            "agent_id": "a1",
            "outcome": "failure",
            "duration_seconds": 60,
        })
        assert ep.id == "abc123"
        assert ep.agent_id == "a1"
        assert ep.outcome == "failure"
        assert ep.duration_seconds == 60.0
        assert ep.task_type == ""
        assert ep.priority == "medium"

    def test_dict_to_episode_duration_seconds_casts_to_float(self) -> None:
        ep = _dict_to_episode({"duration_seconds": 42})
        assert ep.duration_seconds == 42.0
        assert isinstance(ep.duration_seconds, float)

        ep2 = _dict_to_episode({"duration_seconds": "12.5"})
        assert ep2.duration_seconds == 12.5

    def test_episode_to_dict_preserves_context_and_tools(self) -> None:
        ep = Episode(
            agent_id="a1",
            context={"branch": "main", "files": 3},
            tools_used=["grep", "glob", "read"],
        )
        d = _episode_to_dict(ep)
        assert d["context"] == {"branch": "main", "files": 3}
        assert d["tools_used"] == ["grep", "glob", "read"]


class TestEpisodicMemoryRecorder:
    class MockRepo:
        def __init__(self) -> None:
            self.store: dict[str, object] = {}
            self.get_calls: list[dict[str, object]] = []
            self.set_calls: list[dict[str, object]] = []
            self.list_calls: list[dict[str, object]] = []

        async def get(self, agent_id: str, key: str, namespace: str = "", project_id: str | None = None) -> object:
            self.get_calls.append({
                "agent_id": agent_id, "key": key,
                "namespace": namespace, "project_id": project_id,
            })
            return self.store.get(f"{agent_id}:{namespace}:{project_id}:{key}")

        async def set(self, agent_id: str, key: str, value: str, namespace: str = "", project_id: str | None = None) -> None:
            self.set_calls.append({
                "agent_id": agent_id, "key": key, "value": value,
                "namespace": namespace, "project_id": project_id,
            })
            self.store[f"{agent_id}:{namespace}:{project_id}:{key}"] = value

        async def list_by_namespace(self, agent_id: str, namespace: str = "", project_id: str | None = None, limit: int = 100) -> list[object]:
            self.list_calls.append({
                "agent_id": agent_id, "namespace": namespace,
                "project_id": project_id, "limit": limit,
            })
            # Return mock rows matching the stored namespace prefix
            prefix = f"{agent_id}:{namespace}:{project_id}:"
            results = []
            for k, v in self.store.items():
                if k.startswith(prefix):
                    results.append(MockRow(k, str(v)))
            return results[:limit]

    class MockRow:
        def __init__(self, key: str, value: str) -> None:
            self.key = key
            self.value = value

    @pytest.fixture
    def repo(self) -> MockRepo:
        return self.MockRepo()

    @pytest.fixture
    def recorder(self, repo: MockRepo) -> EpisodicMemoryRecorder:
        return EpisodicMemoryRecorder(repo)

    def test_construction_stores_repo_and_namespace(self, recorder: EpisodicMemoryRecorder) -> None:
        assert recorder._repo is not None
        assert recorder._namespace == "episodic"

    def test_namespace_matches_constant(self, recorder: EpisodicMemoryRecorder) -> None:
        assert recorder._namespace == EPISODIC_NAMESPACE

    @pytest.mark.asyncio
    async def test_record_episode(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="review", outcome="success")
        eid = await recorder.record_episode(ep)
        assert eid == ep.id
        assert len(repo.set_calls) == 1
        assert repo.set_calls[0]["namespace"] == EPISODIC_NAMESPACE
        assert repo.set_calls[0]["agent_id"] == "agent-1"
        assert repo.set_calls[0]["key"] == ep.id

    @pytest.mark.asyncio
    async def test_record_episode_with_project_id(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="review")
        await recorder.record_episode(ep, project_id="proj-1")
        assert repo.set_calls[0]["project_id"] == "proj-1"

    @pytest.mark.asyncio
    async def test_get_episode_found(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="review", outcome="success")
        import json
        repo.store["agent-1:episodic:None:ep1"] = json.dumps(_episode_to_dict(ep), default=str)
        result = await recorder.get_episode("agent-1", "ep1")
        assert result is not None
        assert result.task_type == "review"

    @pytest.mark.asyncio
    async def test_get_episode_not_found(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        result = await recorder.get_episode("agent-1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_episodes(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep1 = Episode(agent_id="agent-1", task_type="review", outcome="success")
        ep2 = Episode(agent_id="agent-1", task_type="deploy", outcome="failure")
        import json
        repo.store["agent-1:episodic:proj-a:a"] = json.dumps(_episode_to_dict(ep1), default=str)
        repo.store["agent-1:episodic:proj-a:b"] = json.dumps(_episode_to_dict(ep2), default=str)
        results = await recorder.list_episodes("agent-1", project_id="proj-a")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_episodes_filters_by_task_type(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep1 = Episode(agent_id="agent-1", task_type="review", outcome="success")
        ep2 = Episode(agent_id="agent-1", task_type="deploy", outcome="success")
        import json
        repo.store["agent-1:episodic:proj-a:a"] = json.dumps(_episode_to_dict(ep1), default=str)
        repo.store["agent-1:episodic:proj-a:b"] = json.dumps(_episode_to_dict(ep2), default=str)
        results = await recorder.list_episodes("agent-1", task_type="review", project_id="proj-a")
        assert len(results) == 1
        assert results[0].task_type == "review"

    @pytest.mark.asyncio
    async def test_list_episodes_filters_by_outcome(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep1 = Episode(agent_id="agent-1", task_type="task", outcome="success")
        ep2 = Episode(agent_id="agent-1", task_type="task", outcome="failure")
        import json
        repo.store["agent-1:episodic:proj-a:a"] = json.dumps(_episode_to_dict(ep1), default=str)
        repo.store["agent-1:episodic:proj-a:b"] = json.dumps(_episode_to_dict(ep2), default=str)
        results = await recorder.list_episodes("agent-1", outcome="failure", project_id="proj-a")
        assert len(results) == 1
        assert results[0].outcome == "failure"

    @pytest.mark.asyncio
    async def test_list_episodes_skips_malformed_data(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="good")
        import json
        repo.store["agent-1:episodic:proj-a:valid"] = json.dumps(_episode_to_dict(ep), default=str)
        repo.store["agent-1:episodic:proj-a:bad"] = "not-json"
        results = await recorder.list_episodes("agent-1", project_id="proj-a")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_list_by_outcome_delegates_to_list_episodes(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="task", outcome="success")
        import json
        repo.store["agent-1:episodic:proj-a:a"] = json.dumps(_episode_to_dict(ep), default=str)
        results = await recorder.list_by_outcome("agent-1", "success", project_id="proj-a")
        assert len(results) == 1
        assert results[0].outcome == "success"

    @pytest.mark.asyncio
    async def test_list_by_outcome_empty_when_no_match(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        ep = Episode(agent_id="agent-1", task_type="task", outcome="success")
        import json
        repo.store["agent-1:episodic:proj-a:a"] = json.dumps(_episode_to_dict(ep), default=str)
        results = await recorder.list_by_outcome("agent-1", "failure", project_id="proj-a")
        assert results == []

    @pytest.mark.asyncio
    async def test_record_completion_creates_and_stores_episode(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        eid = await recorder.record_completion(
            agent_id="agent-1",
            task_type="code_review",
            work_type="review",
            priority="high",
            outcome="success",
            context={"pr": 42},
            takeaway="LGTM",
            duration_seconds=30.0,
            project_id="proj-1",
        )
        assert len(repo.set_calls) == 1
        assert repo.set_calls[0]["agent_id"] == "agent-1"
        assert repo.set_calls[0]["project_id"] == "proj-1"
        assert eid

    @pytest.mark.asyncio
    async def test_record_completion_defaults(self, recorder: EpisodicMemoryRecorder, repo: MockRepo) -> None:
        await recorder.record_completion(agent_id="agent-1", task_type="test")
        assert len(repo.set_calls) == 1

    def test_recorder_methods_exist(self) -> None:
        methods = [
            m for m in dir(EpisodicMemoryRecorder)
            if not m.startswith("_") and callable(getattr(EpisodicMemoryRecorder, m, None))
        ]
        for method in ("record_episode", "get_episode", "list_episodes", "list_by_outcome", "record_completion"):
            assert method in methods, f"Expected method {method} not found"
