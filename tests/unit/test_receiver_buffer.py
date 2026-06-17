"""Unit tests for ReceiverBuffer: bounding, overflow policy, retention, drain."""

from __future__ import annotations

import time

import pytest

from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer


def _rec(i: int) -> dict[str, object]:
    return {"message": f"m{i}", "kind": "log"}


class TestBounding:
    def test_drop_oldest_evicts_when_full(self) -> None:
        buf = ReceiverBuffer(maxlen=3, overflow=OverflowPolicy.DROP_OLDEST)
        for i in range(5):
            assert buf.offer(_rec(i)) is True
        assert len(buf) == 3
        drained = buf.drain()
        # Oldest two (0,1) were evicted; FIFO returns 2,3,4.
        assert [r["message"] for r in drained] == ["m2", "m3", "m4"]
        assert buf.total_dropped == 2
        assert buf.total_offered == 5

    def test_reject_refuses_when_full(self) -> None:
        buf = ReceiverBuffer(maxlen=2, overflow=OverflowPolicy.REJECT)
        assert buf.offer(_rec(0)) is True
        assert buf.offer(_rec(1)) is True
        assert buf.offer(_rec(2)) is False  # full -> rejected
        assert len(buf) == 2
        assert buf.total_rejected == 1
        # Nothing already buffered is lost.
        assert [r["message"] for r in buf.drain()] == ["m0", "m1"]

    def test_is_full(self) -> None:
        buf = ReceiverBuffer(maxlen=1, overflow=OverflowPolicy.REJECT)
        assert buf.is_full() is False
        buf.offer(_rec(0))
        assert buf.is_full() is True

    def test_maxlen_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            ReceiverBuffer(maxlen=0)


class TestDrain:
    def test_drain_fifo_order(self) -> None:
        buf = ReceiverBuffer(maxlen=10)
        for i in range(4):
            buf.offer(_rec(i))
        assert [r["message"] for r in buf.drain()] == ["m0", "m1", "m2", "m3"]
        assert len(buf) == 0

    def test_drain_with_limit(self) -> None:
        buf = ReceiverBuffer(maxlen=10)
        for i in range(4):
            buf.offer(_rec(i))
        first_two = buf.drain(limit=2)
        assert [r["message"] for r in first_two] == ["m0", "m1"]
        assert len(buf) == 2

    def test_offer_many_returns_accepted_count(self) -> None:
        buf = ReceiverBuffer(maxlen=2, overflow=OverflowPolicy.REJECT)
        accepted = buf.offer_many([_rec(0), _rec(1), _rec(2)])
        assert accepted == 2


class TestRetention:
    def test_age_retention_evicts_old_records(self) -> None:
        buf = ReceiverBuffer(maxlen=10, retention_s=0.05)
        buf.offer(_rec(0))
        time.sleep(0.08)
        buf.offer(_rec(1))
        # The first record aged out; only the fresh one remains.
        remaining = buf.drain()
        assert [r["message"] for r in remaining] == ["m1"]
        assert buf.total_dropped >= 1

    def test_no_retention_keeps_everything(self) -> None:
        buf = ReceiverBuffer(maxlen=10, retention_s=None)
        buf.offer(_rec(0))
        time.sleep(0.02)
        assert len(buf) == 1


class TestSnapshot:
    def test_snapshot_is_most_recent_first_and_nondestructive(self) -> None:
        buf = ReceiverBuffer(maxlen=10)
        for i in range(3):
            buf.offer(_rec(i))
        snap = buf.snapshot()
        assert [r["message"] for r in snap["recent"]] == ["m2", "m1", "m0"]
        # Non-destructive.
        assert len(buf) == 3
        assert snap["size"] == 3
        assert snap["maxlen"] == 10

    def test_snapshot_counters(self) -> None:
        buf = ReceiverBuffer(maxlen=1, overflow=OverflowPolicy.REJECT)
        buf.offer(_rec(0))
        buf.offer(_rec(1))  # rejected
        snap = buf.snapshot()
        assert snap["total_offered"] == 2
        assert snap["total_rejected"] == 1
        assert snap["overflow"] == "reject"

    def test_snapshot_limit(self) -> None:
        buf = ReceiverBuffer(maxlen=10)
        for i in range(5):
            buf.offer(_rec(i))
        snap = buf.snapshot(limit=2)
        assert len(snap["recent"]) == 2

    def test_clear(self) -> None:
        buf = ReceiverBuffer(maxlen=10)
        buf.offer(_rec(0))
        buf.clear()
        assert len(buf) == 0
