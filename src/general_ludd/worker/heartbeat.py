"""Worker heartbeat — ping/pong liveness exchange.

The daemon (or a peer worker) sends a :class:`WorkerPingEvent`; the worker
answers with a :class:`WorkerPongEvent` correlated to the ping's id. The
worker app exposes this via ``POST /ping`` so liveness can be observed over
the wire, not just inferred from ``/healthz``.
"""

from __future__ import annotations

from general_ludd.events.types import WorkerPingEvent, WorkerPongEvent


def make_ping() -> WorkerPingEvent:
    """Construct a ping event a caller can send to a worker."""
    return WorkerPingEvent()


def handle_ping(ping: WorkerPingEvent, worker_id: str) -> WorkerPongEvent:
    """Answer a ping with a pong correlated to the ping's event id."""
    return WorkerPongEvent(worker_id=worker_id, correlation_id=ping.event_id)
