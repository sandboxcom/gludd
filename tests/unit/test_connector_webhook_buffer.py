"""Unit tests for ``connectors/webhook_buffer.py``.

``WebhookBufferSource`` is a bounded, thread-safe, in-memory ring that the
push-side ingest endpoint feeds normalized records into. It satisfies the
duck-typed connector contract (``KIND``, ``name``, ``health()``,
``query(spec)``) so ``/api/observe`` can read pushed data alongside pulled
sources.

Contract under test:

* ``KIND`` defaults to ``'logs'``; ``name`` is set.
* ``push(records)`` / ``push_one(record)`` append to a ``deque(maxlen=...)`` —
  oldest records are evicted at capacity (ring semantics).
* ``query(spec)`` returns the buffered records, filterable by ``kind`` and
  ``since`` (epoch seconds).
* ``health()`` never raises and reports size/capacity.
* concurrent appends from many threads never lose or corrupt records (lock).
"""

from __future__ import annotations

import threading
from typing import Any, cast

from general_ludd.connectors.webhook_buffer import WebhookBufferSource


def _rec(**over: object) -> dict:
    base: dict = {
        "ts": 100.0,
        "source": "webhook",
        "kind": "logs",
        "level_or_status": "info",
        "message": "m",
        "value": None,
        "labels": {},
        "raw": None,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Contract / identity
# --------------------------------------------------------------------------- #
def test_default_kind_is_logs_and_name_set() -> None:
    src = WebhookBufferSource()
    assert src.KIND == "logs"
    assert isinstance(src.name, str)
    assert src.name


def test_custom_name_and_maxlen() -> None:
    src = WebhookBufferSource(name="pushed", maxlen=5)
    assert src.name == "pushed"
    assert src.capacity == 5


def test_health_never_raises_and_reports_size() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push([_rec(), _rec()])
    health = src.health()
    assert isinstance(health, dict)
    assert health["ok"] is True
    assert health["size"] == 2
    assert health["capacity"] == 10


# --------------------------------------------------------------------------- #
# Ring semantics
# --------------------------------------------------------------------------- #
def test_ring_evicts_oldest_at_maxlen() -> None:
    src = WebhookBufferSource(maxlen=3)
    for i in range(5):
        src.push_one(_rec(message=f"m{i}", ts=float(i)))
    recs = src.query({})
    assert len(recs) == 3
    # oldest two (m0, m1) evicted; m2, m3, m4 remain in insertion order
    assert [r["message"] for r in recs] == ["m2", "m3", "m4"]


def test_push_accepts_iterable_of_records() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push([_rec(message="a"), _rec(message="b")])
    assert {r["message"] for r in src.query({})} == {"a", "b"}


def test_push_ignores_non_dict_items() -> None:
    src = WebhookBufferSource(maxlen=10)
    cast(Any, src).push([_rec(message="ok"), "garbage", 42, None])
    recs = src.query({})
    assert len(recs) == 1
    assert recs[0]["message"] == "ok"


# --------------------------------------------------------------------------- #
# query filtering
# --------------------------------------------------------------------------- #
def test_query_filters_by_kind() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push(
        [
            _rec(kind="logs", message="L"),
            _rec(kind="alerts", message="A"),
            _rec(kind="traces", message="T"),
        ]
    )
    only_alerts = src.query({"kind": "alerts"})
    assert [r["message"] for r in only_alerts] == ["A"]


def test_query_filters_by_kinds_list() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push(
        [
            _rec(kind="logs", message="L"),
            _rec(kind="alerts", message="A"),
            _rec(kind="traces", message="T"),
        ]
    )
    got = src.query({"kinds": ["logs", "traces"]})
    assert sorted(r["message"] for r in got) == ["L", "T"]


def test_query_filters_by_since() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push(
        [
            _rec(ts=10.0, message="old"),
            _rec(ts=200.0, message="new"),
            _rec(ts=None, message="undated"),
        ]
    )
    got = src.query({"since": 100.0})
    # only the record with ts >= 100 survives; undated (ts=None) excluded by since
    assert [r["message"] for r in got] == ["new"]


def test_query_no_spec_returns_all_in_order() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push([_rec(message="a", ts=1.0), _rec(message="b", ts=2.0)])
    assert [r["message"] for r in src.query({})] == ["a", "b"]


def test_query_returns_copies_not_internal_refs() -> None:
    src = WebhookBufferSource(maxlen=10)
    src.push_one(_rec(message="x"))
    out = src.query({})
    out[0]["message"] = "mutated"
    # mutating the returned list must not corrupt the buffer
    assert src.query({})[0]["message"] == "x"


# --------------------------------------------------------------------------- #
# thread safety
# --------------------------------------------------------------------------- #
def test_concurrent_appends_are_thread_safe() -> None:
    src = WebhookBufferSource(maxlen=10_000)
    n_threads = 8
    per_thread = 500

    def worker(tid: int) -> None:
        for i in range(per_thread):
            src.push_one(_rec(message=f"{tid}-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    recs = src.query({})
    assert len(recs) == n_threads * per_thread
    # every record is well-formed (no torn writes)
    assert all(isinstance(r, dict) and "message" in r for r in recs)


def test_concurrent_push_and_query_do_not_raise() -> None:
    src = WebhookBufferSource(maxlen=1000)
    stop = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            while not stop.is_set():
                src.push_one(_rec())
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(2000):
                src.query({"kind": "logs"})
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    r.join()
    stop.set()
    w.join()
    assert errors == []
