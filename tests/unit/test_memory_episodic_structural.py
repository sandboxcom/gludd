"""Structural tests for memory/episodic.py — Episode and serialization."""

from __future__ import annotations

from general_ludd.memory.episodic import EPISODIC_NAMESPACE, Episode, _dict_to_episode, _episode_to_dict


class TestEpisode:
    def test_defaults(self):
        ep = Episode()
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
        assert len(ep.created_at) > 0

    def test_custom_fields(self):
        ep = Episode(
            agent_id="a1",
            task_type="code",
            work_type="fix",
            priority="high",
            outcome="success",
            context={"file": "x.py"},
            tools_used=["read", "edit"],
            takeaway="learned something",
            error_message="",
            duration_seconds=12.5,
            created_at="2024-01-01T00:00:00+00:00",
        )
        assert ep.agent_id == "a1"
        assert ep.task_type == "code"
        assert ep.outcome == "success"
        assert ep.tools_used == ["read", "edit"]
        assert ep.duration_seconds == 12.5


class TestEpisodeRoundtrip:
    def test_roundtrip_dict(self):
        ep = Episode(
            agent_id="a1",
            task_type="code",
            work_type="fix",
            priority="high",
            outcome="success",
            context={"file": "x.py"},
            tools_used=["read", "edit"],
            takeaway="t",
            error_message="",
            duration_seconds=5.0,
        )
        d = _episode_to_dict(ep)
        ep2 = _dict_to_episode(d)
        assert ep2.agent_id == ep.agent_id
        assert ep2.task_type == ep.task_type
        assert ep2.outcome == ep.outcome
        assert ep2.tools_used == ep.tools_used
        assert ep2.duration_seconds == ep.duration_seconds

    def test_dict_to_episode_missing_fields(self):
        ep = _dict_to_episode({"id": "abc", "agent_id": "x"})
        assert ep.id == "abc"
        assert ep.agent_id == "x"
        assert ep.task_type == ""
        assert ep.outcome == "unknown"
        assert ep.context == {}
        assert ep.tools_used == []

    def test_dict_to_episode_converts_duration(self):
        ep = _dict_to_episode({"duration_seconds": "3.5"})
        assert ep.duration_seconds == 3.5


class TestEpisodicNamespace:
    def test_constant_value(self):
        assert EPISODIC_NAMESPACE == "episodic"
