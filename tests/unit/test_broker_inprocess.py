"""Unit tests for the in-process IPC Broker (Phase 1 of the gunicorn multi-worker architecture).

The ``Broker`` Protocol is the seam along which the daemon's pub/sub will
eventually be swapped from an in-process fan-out (``InProcessBroker``) to a
real cross-worker broker (Redis/PosixMQ-backed) once gunicorn pre-forks. This
file pins the behavior of the default in-process implementation so the swap is
behavior-preserving.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from general_ludd.ipc import Broker, InProcessBroker


def _msg(i: int) -> dict[str, Any]:
    return {"seq": i, "payload": f"m{i}"}


class TestBrokerProtocol:
    def test_inprocess_broker_satisfies_broker_protocol(self) -> None:
        """InProcessBroker must pass the runtime_checkable Broker isinstance check.

        This is the contract a future Redis/PosixMQ broker must also satisfy;
        pinning it here means the daemon can depend on ``Broker`` and stay
        transport-agnostic.
        """
        broker: InProcessBroker = InProcessBroker()
        assert isinstance(broker, Broker)


class TestDelivery:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self) -> None:
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        def handler(message: dict[str, Any]) -> None:
            received.append(message)

        broker.subscribe("topic.a", handler)
        delivered = await broker.publish("topic.a", _msg(1))

        assert delivered == 1
        assert received == [_msg(1)]

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self) -> None:
        broker = InProcessBroker()
        received_a: list[dict[str, Any]] = []
        received_b: list[dict[str, Any]] = []
        received_c: list[dict[str, Any]] = []

        def make_handler(sink: list[dict[str, Any]]) -> Any:
            def handler(message: dict[str, Any]) -> None:
                sink.append(message)
            return handler

        h_a = make_handler(received_a)
        h_b = make_handler(received_b)
        h_c = make_handler(received_c)

        broker.subscribe("topic.x", h_a)
        broker.subscribe("topic.x", h_b)
        broker.subscribe("topic.x", h_c)

        delivered = await broker.publish("topic.x", _msg(7))

        assert delivered == 3
        assert received_a == [_msg(7)]
        assert received_b == [_msg(7)]
        assert received_c == [_msg(7)]

    @pytest.mark.asyncio
    async def test_publish_to_no_subscribers_is_silently_dropped(self) -> None:
        broker = InProcessBroker()
        delivered = await broker.publish("topic.unloved", _msg(0))
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_messages_delivered_in_publish_order(self) -> None:
        broker = InProcessBroker()
        received: list[int] = []

        def handler(message: dict[str, Any]) -> None:
            received.append(message["seq"])

        broker.subscribe("topic.ordered", handler)
        for i in range(10):
            await broker.publish("topic.ordered", _msg(i))

        assert received == list(range(10))


class TestUnsubscribeAndClear:
    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self) -> None:
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        def handler(message: dict[str, Any]) -> None:
            received.append(message)

        broker.subscribe("topic.b", handler)
        broker.unsubscribe("topic.b", handler)

        delivered = await broker.publish("topic.b", _msg(1))
        assert delivered == 0
        assert received == []

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_handler_is_noop(self) -> None:
        """Unsubscribing a handler that was never registered must not raise."""
        broker = InProcessBroker()

        def handler(message: dict[str, Any]) -> None:
            del message

        # Should not raise.
        broker.unsubscribe("topic.never", handler)

    @pytest.mark.asyncio
    async def test_clear_purges_all_subscribers(self) -> None:
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        def handler(message: dict[str, Any]) -> None:
            received.append(message)

        broker.subscribe("topic.c1", handler)
        broker.subscribe("topic.c2", handler)
        broker.clear()

        assert await broker.publish("topic.c1", _msg(1)) == 0
        assert await broker.publish("topic.c2", _msg(2)) == 0
        assert received == []


class TestResilience:
    @pytest.mark.asyncio
    async def test_failing_handler_does_not_block_other_handlers(self) -> None:
        """A handler that raises must not prevent delivery to the rest."""
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        def good_handler(message: dict[str, Any]) -> None:
            received.append(message)

        def bad_handler(message: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        broker.subscribe("topic.r", bad_handler)
        broker.subscribe("topic.r", good_handler)

        delivered = await broker.publish("topic.r", _msg(1))

        # The failing handler does not count as delivered, but the good one does.
        assert delivered == 1
        assert received == [_msg(1)]

    @pytest.mark.asyncio
    async def test_async_handler_awaited_correctly(self) -> None:
        """An async (coroutine) handler must be awaited, not just scheduled."""
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        async def handler(message: dict[str, Any]) -> None:
            # Yield control to prove we are actually inside the event loop.
            await asyncio.sleep(0)
            received.append(message)

        broker.subscribe("topic.async", handler)
        delivered = await broker.publish("topic.async", _msg(42))

        assert delivered == 1
        assert received == [_msg(42)]

    @pytest.mark.asyncio
    async def test_async_handler_failure_does_not_block_other_handlers(self) -> None:
        """An async handler that raises must be caught like a sync one."""
        broker = InProcessBroker()
        received: list[dict[str, Any]] = []

        async def bad_handler(message: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("async boom")

        def good_handler(message: dict[str, Any]) -> None:
            received.append(message)

        broker.subscribe("topic.ar", bad_handler)
        broker.subscribe("topic.ar", good_handler)

        delivered = await broker.publish("topic.ar", _msg(5))

        assert delivered == 1
        assert received == [_msg(5)]
