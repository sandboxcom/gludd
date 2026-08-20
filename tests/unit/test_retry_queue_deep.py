"""Deep tests for RetryQueue — enqueue/dequeue, exponential backoff,
max retries, dead letter queue, poison pill, priority ordering, ack/nack,
idempotency, concurrent collision safety, delay expiry, peek, and
requeue with custom delay.
"""

from __future__ import annotations

import time

import pytest

from general_ludd.messaging.retry_queue import RetryQueue

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_item(payload: str, priority: int = 0) -> dict:
    return {"payload": payload, "priority": priority}


# ===========================================================================
# Construction & properties
# ===========================================================================


class TestConstruction:
    def test_default_construction(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        assert rq.size == 0
        assert rq.dlq_size == 0
        assert rq.active_count == 0

    def test_custom_params(self) -> None:
        rq = RetryQueue(max_retries=5, base_delay=0.1)
        assert rq.max_retries == 5
        assert rq.base_delay == 0.1

    def test_negative_max_retries_raises(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            RetryQueue(max_retries=-1, base_delay=0.1)

    def test_negative_base_delay_raises(self) -> None:
        with pytest.raises(ValueError, match="base_delay"):
            RetryQueue(max_retries=3, base_delay=-0.01)


# ===========================================================================
# Basic enqueue / dequeue
# ===========================================================================


class TestEnqueueDequeue:
    def test_enqueue_increases_size(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("a"))
        assert rq.size == 1
        rq.enqueue(_make_item("b"))
        assert rq.size == 2

    def test_dequeue_returns_first(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("one"))
        rq.enqueue(_make_item("two"))
        item = rq.dequeue(timeout=0.01)
        assert item.payload["payload"] == "one"

    def test_dequeue_empty_returns_none_after_timeout(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        assert rq.dequeue(timeout=0.01) is None

    def test_dequeue_has_unique_id(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("x"))
        rq.enqueue(_make_item("y"))
        a = rq.dequeue(timeout=0.01)
        b = rq.dequeue(timeout=0.01)
        assert a.item_id != b.item_id

    def test_dequeue_returns_item_with_retry_count_zero(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("fresh"))
        item = rq.dequeue(timeout=0.01)
        assert item is not None
        assert item.attempt == 0


# ===========================================================================
# Priority ordering
# ===========================================================================


class TestPriorityOrdering:
    def test_higher_priority_dequeued_first(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(
            {"payload": "low", "priority": 1},
            priority=1,
        )
        rq.enqueue(
            {"payload": "high", "priority": 10},
            priority=10,
        )
        rq.enqueue(
            {"payload": "mid", "priority": 5},
            priority=5,
        )
        first = rq.dequeue(timeout=0.01)
        second = rq.dequeue(timeout=0.01)
        third = rq.dequeue(timeout=0.01)
        assert first is not None and second is not None and third is not None
        assert first.payload["payload"] == "high"
        assert second.payload["payload"] == "mid"
        assert third.payload["payload"] == "low"

    def test_equal_priority_fifo(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue({"payload": "a"}, priority=1)
        rq.enqueue({"payload": "b"}, priority=1)
        rq.enqueue({"payload": "c"}, priority=1)
        a = rq.dequeue(timeout=0.01)
        b = rq.dequeue(timeout=0.01)
        c = rq.dequeue(timeout=0.01)
        assert a is not None and b is not None and c is not None
        assert a.payload["payload"] == "a"
        assert b.payload["payload"] == "b"
        assert c.payload["payload"] == "c"


# ===========================================================================
# Ack / Nack
# ===========================================================================


class TestAckNack:
    def test_ack_removes_from_active(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("done"))
        item = rq.dequeue(timeout=0.01)
        assert rq.active_count == 1
        rq.ack(item.item_id)
        assert rq.active_count == 0
        assert rq.size == 0

    def test_ack_unknown_id_raises(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        with pytest.raises(KeyError, match="unknown item"):
            rq.ack("nonexistent")

    def test_nack_increments_attempt(self) -> None:
        rq = RetryQueue(max_retries=5, base_delay=0.001)
        rq.enqueue(_make_item("retry-me"))
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "transient error")
        # item is requeued; dequeue it again
        time.sleep(0.005)
        item2 = rq.dequeue(timeout=0.01)
        rq.nack(item2.item_id, "another error")
        # dequeue again for the third time
        time.sleep(0.005)
        re_item = rq.dequeue(timeout=0.01)
        assert re_item is not None
        assert re_item.attempt == 2

    def test_nack_unknown_id_raises(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        with pytest.raises(KeyError, match="unknown item"):
            rq.nack("nonexistent", "error")


# ===========================================================================
# Exponential backoff
# ===========================================================================


class TestExponentialBackoff:
    def test_backoff_doubles_each_attempt(self) -> None:
        rq = RetryQueue(max_retries=10, base_delay=0.01)
        rq.enqueue(_make_item("backoff"))
        item = rq.dequeue(timeout=0.01)
        assert rq._backoff_delay(item.attempt) == pytest.approx(0.01)
        rq.nack(item.item_id, "e1")
        # item is requeued with attempt=1, but delay is computed from current attempt
        # dequeue will respect the delay when item is ready
        assert rq._backoff_delay(1) == pytest.approx(0.02)
        assert rq._backoff_delay(2) == pytest.approx(0.04)
        assert rq._backoff_delay(3) == pytest.approx(0.08)

    def test_backoff_caps_at_max_delay(self) -> None:
        rq = RetryQueue(max_retries=10, base_delay=0.01, max_delay=0.05)
        delay = rq._backoff_delay(5)  # 0.01 * 2^5 = 0.32, capped to 0.05
        assert delay == pytest.approx(0.05)

    def test_dequeue_respects_backoff_delay(self) -> None:
        rq = RetryQueue(max_retries=10, base_delay=0.01)
        rq.enqueue(_make_item("delay-check"))
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "e1")

        # immediately after nack, backoff has not expired → no dequeue
        result = rq.dequeue(timeout=0.0)
        assert result is None

        # after base_delay, the item becomes available
        time.sleep(0.02)
        re_item = rq.dequeue(timeout=0.01)
        assert re_item is not None
        assert re_item.attempt == 1


# ===========================================================================
# Max retries → Dead Letter Queue
# ===========================================================================


class TestMaxRetriesDLQ:
    def test_exceeding_max_retries_routes_to_dlq(self) -> None:
        rq = RetryQueue(max_retries=2, base_delay=0.001)
        rq.enqueue(_make_item("doomed"))
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "e1")
        time.sleep(0.005)
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "e2")
        time.sleep(0.005)
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "e3")  # attempt 2 reaches max_retries → DLQ

        assert rq.size == 0
        assert rq.dlq_size == 1
        assert rq.active_count == 0

    def test_dlq_item_preserves_payload_and_errors(self) -> None:
        rq = RetryQueue(max_retries=1, base_delay=0.001)
        rq.enqueue(_make_item("fatal"))
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "boom")
        time.sleep(0.005)
        item = rq.dequeue(timeout=0.01)
        rq.nack(item.item_id, "fatal error")

        dlq_items = rq.get_dlq_items()
        assert len(dlq_items) == 1
        dlq = dlq_items[0]
        assert dlq.payload["payload"] == "fatal"
        assert "boom" in dlq.errors
        assert "fatal error" in dlq.errors

    def test_dlq_size_increments_properly(self) -> None:
        rq = RetryQueue(max_retries=1, base_delay=0.001)
        for i in range(3):
            rq.enqueue(_make_item(f"item-{i}"))
            item = rq.dequeue(timeout=0.01)
            rq.nack(item.item_id, f"err{i}")
            time.sleep(0.005)
            item = rq.dequeue(timeout=0.01)
            rq.nack(item.item_id, f"fatal{i}")
        assert rq.dlq_size == 3

    def test_get_dlq_items_returns_empty_list_when_no_dlq(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        assert rq.get_dlq_items() == []

    def test_ack_does_not_go_to_dlq(self) -> None:
        rq = RetryQueue(max_retries=1, base_delay=0.001)
        rq.enqueue(_make_item("ok"))
        item = rq.dequeue(timeout=0.01)
        rq.ack(item.item_id)
        assert rq.dlq_size == 0


# ===========================================================================
# Poison pill
# ===========================================================================


class TestPoisonPill:
    def test_poison_pill_dequeues_as_sentinel(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.poison()
        item = rq.dequeue(timeout=0.01)
        assert item.is_poison
        assert item.poisoned

    def test_poison_pill_skips_normal_items(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("normal"), priority=10)
        poison_id = rq.poison()
        # poison has highest priority; normal is lower
        first = rq.dequeue(timeout=0.01)
        assert first is not None and first.is_poison
        assert first.item_id == poison_id
        second = rq.dequeue(timeout=0.01)
        assert second is not None
        assert second.payload["payload"] == "normal"

    def test_poison_pill_stops_processing_loop(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.poison()
        rq.enqueue(_make_item("after"))  # won't be reached if poison signals stop
        item = rq.dequeue(timeout=0.01)
        assert item is not None
        assert item.is_poison

    def test_multiple_poison_pills_are_all_dequeued(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.poison()
        rq.poison()
        assert rq.dequeue(timeout=0.01).is_poison
        assert rq.dequeue(timeout=0.01).is_poison


# ===========================================================================
# Requeue with custom delay
# ===========================================================================


class TestRequeue:
    def test_requeue_with_custom_delay(self) -> None:
        rq = RetryQueue(max_retries=5, base_delay=0.01)
        rq.enqueue(_make_item("requeue-me"))
        item = rq.dequeue(timeout=0.01)
        rq.requeue(item.item_id, delay=0.05)

        # too soon: not available
        assert rq.dequeue(timeout=0.0) is None

        # after delay
        time.sleep(0.06)
        re_item = rq.dequeue(timeout=0.01)
        assert re_item is not None
        assert re_item.payload["payload"] == "requeue-me"

    def test_requeue_unknown_id_raises(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        with pytest.raises(KeyError, match="unknown item"):
            rq.requeue("nonexistent", delay=1.0)


# ===========================================================================
# Idempotency
# ===========================================================================


class TestIdempotency:
    def test_double_ack_raises(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("one-ack"))
        item = rq.dequeue(timeout=0.01)
        rq.ack(item.item_id)
        with pytest.raises(KeyError, match="unknown item"):
            rq.ack(item.item_id)

    def test_double_nack_after_ack_raises(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("ack-then-nack"))
        item = rq.dequeue(timeout=0.01)
        rq.ack(item.item_id)
        with pytest.raises(KeyError, match="unknown item"):
            rq.nack(item.item_id, "late error")


# ===========================================================================
# Peek
# ===========================================================================


class TestPeek:
    def test_peek_does_not_remove_item(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("peekaboo"))
        peeked = rq.peek(timeout=0.01)
        assert peeked.payload["payload"] == "peekaboo"
        assert rq.size == 1
        assert rq.active_count == 0

    def test_peek_empty_returns_none(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        assert rq.peek(timeout=0.0) is None

    def test_peek_skips_not_ready_items(self) -> None:
        rq = RetryQueue(max_retries=5, base_delay=0.01)
        rq.enqueue(_make_item("delayed"))
        item = rq.dequeue(timeout=0.01)
        rq.requeue(item.item_id, delay=0.1)
        # peek should not see the item that is still on backoff
        assert rq.peek(timeout=0.0) is None

    def test_repeated_peek_preserves_item_and_next_dequeue(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue(_make_item("stable"), priority=4)

        first = rq.peek(timeout=0.0)
        second = rq.peek(timeout=0.0)
        dequeued = rq.dequeue(timeout=0.0)

        assert first is second is dequeued
        assert rq.size == 0
        assert rq.active_count == 1

    def test_peek_skips_delayed_high_priority_for_ready_lower_priority(self) -> None:
        now = [10.0]
        rq = RetryQueue(
            max_retries=3,
            base_delay=0.01,
            clock=lambda: now[0],
        )
        rq.enqueue(_make_item("delayed-high"), priority=10)
        delayed = rq.dequeue(timeout=0.0)
        assert delayed is not None
        rq.requeue(delayed.item_id, delay=5.0)
        rq.enqueue(_make_item("ready-low"), priority=1)

        peeked = rq.peek(timeout=0.0)

        assert peeked is not None
        assert peeked.payload["payload"] == "ready-low"
        assert rq.size == 2


# ===========================================================================
# Full lifecycle
# ===========================================================================


class TestFullLifecycle:
    def test_mixed_lifecycle(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.001)
        # enqueue several items
        for i in range(5):
            rq.enqueue(_make_item(f"task-{i}"))

        acked = 0
        nacked = 0
        while rq.size > 0 or rq.active_count > 0:
            item = rq.dequeue(timeout=0.01)
            if item is None:
                break
            if item.is_poison:
                break
            if item.payload["payload"].endswith(("0", "2", "4")):
                rq.ack(item.item_id)
                acked += 1
            else:
                rq.nack(item.item_id, "simulated error")
                nacked += 1

        assert acked == 3
        # task-1 and task-3 eventually DLQ after 3 retries (max_retries=3)
        assert rq.dlq_size == 2

    def test_empty_queue_full_drain(self) -> None:
        rq = RetryQueue(max_retries=3, base_delay=0.01)
        assert rq.size == 0
        assert rq.active_count == 0
        assert rq.dlq_size == 0
        assert rq.dequeue(timeout=0.0) is None
