"""Prove receiver endpoints are reachable through the daemon wiring pattern
and the buffer is functional after ingest."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.receiver import router as receiver_router
from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer

TOKEN = "test-wiring-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _set_ingest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(receiver_router.INGEST_TOKEN_ENV, TOKEN)


def _make_client(buffer: ReceiverBuffer | None = None) -> tuple[TestClient, dict]:
    app = FastAPI()
    state: dict = {}
    if buffer is not None:
        state["receiver_buffer"] = buffer
    receiver_router.register(app, state)
    return TestClient(app, raise_server_exceptions=False), state


def _webhook_payload(message: str = "hello", **kwargs: object) -> bytes:
    return json.dumps({"message": message, **kwargs, "host": "test-01"}).encode()


class TestHealthEndpoint:
    def test_health_endpoint_reachable(self) -> None:
        client, _ = _make_client()
        resp = client.get("/api/receive/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "size" in body
        assert "maxlen" in body
        assert "total_offered" in body

    def test_health_reflects_buffer_state(self) -> None:
        buf = ReceiverBuffer(maxlen=100, overflow=OverflowPolicy.REJECT)
        client, _ = _make_client(buffer=buf)

        resp = client.post("/ingest/webhook", content=_webhook_payload("msg1"), headers=AUTH)
        assert resp.status_code == 202

        health = client.get("/api/receive/health").json()
        assert health["size"] == 1
        assert health["total_offered"] == 1

        client.post("/ingest/webhook", content=_webhook_payload("msg2"), headers=AUTH)
        health = client.get("/api/receive/health").json()
        assert health["size"] == 2

    def test_health_no_auth_required(self) -> None:
        client, _ = _make_client()
        resp = client.get("/api/receive/health")
        assert resp.status_code == 200


class TestBufferIntegration:
    def test_accepted_records_drainable(self) -> None:
        buf = ReceiverBuffer(maxlen=100, overflow=OverflowPolicy.REJECT)
        client, _ = _make_client(buffer=buf)

        client.post("/ingest/webhook", content=_webhook_payload("a"), headers=AUTH)
        client.post("/ingest/webhook", content=_webhook_payload("b"), headers=AUTH)

        drained = buf.drain()
        assert len(drained) == 2
        messages = sorted(r.get("join", {}).get("message", str(r))
                          for r in drained if isinstance(r, dict))
        assert "a" in str(messages)
        assert "b" in str(messages)

    def test_buffer_full_backpressure(self) -> None:
        buf = ReceiverBuffer(maxlen=2, overflow=OverflowPolicy.REJECT)
        client, _ = _make_client(buffer=buf)

        assert client.post("/ingest/webhook",
                          content=_webhook_payload("1"), headers=AUTH).status_code == 202
        assert client.post("/ingest/webhook",
                          content=_webhook_payload("2"), headers=AUTH).status_code == 202
        third = client.post("/ingest/webhook",
                           content=_webhook_payload("3"), headers=AUTH)
        assert third.status_code == 503
        assert third.json()["error"] == "buffer_full"

        health = client.get("/api/receive/health").json()
        assert health["total_offered"] == 3
        assert health["total_rejected"] == 1
