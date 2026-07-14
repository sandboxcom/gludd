"""Structural tests for worker/heartbeat.py — ping/pong liveness exchange."""

from __future__ import annotations

from general_ludd.worker.heartbeat import handle_ping, make_ping


class TestMakePing:
    def test_returns_ping_event(self):
        ping = make_ping()
        assert ping is not None
        assert hasattr(ping, "event_id")

    def test_event_id_is_nonempty_string(self):
        ping = make_ping()
        assert isinstance(ping.event_id, str)
        assert len(ping.event_id) > 0


class TestHandlePing:
    def test_returns_pong_event(self):
        ping = make_ping()
        pong = handle_ping(ping, worker_id="test-worker")
        assert pong is not None
        assert hasattr(pong, "correlation_id")

    def test_correlation_id_matches_ping_event_id(self):
        ping = make_ping()
        pong = handle_ping(ping, worker_id="test-worker")
        assert pong.correlation_id == ping.event_id

    def test_payload_contains_worker_id(self):
        ping = make_ping()
        pong = handle_ping(ping, worker_id="test-worker")
        assert pong.payload["worker_id"] == "test-worker"

    def test_worker_id_empty_string(self):
        ping = make_ping()
        pong = handle_ping(ping, worker_id="")
        assert pong.payload["worker_id"] == ""
