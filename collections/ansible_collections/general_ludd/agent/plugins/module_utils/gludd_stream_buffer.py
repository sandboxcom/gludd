"""Rolling byte/frame buffer for the gludd_stream module.

Kept in ``module_utils`` (rather than inside the module file) so it can be
unit-tested without ``ansible`` installed. The module imports this via the
standard collection path:

    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd_stream_buffer import (
        RollingBuffer,
    )

The buffer is a simple bounded byte sink: ``push`` appends chunks, and when
the total exceeds ``max_bytes`` the oldest bytes are discarded so that
``len(buffer) <= max_bytes`` always holds. ``drain`` returns and clears the
current contents; ``peek`` returns them without clearing.
"""

from __future__ import annotations

from collections import deque
from typing import List


class RollingBuffer:
    """A bounded byte buffer that discards the oldest data when full.

    Parameters
    ----------
    max_bytes:
        Maximum number of bytes/frames to retain. Once exceeded, the oldest
        bytes are discarded on subsequent pushes.

    Examples
    --------
    >>> buf = RollingBuffer(max_bytes=8)
    >>> buf.push(b"hello")
    >>> len(buf)
    5
    >>> buf.push(b"world!!!!!")
    >>> len(buf)
    8
    >>> buf.peek()
    b'oworld!!'
    >>> buf.drain()
    b'oworld!!'
    >>> len(buf)
    0
    """

    def __init__(self, max_bytes: int = 1048576) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size: int = 0

    def push(self, data: bytes) -> None:
        """Append ``data`` to the buffer, evicting oldest bytes if needed."""
        if not data:
            return
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("RollingBuffer.push expects bytes, got %r" % type(data))
        self._chunks.append(bytes(data))
        self._size += len(data)
        self._evict()

    def _evict(self) -> None:
        """Drop oldest chunks until ``self._size <= self.max_bytes``."""
        while self._size > self.max_bytes and self._chunks:
            oldest = self._chunks.popleft()
            excess = self._size - self.max_bytes
            if excess >= len(oldest):
                self._size -= len(oldest)
            else:
                # Keep the tail of the oldest chunk.
                keep = oldest[excess:]
                self._chunks.appendleft(keep)
                self._size -= excess

    def peek(self) -> bytes:
        """Return the current buffer contents as a single ``bytes`` object."""
        return b"".join(self._chunks)

    def peek_head(self, n: int) -> bytes:
        """Return the first ``n`` bytes non-destructively (fewer if buffer is smaller)."""
        if n <= 0:
            return b""
        full = self.peek()
        return full[:n]

    def peek_tail(self, n: int) -> bytes:
        """Return the last ``n`` bytes non-destructively (fewer if buffer is smaller)."""
        if n <= 0:
            return b""
        full = self.peek()
        return full[max(0, len(full) - n):]

    def find_key(self, key: bytes) -> int | None:
        """Return the byte offset of the first occurrence of ``key``, or None.

        Returns 0 when ``key`` matches at the very start of the buffer.
        Returns None when ``key`` is empty, absent, or longer than the buffer.
        """
        if not key:
            return None
        full = self.peek()
        idx = full.find(key)
        if idx < 0:
            return None
        return idx

    def split_at(self, offset: int) -> tuple[bytes, bytes]:
        """Split the buffer at ``offset``: return (head, tail) and reset to tail.

        ``head`` is ``buffer[:offset]`` and ``tail`` is ``buffer[offset:]``. After
        the call the buffer holds only ``tail``. A negative or out-of-range
        ``offset`` is clamped to ``[0, len(buffer)]``.
        """
        full = self.peek()
        if offset < 0:
            offset = 0
        if offset > len(full):
            offset = len(full)
        head, tail = full[:offset], full[offset:]
        self._chunks.clear()
        if tail:
            self._chunks.append(tail)
        self._size = len(tail)
        return head, tail

    def drain(self) -> bytes:
        """Return the buffer contents and reset the buffer to empty."""
        out = self.peek()
        self._chunks.clear()
        self._size = 0
        return out

    def __len__(self) -> int:
        return self._size

    @property
    def size(self) -> int:
        """Current number of bytes retained."""
        return self._size

    def chunk_paths(self) -> List[str]:  # pragma: no cover - convenience accessor
        """Placeholder for module-side artifact tracking; unused by core tests."""
        return []
