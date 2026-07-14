"""Structural tests for routers/messages.py — inter-agent message API."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from general_ludd.routers.messages import (
    _MAX_INMEMORY_MESSAGES,
    SendMessageRequest,
    _get_session_factory,
    _msg_to_dict,
    register,
)


class TestSendMessageRequest:
    def test_minimal_fields(self):
        req = SendMessageRequest(sender="agent-1", recipient="agent-2")
        assert req.sender == "agent-1"
        assert req.recipient == "agent-2"
        assert req.topic == ""
        assert req.body == ""
        assert req.priority == "normal"
        assert req.ttl_seconds is None
        assert req.project_id is None

    def test_all_fields(self):
        req = SendMessageRequest(
            sender="orch",
            recipient="worker-3",
            topic="deploy",
            body="Deploy v1.2 to staging.",
            priority="high",
            ttl_seconds=3600,
            project_id="proj-abc",
        )
        assert req.sender == "orch"
        assert req.recipient == "worker-3"
        assert req.topic == "deploy"
        assert req.body == "Deploy v1.2 to staging."
        assert req.priority == "high"
        assert req.ttl_seconds == 3600
        assert req.project_id == "proj-abc"

    def test_priority_validation_rejects_bogus(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", priority="bogus")

    def test_priority_valid_values(self):
        for p in ("low", "normal", "high", "urgent"):
            req = SendMessageRequest(sender="a", recipient="b", priority=p)
            assert req.priority == p

    def test_sender_min_length(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="", recipient="b")

    def test_ttl_seconds_ge_1(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", ttl_seconds=0)

    def test_ttl_seconds_none_allowed(self):
        req = SendMessageRequest(sender="a", recipient="b", ttl_seconds=None)
        assert req.ttl_seconds is None

    def test_sender_max_length(self):
        long_name = "x" * 129
        with pytest.raises(ValueError):
            SendMessageRequest(sender=long_name, recipient="b")

    def test_recipient_max_length(self):
        long_name = "x" * 129
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient=long_name)


class TestConstants:
    def test_max_inmemory_messages(self):
        assert _MAX_INMEMORY_MESSAGES == 5000

    def test_max_inmemory_messages_is_int(self):
        assert isinstance(_MAX_INMEMORY_MESSAGES, int)


class TestGetter:
    def test_get_session_factory_is_callable(self):
        assert callable(_get_session_factory)

    def test_get_session_factory_returns_none_no_factory(self):
        app = FastAPI()
        assert _get_session_factory(app) is None


class TestMsgToDict:
    def test_returns_dict_with_expected_keys(self):
        from general_ludd.db.models import AgentMessageModel

        msg = AgentMessageModel(
            id="MSG-A1B2C3D4E5F6",
            sender="orch",
            recipient="worker-1",
            topic="status",
            body="All systems go.",
            priority="normal",
            created_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            read_at=None,
            ttl_seconds=600,
            project_id="proj-1",
        )
        result = _msg_to_dict(msg)
        assert result["id"] == "MSG-A1B2C3D4E5F6"
        assert result["sender"] == "orch"
        assert result["recipient"] == "worker-1"
        assert result["topic"] == "status"
        assert result["body"] == "All systems go."
        assert result["priority"] == "normal"
        assert result["project_id"] == "proj-1"
        assert result["ttl_seconds"] == 600
        assert result["read_at"] is None
        assert isinstance(result["created_at"], str)

    def test_read_at_not_none_serialized(self):
        from general_ludd.db.models import AgentMessageModel

        read_time = datetime(2025, 1, 15, 11, 0, 0, tzinfo=UTC)
        msg = AgentMessageModel(
            id="MSG-222222222222",
            sender="a",
            recipient="b",
            created_at=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            read_at=read_time,
            ttl_seconds=None,
        )
        result = _msg_to_dict(msg)
        assert result["read_at"] == str(read_time)


class TestRegister:
    def test_register_is_callable(self):
        assert callable(register)

    def test_register_accepts_two_args(self):
        app = FastAPI()
        try:
            register(app, {})
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc

    def test_registers_send_route(self):
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/api/messages" in routes

    def test_registers_ack_route(self):
        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes}
        assert "/api/messages/{message_id}/ack" in routes

    def test_route_methods(self):
        app = FastAPI()
        register(app, {})
        methods: dict[str, set[str]] = {}
        for r in app.routes:
            if hasattr(r, "methods"):
                methods[r.path] = r.methods
        assert "POST" in methods.get("/api/messages", set())
        assert "GET" in methods.get("/api/messages", set())
        assert "POST" in methods.get("/api/messages/{message_id}/ack", set())

    def test_register_bounds_memory_store_to_deque(self):
        import collections

        app = FastAPI()
        state: dict[str, object] = {"messages": [{"id": "old"}]}
        register(app, state)
        assert isinstance(state["messages"], collections.deque)
        assert len(state["messages"]) == 1
        assert state["messages"][0]["id"] == "old"
        # maxlen set correctly
        assert state["messages"].maxlen == _MAX_INMEMORY_MESSAGES

    def test_register_idempotent_with_deque(self):
        import collections

        app = FastAPI()
        dq = collections.deque([{"id": "x"}], maxlen=_MAX_INMEMORY_MESSAGES)
        state: dict[str, object] = {"messages": dq}
        register(app, state)
        assert state["messages"] is dq

    def test_register_empty_state(self):
        import collections

        app = FastAPI()
        state: dict[str, object] = {}
        register(app, state)
        assert isinstance(state["messages"], collections.deque)
        assert len(state["messages"]) == 0
