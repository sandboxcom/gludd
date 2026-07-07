"""In-memory ring buffer source for pushed observability records (#82).

``WebhookBufferSource`` is the *buffer* half of the push receiver: the daemon
``/api/ingest`` endpoint normalizes a pushed payload (see
:mod:`general_ludd.connectors.ingest`) and pushes the resulting records into one
of these. It satisfies the same duck-typed connector contract as the *pulled*
sources (``KIND``, ``name``, ``health()``, ``query(spec)``), so the
``/api/observe`` fan-out reads pushed data exactly as it reads a Prometheus or
GitHub-Actions source — no special-casing.

Design:

* Backed by a ``collections.deque(maxlen=...)`` — a bounded ring. Once full, the
  oldest record is evicted on each new append, so memory is capped regardless of
  push volume.
* **Thread-safe**: every mutation and read is guarded by a single lock, because
  the receiver may push from multiple request handlers while ``/api/observe``
  reads concurrently.
* Self-contained — imports nothing from sibling connectors or a base class. The
  records it stores are plain dicts matching the normalized-record schema.
"""

from __future__ import annotations

import collections.abc
import threading
from collections import deque
from copy import deepcopy
from typing import cast

# Default record kind for a webhook/push buffer (matches the LOG_KIND marker).
_DEFAULT_KIND = "logs"
_DEFAULT_MAXLEN = 1000


class WebhookBufferSource:
    """A bounded, thread-safe ring of pushed normalized records (duck-typed source)."""

    KIND: str = _DEFAULT_KIND

    def __init__(
        self,
        *,
        name: str = "webhook-buffer",
        maxlen: int = _DEFAULT_MAXLEN,
        kind: str = _DEFAULT_KIND,
    ) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self.name: str = name
        # Per-instance KIND (default 'logs') without mutating the class attr.
        self.KIND = kind
        self._maxlen = int(maxlen)
        self._buffer: deque[dict[str, object]] = deque(maxlen=self._maxlen)
        self._lock = threading.Lock()

    # -- capacity ---------------------------------------------------------- #
    @property
    def capacity(self) -> int:
        """The ring's maximum size (``maxlen``)."""
        return self._maxlen

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- ingest ------------------------------------------------------------ #
    def push_one(self, record: dict[str, object]) -> bool:
        """Append a single record. Non-dict input is ignored (returns ``False``)."""
        if not isinstance(record, dict):
            return False
        with self._lock:
            self._buffer.append(record)
        return True

    def push(self, records: object) -> int:
        """Append an iterable of records; non-dict items are skipped.

        Returns the number of records actually stored. Never raises on a
        non-iterable (returns ``0``).
        """
        try:
            iterator = iter(cast(collections.abc.Iterable[dict[str, object]], records))
        except TypeError:
            return 0
        accepted = 0
        with self._lock:
            for record in iterator:
                if isinstance(record, dict):
                    self._buffer.append(record)
                    accepted += 1
        return accepted

    # -- read -------------------------------------------------------------- #
    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Return buffered records (deep-copied), filtered by ``spec``.

        Supported filters:

        * ``kind``  — keep records whose ``kind`` equals this value.
        * ``kinds`` — keep records whose ``kind`` is in this collection.
        * ``since`` — keep records whose ``ts`` is a number ``>= since``;
          records with no (``None``) ts are excluded when ``since`` is set.

        Never raises. Returned dicts are deep copies, so a caller mutating them
        cannot corrupt the buffer.
        """
        spec = spec or {}
        with self._lock:
            snapshot = list(self._buffer)

        kind = spec.get("kind")
        kinds = spec.get("kinds")
        kinds_set: set[object] | None = set(kinds) if isinstance(kinds, list) else None
        since = spec.get("since")
        since_f: float | None = None
        if since is not None:
            try:
                since_f = float(str(since))
            except (TypeError, ValueError):
                since_f = None

        out: list[dict[str, object]] = []
        for record in snapshot:
            if kind is not None and record.get("kind") != kind:
                continue
            if kinds_set is not None and record.get("kind") not in kinds_set:
                continue
            if since_f is not None:
                ts = record.get("ts")
                if not isinstance(ts, (int, float)) or float(ts) < since_f:
                    continue
            out.append(deepcopy(record))
        return out

    # -- health ------------------------------------------------------------ #
    def health(self) -> dict[str, object]:
        """Return a health dict. Never raises."""
        with self._lock:
            size = len(self._buffer)
        return {
            "ok": True,
            "source": self.name,
            "size": size,
            "capacity": self._maxlen,
        }
