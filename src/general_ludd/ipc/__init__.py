"""IPC layer for the gunicorn multi-worker architecture.

Phase 1 ships only the in-process primitives — the :class:`Broker` Protocol
and its :class:`InProcessBroker` default, plus the bounded :class:`WriteQueue`
each worker uses to absorb outgoing bursts. A future phase will add a real
cross-worker transport (Redis pub/sub, POSIX MQ) that implements the same
:class:`Broker` Protocol, making the daemon transport-agnostic.
"""

from __future__ import annotations

from general_ludd.ipc.broker import Broker, Handler, InProcessBroker, Message
from general_ludd.ipc.queue import (
    DEFAULT_WRITE_QUEUE_MAXSIZE,
    Envelope,
    OverflowPolicy,
    WriteQueue,
)

__all__ = [
    "DEFAULT_WRITE_QUEUE_MAXSIZE",
    "Broker",
    "Envelope",
    "Handler",
    "InProcessBroker",
    "Message",
    "OverflowPolicy",
    "WriteQueue",
]
