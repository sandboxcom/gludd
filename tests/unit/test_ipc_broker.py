"""Structural tests for ipc/broker.py — Broker Protocol, InProcessBroker, Message, Handler."""

from __future__ import annotations

import asyncio

from general_ludd.ipc.broker import Broker, Handler, InProcessBroker, Message


class TestBroker:
    def test_is_runtime_checkable(self):
        assert isinstance(InProcessBroker(), Broker)

    def test_has_publish(self):
        assert hasattr(Broker, "publish")

    def test_has_subscribe(self):
        assert hasattr(Broker, "subscribe")

    def test_has_unsubscribe(self):
        assert hasattr(Broker, "unsubscribe")

    def test_has_clear(self):
        assert hasattr(Broker, "clear")


class TestInProcessBroker:
    def test_instantiation_no_args(self):
        b = InProcessBroker()
        assert b is not None

    def test_publish_no_subscribers_returns_zero(self):
        b = InProcessBroker()
        result = asyncio.run(b.publish("test", {"key": "value"}))
        assert result == 0

    def test_subscribe_and_publish(self):
        b = InProcessBroker()
        received: list[Message] = []
        b.subscribe("test.topic", lambda m: received.append(m))
        result = asyncio.run(b.publish("test.topic", {"msg": "hello"}))
        assert result == 1
        assert received == [{"msg": "hello"}]

    def test_unsubscribe_removes_handler(self):
        b = InProcessBroker()
        handler = lambda m: None
        b.subscribe("test.topic", handler)
        b.unsubscribe("test.topic", handler)
        result = asyncio.run(b.publish("test.topic", {"k": "v"}))
        assert result == 0

    def test_clear_drops_all_subscribers(self):
        b = InProcessBroker()
        b.subscribe("t1", lambda m: None)
        b.subscribe("t2", lambda m: None)
        b.clear()
        r1 = asyncio.run(b.publish("t1", {}))
        r2 = asyncio.run(b.publish("t2", {}))
        assert r1 == 0
        assert r2 == 0


class TestMessageTypeAlias:
    def test_message_is_dict(self):
        m: Message = {"key": "value"}
        assert isinstance(m, dict)
        assert m["key"] == "value"


class TestHandlerTypeAlias:
    def test_handler_is_callable(self):
        h: Handler = lambda m: len(m)
        assert callable(h)
        assert h({"a": 1, "b": 2}) == 2
