"""Writer package — bridge between HTTP workers and the writer subprocess.

B3.1.3 Slice 2 introduces :class:`QueueWriteSession` and :func:`enqueue_or_commit`,
the indirection layer that lets the SAME router code run in both:

* **inline mode** (single-process / pre-beta.3): routers open a real DB session
  via ``app.state._session_factory`` and commit directly;
* **queued mode** (gunicorn multi-worker, beta.3+): HTTP workers cannot safely
  open mutating DB sessions (the writer subprocess owns those), so routers
  enqueue writes through ``app.state._write_queue`` instead.

The helper :func:`enqueue_or_commit` branches on ``app.state._write_queue`` so
a router does not need to know which mode it is in. Slice 2 ships only the
bridge — router wiring (Slice 3) consumes it next.
"""

from __future__ import annotations

from general_ludd.writer.bridge import (
    HTTP_ENQUEUED,
    HTTP_INLINE_COMMIT,
    QueueFullError,
    QueueWriteSession,
    enqueue_or_commit,
)
from general_ludd.writer.process import WriterProcess

__all__ = [
    "HTTP_ENQUEUED",
    "HTTP_INLINE_COMMIT",
    "QueueFullError",
    "QueueWriteSession",
    "WriterProcess",
    "enqueue_or_commit",
]
