"""Deep edge-case tests for routers/messages.py.

Covers paths not exercised by the existing structural + regression suites:
degraded-mode edge flows, validation boundaries, broadcast filtering,
ack cross-tenant refusal, deque re-registration, and serialization edges.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import messages as messages_router
from general_ludd.routers.messages import (
    _MAX_INMEMORY_MESSAGES,
    SendMessageRequest,
    _get_session_factory,
    _msg_to_dict,
    register,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _build_degraded_client(state: dict | None = None) -> TestClient:
    app = FastAPI()
    messages_router.register(app, state or {})
    return TestClient(app)


def _build_degraded_app_state(
    state: dict | None = None,
) -> tuple[FastAPI, dict]:
    app = FastAPI()
    daemon_state: dict = state or {}
    messages_router.register(app, daemon_state)
    return app, daemon_state


# ── SendMessageRequest boundary validation ──────────────────────────────


class TestSendMessageRequestDeep:
    def test_body_exactly_max_length_passes(self):
        body = "x" * 65536
        req = SendMessageRequest(sender="a", recipient="b", body=body)
        assert len(req.body) == 65536

    def test_body_exceeds_max_length_raises(self):
        body = "x" * 65537
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", body=body)

    def test_topic_exactly_max_length_passes(self):
        topic = "x" * 256
        req = SendMessageRequest(sender="a", recipient="b", topic=topic)
        assert len(req.topic) == 256

    def test_topic_exceeds_max_length_raises(self):
        topic = "x" * 257
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", topic=topic)

    def test_sender_exactly_max_length_passes(self):
        sender = "x" * 128
        req = SendMessageRequest(sender=sender, recipient="b")
        assert req.sender == sender

    def test_recipient_exactly_max_length_passes(self):
        recipient = "x" * 128
        req = SendMessageRequest(sender="a", recipient=recipient)
        assert req.recipient == recipient

    def test_priority_empty_string_raises(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", priority="")

    def test_priority_case_sensitive_uppercase_raises(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", priority="NORMAL")

    def test_priority_case_sensitive_mixed_raises(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", priority="High")

    def test_ttl_seconds_negative_raises(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", ttl_seconds=-1)

    def test_ttl_seconds_very_large_passes(self):
        req = SendMessageRequest(sender="a", recipient="b", ttl_seconds=999999999)
        assert req.ttl_seconds == 999999999

    def test_ttl_seconds_float_raises(self):
        with pytest.raises(ValueError):
            SendMessageRequest(sender="a", recipient="b", ttl_seconds=1.5)  # type: ignore[arg-type]

    def test_sender_whitespace_only_passes_pydantic(self):
        req = SendMessageRequest(sender="   ", recipient="b")
        assert req.sender == "   "

    def test_project_id_none_passes(self):
        req = SendMessageRequest(sender="a", recipient="b", project_id=None)
        assert req.project_id is None

    def test_project_id_empty_string_passes(self):
        req = SendMessageRequest(sender="a", recipient="b", project_id="")
        assert req.project_id == ""


# ── degraded POST /api/messages edge cases ──────────────────────────────


class TestDegradedSendEdgeCases:
    def test_send_with_all_optional_fields_populated(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={
                "sender": "orch",
                "recipient": "worker-1",
                "topic": "deploy",
                "body": "go",
                "priority": "urgent",
                "ttl_seconds": 60,
                "project_id": "proj-x",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sender"] == "orch"
        assert data["recipient"] == "worker-1"
        assert data["topic"] == "deploy"
        assert data["priority"] == "urgent"
        assert data["ttl_seconds"] == 60
        assert data["project_id"] == "proj-x"
        assert data["id"].startswith("MSG-")
        assert data["read_at"] is None
        assert data["created_at"] is not None

    def test_send_minimal_fields_degraded(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["sender"] == "s"
        assert data["recipient"] == "r"
        assert data["priority"] == "normal"
        assert data["topic"] == ""
        assert data["body"] == ""

    def test_send_missing_required_field_returns_422(self):
        client = _build_degraded_client()
        resp = client.post("/api/messages", json={"sender": "s"})
        assert resp.status_code == 422

    def test_send_invalid_priority_returns_422(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "priority": "bogus"},
        )
        assert resp.status_code == 422

    def test_send_ttl_zero_returns_422(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "ttl_seconds": 0},
        )
        assert resp.status_code == 422


# ── degraded GET /api/messages (inbox) edge cases ────────────────────────


class TestDegradedInboxDeep:
    @pytest.fixture(autouse=True)
    def _seed(self):
        self.client = _build_degraded_client()

    def _post(self, sender="s", recipient="r", body="x", **kw):
        return self.client.post(
            "/api/messages",
            json={"sender": sender, "recipient": recipient, "body": body, **kw},
        )

    def test_inbox_empty_no_messages(self):
        resp = self.client.get("/api/messages", params={"recipient": "nobody"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["messages"] == []

    def test_inbox_unread_true_filters_read(self):
        self._post(recipient="agent-x", body="msg1")
        msgs = self.client.get("/api/messages", params={"recipient": "agent-x", "unread": True}).json()["messages"]
        msg_id = msgs[0]["id"]
        self.client.post(f"/api/messages/{msg_id}/ack")

        resp = self.client.get("/api/messages", params={"recipient": "agent-x", "unread": True})
        assert resp.json()["count"] == 0

    def test_inbox_unread_false_returns_read_messages(self):
        self._post(recipient="agent-y", body="hi")
        msgs = self.client.get("/api/messages", params={"recipient": "agent-y", "unread": True}).json()["messages"]
        self.client.post(f"/api/messages/{msgs[0]['id']}/ack")

        resp = self.client.get("/api/messages", params={"recipient": "agent-y", "unread": False})
        assert resp.json()["count"] == 1

    def test_inbox_include_broadcast_true(self):
        self._post(recipient="broadcast", body="all-hands")
        self._post(recipient="agent-z", body="direct")

        resp = self.client.get(
            "/api/messages",
            params={"recipient": "agent-z", "include_broadcast": True},
        )
        data = resp.json()
        assert data["count"] == 2
        recipients = {m["recipient"] for m in data["messages"]}
        assert recipients == {"broadcast", "agent-z"}

    def test_inbox_include_broadcast_false(self):
        self._post(recipient="broadcast", body="all-hands")
        self._post(recipient="agent-z", body="direct")

        resp = self.client.get(
            "/api/messages",
            params={"recipient": "agent-z", "include_broadcast": False},
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["messages"][0]["recipient"] == "agent-z"

    def test_inbox_project_id_filters_in_degraded_mode(self):
        self._post(recipient="agent-p", body="p1", project_id="proj-A")
        self._post(recipient="agent-p", body="p2", project_id="proj-B")

        resp = self.client.get(
            "/api/messages",
            params={"recipient": "agent-p", "project_id": "proj-A"},
        )
        assert resp.json()["count"] == 1
        assert resp.json()["messages"][0]["project_id"] == "proj-A"

    def test_inbox_project_id_none_returns_all_in_degraded(self):
        self._post(recipient="agent-q", body="p1", project_id="proj-A")
        self._post(recipient="agent-q", body="p2", project_id=None)

        resp = self.client.get("/api/messages", params={"recipient": "agent-q"})
        assert resp.json()["count"] == 2

    def test_inbox_recipient_not_matching_returns_empty(self):
        self._post(recipient="agent-a", body="x")
        resp = self.client.get("/api/messages", params={"recipient": "agent-different"})
        assert resp.json()["count"] == 0

    def test_inbox_only_broadcast_recipient_gets_broadcasts(self):
        self._post(recipient="broadcast", body="global")

        resp = self.client.get(
            "/api/messages",
            params={"recipient": "broadcast", "include_broadcast": True},
        )
        assert resp.json()["count"] == 1


# ── degraded POST /api/messages/{id}/ack edge cases ─────────────────────


class TestDegradedAckDeep:
    def test_ack_existing_message_marks_read(self):
        client = _build_degraded_client()
        resp = client.post("/api/messages", json={"sender": "s", "recipient": "r"})
        msg_id = resp.json()["id"]

        ack_resp = client.post(f"/api/messages/{msg_id}/ack")
        assert ack_resp.status_code == 200
        data = ack_resp.json()
        assert data["acked"] is True
        assert data["id"] == msg_id
        assert data["read_at"] is not None

    def test_ack_nonexistent_message_returns_404(self):
        client = _build_degraded_client()
        resp = client.post("/api/messages/FAKE-NOEXIST/ack")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "message not found"

    def test_ack_twice_still_succeeds(self):
        client = _build_degraded_client()
        resp = client.post("/api/messages", json={"sender": "s", "recipient": "r"})
        msg_id = resp.json()["id"]

        r1 = client.post(f"/api/messages/{msg_id}/ack")
        assert r1.status_code == 200
        r2 = client.post(f"/api/messages/{msg_id}/ack")
        assert r2.status_code == 200
        assert r2.json()["acked"] is True

    def test_ack_cross_tenant_refused_degraded(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "project_id": "proj-A"},
        )
        msg_id = resp.json()["id"]

        ack = client.post(
            f"/api/messages/{msg_id}/ack",
            params={"project_id": "proj-B"},
        )
        assert ack.status_code == 404

    def test_ack_cross_tenant_none_vs_set_refused_degraded(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "project_id": "proj-A"},
        )
        msg_id = resp.json()["id"]

        ack = client.post(
            f"/api/messages/{msg_id}/ack",
            params={"project_id": "proj-B"},
        )
        assert ack.status_code == 404

    def test_ack_without_project_id_when_message_has_one_succeeds(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "project_id": "proj-A"},
        )
        msg_id = resp.json()["id"]

        ack = client.post(f"/api/messages/{msg_id}/ack")
        assert ack.status_code == 200

    def test_ack_with_matching_project_id_succeeds(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={"sender": "s", "recipient": "r", "project_id": "proj-A"},
        )
        msg_id = resp.json()["id"]

        ack = client.post(
            f"/api/messages/{msg_id}/ack",
            params={"project_id": "proj-A"},
        )
        assert ack.status_code == 200

    def test_ack_message_after_eviction_returns_404(self):
        app, _daemon_state = _build_degraded_app_state()
        client = TestClient(app)

        for i in range(_MAX_INMEMORY_MESSAGES + 10):
            client.post(
                "/api/messages",
                json={"sender": "s", "recipient": "agent-x", "body": f"m-{i}"},
            )

        resp = client.post("/api/messages/OLD-EVICTED-MSG/ack")
        assert resp.status_code == 404


# ── register() deque edge cases ─────────────────────────────────────────


class TestRegisterDequeDeep:
    def test_register_with_deque_wrong_maxlen_replaces(self):
        app = FastAPI()
        dq = collections.deque([{"id": "a"}], maxlen=100)
        state: dict = {"messages": dq}
        register(app, state)
        assert state["messages"].maxlen == _MAX_INMEMORY_MESSAGES
        assert list(state["messages"]) == [{"id": "a"}]

    def test_register_empty_list_preserved_as_empty_deque(self):
        app = FastAPI()
        state: dict = {"messages": []}
        register(app, state)
        assert isinstance(state["messages"], collections.deque)
        assert len(state["messages"]) == 0
        assert state["messages"].maxlen == _MAX_INMEMORY_MESSAGES

    def test_register_none_messages_key_creates_empty_deque(self):
        app = FastAPI()
        state: dict = {}
        register(app, state)
        assert isinstance(state["messages"], collections.deque)
        assert len(state["messages"]) == 0

    def test_register_non_list_non_deque_messages_overwrites(self):
        app = FastAPI()
        state: dict = {"messages": "string-not-collection"}
        register(app, state)
        assert isinstance(state["messages"], collections.deque)
        assert len(state["messages"]) == 0


# ── _msg_to_dict serialisation edges ────────────────────────────────────


class TestMsgToDictDeep:
    def test_created_at_none_returns_none_string(self):
        from general_ludd.db.models import AgentMessageModel

        msg = AgentMessageModel(
            id="MSG-TEST",
            sender="a",
            recipient="b",
            created_at=None,
            read_at=None,
            ttl_seconds=None,
        )
        result = _msg_to_dict(msg)
        assert result["created_at"] is None
        assert result["read_at"] is None

    def test_all_optional_fields_none(self):
        from general_ludd.db.models import AgentMessageModel

        msg = AgentMessageModel(
            id="MSG-OPT-NONE",
            sender="s",
            recipient="r",
            topic="",
            body="",
            priority="normal",
            created_at=datetime(2025, 6, 1, tzinfo=UTC),
            read_at=None,
            ttl_seconds=None,
        )
        result = _msg_to_dict(msg)
        assert result["topic"] == ""
        assert result["body"] == ""
        assert result["priority"] == "normal"
        assert result["project_id"] is None
        assert result["ttl_seconds"] is None
        assert result["read_at"] is None

    def test_read_at_set_serializes_as_string(self):
        from general_ludd.db.models import AgentMessageModel

        ts = datetime(2025, 7, 15, 14, 30, 0, tzinfo=UTC)
        msg = AgentMessageModel(
            id="MSG-RD",
            sender="a",
            recipient="b",
            created_at=ts,
            read_at=ts,
            ttl_seconds=None,
        )
        result = _msg_to_dict(msg)
        assert result["read_at"] == str(ts)


# ── degraded send → inbox → ack full lifecycle ─────────────────────────


class TestDegradedFullLifecycle:
    def test_send_inbox_ack_then_inbox_unread_empty(self):
        client = _build_degraded_client()

        resp = client.post(
            "/api/messages",
            json={"sender": "orch", "recipient": "worker-7", "body": "task-1"},
        )
        assert resp.status_code == 201
        msg_id = resp.json()["id"]

        inbox = client.get(
            "/api/messages",
            params={"recipient": "worker-7", "unread": True},
        )
        assert inbox.json()["count"] == 1

        ack = client.post(f"/api/messages/{msg_id}/ack")
        assert ack.status_code == 200

        inbox2 = client.get(
            "/api/messages",
            params={"recipient": "worker-7", "unread": True},
        )
        assert inbox2.json()["count"] == 0

    def test_broadcast_visible_to_all_recipients(self):
        client = _build_degraded_client()

        client.post(
            "/api/messages",
            json={"sender": "sys", "recipient": "broadcast", "body": "announce"},
        )

        for agent in ("agent-1", "agent-2", "agent-3"):
            resp = client.get(
                "/api/messages",
                params={"recipient": agent, "include_broadcast": True},
            )
            assert resp.json()["count"] == 1
            assert resp.json()["messages"][0]["recipient"] == "broadcast"

    def test_urgent_priority_flows_through(self):
        client = _build_degraded_client()
        resp = client.post(
            "/api/messages",
            json={
                "sender": "monitor",
                "recipient": "oncall",
                "priority": "urgent",
                "body": "pagerduty",
            },
        )
        assert resp.status_code == 201
        inbound = client.get("/api/messages", params={"recipient": "oncall"})
        assert inbound.json()["messages"][0]["priority"] == "urgent"


# ── _get_session_factory ────────────────────────────────────────────────


class TestGetSessionFactoryDeep:
    def test_returns_none_when_no_state_attribute(self):
        app = FastAPI()
        result = _get_session_factory(app)
        assert result is None

    def test_returns_none_when_state_has_no_factory(self):
        app = FastAPI()
        app.state.some_other_attr = 42
        result = _get_session_factory(app)
        assert result is None
