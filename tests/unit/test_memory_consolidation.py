"""Structural tests for memory/consolidation.py — MemoryConsolidator."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from general_ludd.memory.consolidation import (
    CONSOLIDATED_NAMESPACE,
    CONSOLIDATION_KEY_PREFIX,
    MemoryConsolidator,
    _safe_key,
)

EPISODIC_NAMESPACE = "episodic"


class TestSafeKey:
    def test_alphanumeric_preserved(self):
        assert _safe_key("hello_world-123") == "hello_world-123"

    def test_spaces_replaced(self):
        assert _safe_key("hello world") == "hello_world"

    def test_special_chars_replaced(self):
        result = _safe_key("hello@world!")
        assert "@" not in result
        assert "!" not in result

    def test_lowercases(self):
        assert _safe_key("HelloWorld") == "helloworld"

    def test_truncates_to_64_chars(self):
        long_name = "a" * 100
        assert len(_safe_key(long_name)) == 64

    def test_mixed_allowed_and_special(self):
        result = _safe_key("Hello World (v2)!")
        assert "(" not in result
        assert ")" not in result
        assert result.startswith("hello_world_")


class TestConstants:
    def test_consolidated_namespace(self):
        assert CONSOLIDATED_NAMESPACE == "consolidated"

    def test_consolidation_key_prefix(self):
        assert CONSOLIDATION_KEY_PREFIX == "summary_"

    def test_namespace_not_empty(self):
        assert len(CONSOLIDATED_NAMESPACE) > 0

    def test_prefix_ends_with_underscore(self):
        assert CONSOLIDATION_KEY_PREFIX.endswith("_")


class TestMemoryConsolidatorInit:
    def test_default_construction(self):
        repo = object()
        mc = MemoryConsolidator(memory_repo=repo)
        assert mc._repo is repo
        assert mc._model_gateway is None
        assert mc._min_episodes == 10
        assert mc._max_age_hours == 24.0

    def test_custom_min_episodes(self):
        mc = MemoryConsolidator(memory_repo=object(), min_episodes_to_consolidate=5)
        assert mc._min_episodes == 5

    def test_custom_max_age_hours(self):
        mc = MemoryConsolidator(memory_repo=object(), max_episode_age_hours=12.0)
        assert mc._max_age_hours == 12.0

    def test_all_custom_params(self):
        repo = object()
        gw = object()
        mc = MemoryConsolidator(
            memory_repo=repo, model_gateway=gw, min_episodes_to_consolidate=7, max_episode_age_hours=48.0
        )
        assert mc._repo is repo
        assert mc._model_gateway is gw
        assert mc._min_episodes == 7
        assert mc._max_age_hours == 48.0

    def test_with_model_gateway_only(self):
        gw = object()
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        assert mc._model_gateway is gw
        assert mc._min_episodes == 10

    def test_model_gateway_default_none(self):
        mc = MemoryConsolidator(memory_repo=object())
        assert mc._model_gateway is None


class TestConsolidateInsufficientEpisodes:
    @staticmethod
    async def test_too_few_episodes():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] == 0
        assert "insufficient" in result["reason"]
        assert result["total"] == 0

    @staticmethod
    async def test_below_threshold_returns_early():
        repo = _FakeRepo(episodes=[_make_episode_dict(task_type="code") for _ in range(5)])
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] == 0
        assert "insufficient" in result["reason"]
        assert result["total"] == 5

    @staticmethod
    async def test_old_episodes_below_min_returns_early():
        old_ts = _old_iso()
        recent_ts = datetime.now(UTC).isoformat()
        old_eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(3)]
        recent_eps = [_make_episode_dict(task_type="code", created_at=recent_ts) for _ in range(10)]
        repo = _FakeRepo(episodes=old_eps + recent_eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] == 0
        assert result["reason"] == "insufficient old episodes"
        assert result["old_count"] == 3
        assert result["total"] == 13

    @staticmethod
    async def test_recent_episodes_skip_consolidation():
        recent_ts = datetime.now(UTC).isoformat()
        eps = [_make_episode_dict(task_type="code", created_at=recent_ts) for _ in range(15)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=5, max_episode_age_hours=24.0)
        result = await mc.consolidate("agent-1")
        assert result["reason"] == "insufficient old episodes"
        assert result["consolidated"] == 0

    @staticmethod
    async def test_force_bypasses_both_checks():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=10)
        result = await mc.consolidate("agent-1", force=True)
        assert result["consolidated"] == 0
        assert "consolidated" in result


class TestGroupByTaskType:
    def test_single_group_all_same_type(self):
        eps = [_make_fake_ep(task_type="code") for _ in range(5)]
        groups = _group_episodes(eps)
        assert len(groups) == 1
        assert "code" in groups
        assert len(groups["code"]) == 5

    def test_multiple_groups_distinct_types(self):
        eps = [
            _make_fake_ep(task_type="code"),
            _make_fake_ep(task_type="code"),
            _make_fake_ep(task_type="test"),
            _make_fake_ep(task_type="test"),
            _make_fake_ep(task_type="test"),
            _make_fake_ep(task_type="deploy"),
        ]
        groups = _group_episodes(eps)
        assert len(groups) == 3
        assert len(groups["code"]) == 2
        assert len(groups["test"]) == 3
        assert len(groups["deploy"]) == 1

    def test_none_task_type_maps_to_unknown(self):
        eps = [
            _make_fake_ep(task_type=None),
            _make_fake_ep(task_type=None),
            _make_fake_ep(task_type="code"),
        ]
        groups = _group_episodes(eps)
        assert len(groups) == 2
        assert len(groups["unknown"]) == 2
        assert len(groups["code"]) == 1

    def test_empty_list_produces_no_groups(self):
        groups = _group_episodes([])
        assert len(groups) == 0

    def test_single_episode_per_type(self):
        eps = [
            _make_fake_ep(task_type="code"),
            _make_fake_ep(task_type="test"),
            _make_fake_ep(task_type="deploy"),
            _make_fake_ep(task_type="refactor"),
        ]
        groups = _group_episodes(eps)
        assert len(groups) == 4
        for task_type in ("code", "test", "deploy", "refactor"):
            assert len(groups[task_type]) == 1

    def test_group_keys_are_strings(self):
        eps = [_make_fake_ep(task_type="code"), _make_fake_ep(task_type="test")]
        groups = _group_episodes(eps)
        for key in groups:
            assert isinstance(key, str)


class TestSummarizeGroupStructure:
    def test_empty_episodes(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("test_type", [])
        assert summary["task_type"] == "test_type"
        assert summary["episode_count"] == 0
        assert summary["avg_duration_seconds"] == 0
        assert summary["total_duration_seconds"] == 0
        assert summary["outcomes"] == {}
        assert summary["priorities"] == {}
        assert summary["error_patterns"] == []
        assert summary["key_takeaways"] == []
        assert "consolidated_at" in summary

    def test_single_success_episode(self):
        ep = _make_fake_ep(
            outcome="success", takeaway="keep it simple", priority="high", duration_seconds=10.0
        )
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [ep])
        assert summary["task_type"] == "code"
        assert summary["episode_count"] == 1
        assert summary["outcomes"] == {"success": 1}
        assert summary["priorities"] == {"high": 1}
        assert summary["total_duration_seconds"] == 10.0
        assert summary["avg_duration_seconds"] == 10.0
        assert summary["error_patterns"] == []
        assert summary["key_takeaways"] == ["keep it simple"]
        assert summary["consolidated_at"] is not None

    def test_single_failure_episode(self):
        ep = _make_fake_ep(
            outcome="failure", error_message="disk full", takeaway="", priority="medium", duration_seconds=5.0
        )
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("deploy", [ep])
        assert summary["outcomes"] == {"failure": 1}
        assert summary["error_patterns"] == ["disk full"]
        assert summary["key_takeaways"] == []

    def test_response_fields_present(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [])
        expected_keys = {
            "task_type", "episode_count", "outcomes", "priorities",
            "total_duration_seconds", "avg_duration_seconds",
            "error_patterns", "key_takeaways", "consolidated_at",
        }
        assert set(summary.keys()) == expected_keys

    def test_consolidated_at_is_iso_timestamp(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [_make_fake_ep()])
        ts = summary["consolidated_at"]
        assert isinstance(ts, str)
        datetime.fromisoformat(ts)

    def test_error_patterns_unique(self):
        eps = [
            _make_fake_ep(outcome="failure", error_message="timeout"),
            _make_fake_ep(outcome="failure", error_message="timeout"),
            _make_fake_ep(outcome="failure", error_message="disk full"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert sorted(summary["error_patterns"]) == ["disk full", "timeout"]

    def test_takeaways_unique(self):
        eps = [
            _make_fake_ep(outcome="success", takeaway="use caching"),
            _make_fake_ep(outcome="success", takeaway="use caching"),
            _make_fake_ep(outcome="success", takeaway="write tests first"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert sorted(summary["key_takeaways"]) == ["use caching", "write tests first"]

    def test_error_patterns_capped_at_10(self):
        eps = [_make_fake_ep(outcome="failure", error_message=f"err_{i}") for i in range(15)]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert len(summary["error_patterns"]) == 10

    def test_takeaways_capped_at_10(self):
        eps = [_make_fake_ep(outcome="success", takeaway=f"tip_{i}") for i in range(15)]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert len(summary["key_takeaways"]) == 10


class TestComputeStatistics:
    def test_outcome_counter_basic(self):
        eps = [
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="failure"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["outcomes"]["success"] == 3
        assert summary["outcomes"]["failure"] == 1

    def test_outcome_counter_three_types(self):
        eps = [
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="failure"),
            _make_fake_ep(outcome="partial"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["outcomes"]["success"] == 1
        assert summary["outcomes"]["failure"] == 1
        assert summary["outcomes"]["partial"] == 1
        assert len(summary["outcomes"]) == 3

    def test_priority_counter_mixed(self):
        eps = [
            _make_fake_ep(priority="high"),
            _make_fake_ep(priority="high"),
            _make_fake_ep(priority="high"),
            _make_fake_ep(priority="medium"),
            _make_fake_ep(priority="low"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["priorities"]["high"] == 3
        assert summary["priorities"]["medium"] == 1
        assert summary["priorities"]["low"] == 1
        assert summary["episode_count"] == 5

    def test_duration_statistics(self):
        eps = [
            _make_fake_ep(duration_seconds=10.0),
            _make_fake_ep(duration_seconds=20.0),
            _make_fake_ep(duration_seconds=30.0),
            _make_fake_ep(duration_seconds=20.0),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["total_duration_seconds"] == 80.0
        assert summary["avg_duration_seconds"] == 20.0

    def test_duration_total_is_sum_of_all(self):
        eps = [_make_fake_ep(duration_seconds=float(i)) for i in range(5)]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["total_duration_seconds"] == 10.0
        assert summary["avg_duration_seconds"] == 2.0

    def test_all_failure_stats(self):
        eps = [
            _make_fake_ep(outcome="failure", error_message="e1"),
            _make_fake_ep(outcome="failure", error_message="e2"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["outcomes"]["failure"] == 2
        assert "success" not in summary["outcomes"]
        assert summary["key_takeaways"] == []

    def test_all_success_stats(self):
        eps = [
            _make_fake_ep(outcome="success", takeaway="t1"),
            _make_fake_ep(outcome="success", takeaway="t2"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["outcomes"]["success"] == 2
        assert "failure" not in summary["outcomes"]
        assert summary["error_patterns"] == []

    def test_outcomes_is_dict(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [_make_fake_ep(outcome="success")])
        assert isinstance(summary["outcomes"], dict)

    def test_priorities_is_dict(self):
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", [_make_fake_ep(priority="high")])
        assert isinstance(summary["priorities"], dict)

    def test_mixed_outcomes_produces_correct_counts(self):
        eps = [
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="failure"),
            _make_fake_ep(outcome="success"),
            _make_fake_ep(outcome="failure"),
            _make_fake_ep(outcome="success"),
        ]
        mc = MemoryConsolidator(memory_repo=object())
        summary = mc._summarize_group("code", eps)
        assert summary["outcomes"]["success"] == 3
        assert summary["outcomes"]["failure"] == 2


class TestStoreConsolidated:
    @staticmethod
    async def test_namespace_is_consolidated():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(5)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=3, max_episode_age_hours=0.0)
        await mc.consolidate("agent-1")
        consolidated_keys = [(a, n, k) for (a, n, k) in repo._stored if n == CONSOLIDATED_NAMESPACE]
        assert len(consolidated_keys) >= 1
        for (agent_id, namespace, _key) in consolidated_keys:
            assert namespace == CONSOLIDATED_NAMESPACE
            assert agent_id == "agent-1"

    @staticmethod
    async def test_key_prefix_is_summary():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(5)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=3, max_episode_age_hours=0.0)
        await mc.consolidate("agent-1")
        summary_keys = [
            k for (_, n, k) in repo._stored
            if n == CONSOLIDATED_NAMESPACE and k.startswith(CONSOLIDATION_KEY_PREFIX)
        ]
        assert len(summary_keys) >= 1
        for key in summary_keys:
            assert key.startswith(CONSOLIDATION_KEY_PREFIX)

    @staticmethod
    async def test_value_is_json_serializable():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(3)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0)
        await mc.consolidate("agent-1")
        for (_agent_id, _namespace, _key), val in repo._stored.items():
            parsed = json.loads(val)
            assert isinstance(parsed, dict)
            assert "task_type" in parsed

    @staticmethod
    async def test_project_id_passed_to_storage():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(3)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0)
        await mc.consolidate("agent-1", project_id="proj-42")
        consolidated_keys = [(a, n, k) for (a, n, k) in repo._stored if n == CONSOLIDATED_NAMESPACE]
        assert len(consolidated_keys) >= 1
        for (agent_id, namespace, key) in consolidated_keys:
            assert repo._set_metadata[(agent_id, namespace, key)]["project_id"] == "proj-42"

    @staticmethod
    async def test_multiple_task_types_each_stored():
        old_ts = _old_iso()
        eps = [
            _make_episode_dict(task_type="code", created_at=old_ts),
            _make_episode_dict(task_type="test", created_at=old_ts),
            _make_episode_dict(task_type="deploy", created_at=old_ts),
        ]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] >= 3
        stored_keys = [key for (_, _, key) in repo._stored if key.startswith(CONSOLIDATION_KEY_PREFIX)]
        assert len(stored_keys) == 3

    @staticmethod
    async def test_consolidate_with_model_gateway_stores_insight():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", created_at=old_ts) for _ in range(5)]
        repo = _FakeRepo(episodes=eps)
        gw = _FakeGateway()
        mc = MemoryConsolidator(
            memory_repo=repo, model_gateway=gw, min_episodes_to_consolidate=1, max_episode_age_hours=0.0
        )
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] >= 2
        model_keys = [k for (_, _, k) in repo._stored if k == "model_insight"]
        assert len(model_keys) == 1


class TestConsolidateSuccessful:
    @staticmethod
    async def test_consolidate_with_old_episodes():
        old_ts = _old_iso()
        eps = [_make_episode_dict(task_type="code", outcome="success", created_at=old_ts) for _ in range(15)]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=5)
        result = await mc.consolidate("agent-1")
        assert result["consolidated"] >= 1
        assert "code" in result["task_types"]
        assert result["episodes_consolidated"] >= 10

    @staticmethod
    async def test_consolidate_separates_old_from_recent():
        old_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        recent_ts = datetime.now(UTC).isoformat()
        eps = [
            _make_episode_dict(task_type="code", created_at=old_ts),
            _make_episode_dict(task_type="code", created_at=old_ts),
            _make_episode_dict(task_type="code", created_at=old_ts),
            _make_episode_dict(task_type="code", created_at=recent_ts),
        ]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=2, max_episode_age_hours=24.0)
        result = await mc.consolidate("agent-1")
        assert result["episodes_consolidated"] == 3

    @staticmethod
    async def test_consolidate_unparseable_created_at_handled():
        eps = [
            _make_episode_dict(task_type="code", created_at="not-a-date"),
            _make_episode_dict(task_type="code", created_at=_old_iso()),
        ]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0)
        result = await mc.consolidate("agent-1")
        assert result["episodes_consolidated"] >= 1

    @staticmethod
    async def test_consolidate_task_types_list_correct():
        old_ts = _old_iso()
        eps = [
            _make_episode_dict(task_type="code", created_at=old_ts),
            _make_episode_dict(task_type="test", created_at=old_ts),
        ]
        repo = _FakeRepo(episodes=eps)
        mc = MemoryConsolidator(memory_repo=repo, min_episodes_to_consolidate=1, max_episode_age_hours=0.0)
        result = await mc.consolidate("agent-1")
        assert sorted(result["task_types"]) == sorted(["code", "test"])


class TestGetConsolidated:
    @staticmethod
    async def test_empty_repo_returns_empty_list():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1")
        assert result == []

    @staticmethod
    async def test_returns_stored_summaries():
        stored_val = json.dumps({"task_type": "code", "episode_count": 5})
        repo = _FakeRepo(episodes=[])
        repo._stored[("agent-1", CONSOLIDATED_NAMESPACE, "summary_code")] = stored_val
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1")
        assert len(result) == 1
        assert result[0]["task_type"] == "code"

    @staticmethod
    async def test_filters_by_task_type():
        repo = _FakeRepo(episodes=[])
        repo._stored[("agent-1", CONSOLIDATED_NAMESPACE, "summary_code")] = json.dumps(
            {"task_type": "code", "episode_count": 5}
        )
        repo._stored[("agent-1", CONSOLIDATED_NAMESPACE, "summary_test")] = json.dumps(
            {"task_type": "test", "episode_count": 3}
        )
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1", task_type="code")
        assert len(result) == 1
        assert result[0]["task_type"] == "code"

    @staticmethod
    async def test_skips_invalid_json():
        repo = _FakeRepo(episodes=[])
        repo._stored[("agent-1", CONSOLIDATED_NAMESPACE, "bad")] = "not-valid-json"
        repo._stored[("agent-1", CONSOLIDATED_NAMESPACE, "good")] = json.dumps({"task_type": "code"})
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1")
        assert len(result) == 1

    @staticmethod
    async def test_respects_project_id():
        repo = _FakeRepo(episodes=[])
        mc = MemoryConsolidator(memory_repo=repo)
        result = await mc.get_consolidated("agent-1", project_id="other")
        assert result == []


class TestModelConsolidate:
    @pytest.mark.asyncio
    async def test_no_gateway_returns_none(self):
        mc = MemoryConsolidator(memory_repo=object())
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_with_gateway_extracts_json(self):
        gw = _FakeGateway()
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert "weaknesses" in str(result)
        assert gw.call_count == 1

    @pytest.mark.asyncio
    async def test_strips_code_fences(self):
        gw = _FakeGateway(response_text='```json\n{"key": "value"}\n```')
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert result == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        class Broken:
            call_count = 0

            def call_model(self, *args, **kwargs):
                self.call_count += 1
                raise RuntimeError("boom")

        mc = MemoryConsolidator(memory_repo=object(), model_gateway=Broken())
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_three_backtick_fence(self):
        gw = _FakeGateway(response_text='```\nsome text\nmore text\n```')
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert result == "some text\nmore text"

    @pytest.mark.asyncio
    async def test_calls_complete_fallback(self):
        class CompleteOnly:
            call_count = 0

            def complete(self, prompt):
                self.call_count += 1

                class FakeResponse:
                    content = '{"a": 1}'
                return FakeResponse()

        gw = CompleteOnly()
        mc = MemoryConsolidator(memory_repo=object(), model_gateway=gw)
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert "a" in str(result)
        assert gw.call_count == 1

    @pytest.mark.asyncio
    async def test_complete_fallback_exception_returns_none(self):
        class BrokenComplete:
            def complete(self, prompt):
                raise RuntimeError("simulated")

        mc = MemoryConsolidator(memory_repo=object(), model_gateway=BrokenComplete())
        result = await mc._model_consolidate({"code": {"task_type": "code"}})
        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _old_iso() -> str:
    old = datetime.now(UTC) - timedelta(days=100)
    return old.isoformat()


_ep_counter = 0


def _make_episode_dict(
    *,
    outcome: str = "success",
    error_message: str = "",
    takeaway: str = "",
    priority: str = "medium",
    duration_seconds: float = 5.0,
    task_type: str | None = "code",
    created_at: str | None = None,
) -> dict[str, Any]:
    global _ep_counter
    _ep_counter += 1
    return {
        "id": f"ep-{_ep_counter}",
        "agent_id": "agent-1",
        "task_type": task_type,
        "work_type": "code",
        "priority": priority,
        "outcome": outcome,
        "context": {},
        "tools_used": [],
        "takeaway": takeaway,
        "error_message": error_message,
        "duration_seconds": duration_seconds,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


def _make_fake_ep(
    *,
    outcome: str = "success",
    error_message: str = "",
    takeaway: str = "",
    priority: str = "medium",
    duration_seconds: float = 5.0,
    task_type: str | None = "code",
    created_at: str | None = None,
) -> _FakeEpisode:
    return _FakeEpisode(
        outcome=outcome,
        error_message=error_message,
        takeaway=takeaway,
        priority=priority,
        duration_seconds=duration_seconds,
        task_type=task_type,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )


def _group_episodes(episodes: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for ep in episodes:
        grouped[ep.task_type or "unknown"].append(ep)
    return dict(grouped)


class _FakeGateway:
    def __init__(self, response_text: str = '{"weaknesses":["slow"],"recommendation":"improve"}'):
        self.response_text = response_text
        self.call_count = 0

    def call_model(self, profile_id, messages, work_type="unknown"):
        self.call_count += 1

        class FakeResponse:
            content = self.response_text
        return FakeResponse()

    def complete(self, prompt):
        self.call_count += 1

        class FakeResponse:
            content = self.response_text
        return FakeResponse()


class _FakeRepo:
    def __init__(self, episodes: list[dict[str, Any]] | None = None):
        self._stored: dict[tuple[str, str, str], str] = {}
        self._set_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}
        for ep_dict in (episodes or []):
            ep_key = ep_dict.get("id", f"ep-{hash(json.dumps(ep_dict, sort_keys=True, default=str))}")
            self._stored[("agent-1", EPISODIC_NAMESPACE, ep_key)] = json.dumps(ep_dict, default=str)

    async def set(self, *, agent_id, key, value, namespace="", project_id=None):
        self._stored[(agent_id, namespace, key)] = value
        self._set_metadata[(agent_id, namespace, key)] = {"project_id": project_id}

    async def list_by_namespace(self, agent_id, *, namespace="", project_id=None, limit=100):
        results = []
        for (aid, ns, _key), val in self._stored.items():
            if aid == agent_id and ns == namespace:
                if project_id is not None:
                    meta = self._set_metadata.get((aid, ns, _key))
                    if meta is None:
                        pass
                    elif meta.get("project_id") != project_id:
                        continue
                results.append(_FakeRow(value=val))
        return results[:limit]


class _FakeEpisode:
    def __init__(
        self,
        *,
        outcome: str = "success",
        error_message: str = "",
        takeaway: str = "",
        priority: str = "medium",
        duration_seconds: float = 5.0,
        task_type: str | None = "code",
        created_at: str | None = None,
    ):
        self.outcome = outcome
        self.error_message = error_message
        self.takeaway = takeaway
        self.priority = priority
        self.duration_seconds = duration_seconds
        self.task_type = task_type
        self.created_at = created_at or datetime.now(UTC).isoformat()


class _FakeRow:
    def __init__(self, *, value: str = ""):
        self.value = value
