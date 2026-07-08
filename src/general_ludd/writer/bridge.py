"""Bridge between HTTP workers and the writer subprocess (B3.1.3 Slice 2).

The classes here let router handlers run unchanged in both inline mode (the
pre-beta.3 single-process daemon, where ``app.state._session_factory`` opens
mutating DB sessions directly) and queued mode (the beta.3 gunicorn
multi-worker architecture, where HTTP workers must NOT open mutating sessions
because the dedicated writer subprocess owns all DB writes).

Design
------

:class:`QueueWriteSession` mirrors the ``put → commit/rollback`` shape of a
SQLAlchemy session but never touches the database. Instead it buffers
:class:`~general_ludd.ipc.queue.Envelope` objects and flushes them to a
:class:`~general_ludd.ipc.queue.WriteQueue` on commit. Overflow behaviour is
delegated to the underlying queue:

* ``DROP_OLDEST`` always accepts (silently evicting the oldest queued envelope
  to make room) and ``commit`` returns the count flushed;
* ``REJECT`` refuses the new envelope when full and ``commit`` raises
  :class:`QueueFullError` so the caller can apply backpressure (HTTP 503,
  retry, etc.). The pending buffer is preserved across a REJECT failure so
  the caller can retry the whole batch once the queue drains.

:func:`enqueue_or_commit` is the router-facing helper. It branches on
``app.state._write_queue``:

* set  → enqueue via :class:`QueueWriteSession`, return ``(True, 202)``;
* unset → invoke the optional ``inline_commit`` callback (the router's
  existing direct-session path) and return ``(False, 200)``.

A later slice replaces the ``factory is None`` / ``async with factory()``
blocks inside router handlers with a single ``enqueue_or_commit`` call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI

from general_ludd.ipc import Envelope, WriteQueue

__all__ = [
    "HTTP_ENQUEUED",
    "HTTP_INLINE_COMMIT",
    "QueueFullError",
    "QueueWriteSession",
    "enqueue_or_commit",
]

# HTTP status codes returned by enqueue_or_commit. Exposed as named constants
# so router code reads ``status == HTTP_ENQUEUED`` rather than a bare 202.
# 202 Accepted  — request queued for the writer subprocess; not yet applied.
# 200 OK        — inline mode: the supplied commit callback applied the write
#                 synchronously and the caller should return the result body.
HTTP_ENQUEUED = 202
HTTP_INLINE_COMMIT = 200


class QueueFullError(RuntimeError):
    """Raised by :meth:`QueueWriteSession.commit` when the underlying queue
    refused an envelope under :attr:`~general_ludd.ipc.OverflowPolicy.REJECT`.

    The pending buffer is left intact so the caller can retry the batch once
    the writer subprocess drains the queue. Do NOT swallow this silently —
    apply explicit backpressure (HTTP 503, retry-after) at the router layer.
    """


class QueueWriteSession:
    """Session-shaped buffer that flushes writes to a :class:`WriteQueue`.

    Use this in HTTP workers (queued mode) in place of a real DB session.
    Each :meth:`put` buffers a single :class:`Envelope` tagged with the
    session's ``topic``; :meth:`commit` drains the buffer onto the queue in
    FIFO order, applying the queue's overflow policy per envelope.

    Example::

        session = QueueWriteSession(topic="todo.create", queue=app.state._write_queue)
        await session.put({"title": "ship beta.3", "priority": 1})
        await session.commit()  # → envelope lands on the writer's queue

    The session is single-use: after :meth:`commit` or :meth:`rollback` the
    pending buffer is empty and the instance can be discarded (or reused for
    a fresh batch — there is no internal state beyond ``_pending``).
    """

    def __init__(self, topic: str, queue: WriteQueue) -> None:
        self._topic = topic
        self._queue = queue
        self._pending: list[Envelope] = []

    @property
    def topic(self) -> str:
        """The topic stamped onto every envelope this session produces."""
        return self._topic

    @property
    def pending(self) -> tuple[Envelope, ...]:
        """Snapshot of buffered envelopes not yet flushed to the queue.

        Returned as a tuple so callers cannot mutate the internal buffer
        directly. Empty after :meth:`commit` succeeds or :meth:`rollback`
        runs; preserved on a :class:`QueueFullError` for retry.
        """
        return tuple(self._pending)

    async def put(self, payload: dict[str, Any]) -> None:
        """Buffer a single write as an :class:`Envelope`.

        Does NOT touch the underlying queue — the envelope is held in
        ``pending`` until :meth:`commit` flushes it. This mirrors the
        transactional put/commit shape of a DB session so router code
        transfers mechanically.

        ``payload`` is stored by reference; do NOT mutate the dict after
        calling ``put`` (the writer subprocess will see your mutation).
        """
        self._pending.append(Envelope(topic=self._topic, payload=dict(payload)))

    async def commit(self) -> int:
        """Flush every pending envelope to the queue. Returns the count flushed.

        Overflow handling delegates to the underlying :class:`WriteQueue`:

        * ``DROP_OLDEST`` — every envelope is accepted (the queue evicts the
          oldest silently when full); ``total_dropped`` on the queue reflects
          the eviction count;
        * ``REJECT`` — the first envelope that does not fit raises
          :class:`QueueFullError`. The pending buffer is LEFT INTACT on
          failure so the caller can retry the whole batch after backoff.

        On success the pending buffer is cleared and the count is returned.
        """
        flushed = 0
        # Iterate by index so REJECT can leave the remaining envelopes in
        # self._pending for the caller's retry. We slice off the successfully
        # flushed prefix only after the loop completes without raising.
        for _index, envelope in enumerate(list(self._pending)):
            accepted = await self._queue.put(envelope)
            if not accepted:
                # REJECT refused this envelope. Keep pending as
                # "everything from this envelope onward" so retry is whole.
                self._pending = self._pending[_index:]
                raise QueueFullError(
                    f"write queue rejected envelope on topic {self._topic!r} "
                    f"(queue full; {len(self._pending)} envelope(s) pending retry)"
                )
            flushed += 1
        # All flushed — drop the entire pending buffer.
        self._pending.clear()
        return flushed

    async def rollback(self) -> None:
        """Discard every buffered envelope without flushing.

        Use when validation fails mid-handler after one or more ``put`` calls.
        Never raises — rollback must always leave the session in a clean state
        so the router can return its error response without a resource leak.
        """
        self._pending.clear()


async def enqueue_or_commit(
    app: FastAPI,
    topic: str,
    payload: dict[str, Any],
    inline_commit: Callable[[], Awaitable[Any]] | None = None,
) -> tuple[bool, int]:
    """Branch a router write between queued mode and inline mode.

    Inspects ``app.state._write_queue``:

    * **set** (queued / beta.3 multi-worker) → buffer ``payload`` onto a
      one-shot :class:`QueueWriteSession` against ``queue``, commit it, and
      return ``(True, HTTP_ENQUEUED)`` — i.e. ``(True, 202)``. The router
      should respond HTTP 202 with an ``{"status": "queued"}`` body.
    * **unset** (inline / single-process back-compat) → invoke the optional
      ``inline_commit`` callback (the router's existing direct-session path)
      and return ``(False, HTTP_INLINE_COMMIT)`` — i.e. ``(False, 200)``.
      The router then returns its normal 200/201 response body.

    The ``inline_commit`` callback is a coroutine factory taking no args —
    bind the session + repo + payload in a closure at the router layer::

        async def _inline() -> None:
            async with factory() as session:
                repo = TodoRepository(session)
                await repo.create(todo_data=payload)
                await session.commit()

        enqueued, status = await enqueue_or_commit(
            app, topic="todo.create", payload=payload, inline_commit=_inline,
        )

    When ``inline_commit`` is None and the queue is absent, the helper returns
    ``(False, 200)`` without doing anything — the caller owns the write.
    """
    queue: WriteQueue | None = getattr(app.state, "_write_queue", None)
    if queue is None:
        if inline_commit is not None:
            await inline_commit()
        return False, HTTP_INLINE_COMMIT

    session = QueueWriteSession(topic=topic, queue=queue)
    await session.put(payload)
    await session.commit()
    return True, HTTP_ENQUEUED
