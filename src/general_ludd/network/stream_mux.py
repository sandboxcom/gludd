"""Multiplex framed streams with flow control and ordered delivery.

Core types:
  Frame       — header + payload unit of the wire protocol
  StreamState — per-stream tracking (send/recv windows, buf, fin)
  StreamMux   — connection-level mux: framing, demux, flow control
"""

from __future__ import annotations

import enum
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _monotonic_now() -> float:
    return time.monotonic()


# ── wire constants ────────────────────────────────────────────────────────────
_HEADER_FMT = ">IHBH"  # stream_id(4) seq(2) flags(1) payload_len(2)
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 9 bytes
_MAX_PAYLOAD = 65535


class FrameFlags(enum.IntFlag):
    """Define control bits in the multiplexing frame header."""

    NONE = 0
    SYN = 1 << 0
    FIN = 1 << 1
    RST = 1 << 2
    ACK = 1 << 3
    WINDOW_UPDATE = 1 << 4


@dataclass
class Frame:
    """Represent one header and payload unit of the wire protocol."""

    stream_id: int
    seq: int
    flags: FrameFlags
    payload: bytes = b""

    def encode(self) -> bytes:
        """Encode the frame into its wire representation."""
        header = struct.pack(
            _HEADER_FMT,
            self.stream_id & 0xFFFF_FFFF,
            self.seq & 0xFFFF,
            self.flags & 0xFF,
            len(self.payload) & 0xFFFF,
        )
        return header + self.payload

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        """Decode one frame from a complete wire representation."""
        if len(data) < _HEADER_SIZE:
            raise ValueError(f"frame too short: {len(data)} bytes (need {_HEADER_SIZE})")
        sid, seq, flags_byte, plen = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
        payload = data[_HEADER_SIZE : _HEADER_SIZE + plen]
        return cls(stream_id=sid, seq=seq, flags=FrameFlags(flags_byte), payload=payload)


# ── stream-level state & flow control ─────────────────────────────────────────


@dataclass
class _FlowWindow:
    size: int = 0
    _consumed: int = 0

    def available(self) -> int:
        return max(0, self.size - self._consumed)

    def consume(self, n: int) -> None:
        self._consumed += n

    def advance(self, delta: int) -> None:
        self._consumed = max(0, self._consumed - delta)


@dataclass
class StreamState:
    """Track send, receive, flow-control, and closure state for one stream."""

    stream_id: int
    send_window: int
    recv_window: int

    _send_buf: list[Frame] = field(default_factory=list)
    _recv_buf: dict[int, bytes] = field(default_factory=dict)
    _send_seq: int = 0
    _recv_seq: int = 0
    _send_flow: _FlowWindow = field(init=False, default_factory=_FlowWindow)
    _recv_flow: _FlowWindow = field(init=False, default_factory=_FlowWindow)
    _send_closed: bool = False
    _recv_closed: bool = False
    _rst: bool = False
    _error: str | None = None
    opened_at: float = field(default_factory=_monotonic_now)

    def __post_init__(self) -> None:
        """Size the independent flow windows from the public limits."""
        self._send_flow.size = self.send_window
        self._recv_flow.size = self.recv_window

    @property
    def sendable(self) -> bool:
        """Return whether the stream accepts outbound payloads."""
        return not self._send_closed and not self._rst

    @property
    def receivable(self) -> bool:
        """Return whether the stream accepts inbound payloads."""
        return not self._recv_closed and not self._rst

    @property
    def is_closed(self) -> bool:
        """Return whether both directions are closed."""
        return self._send_closed and self._recv_closed

    @property
    def is_rst(self) -> bool:
        """Return whether the stream has been reset."""
        return self._rst


def _stream_id_generator() -> Callable[[], int]:
    lock = threading.Lock()
    _next: int = 1

    def _next_id() -> int:
        nonlocal _next
        with lock:
            sid = _next
            _next += 2  # even = client-initiated
            return sid

    return _next_id


# ── connection-level multiplexer ──────────────────────────────────────────────


class MuxError(Exception):
    """Base exception for mux-level errors."""


class StreamClosedError(MuxError):
    """Operation on a stream that is already closed or reset."""


class StreamNotFoundError(MuxError):
    """Referenced stream does not exist."""


class ProtocolError(MuxError):
    """Frame violates protocol (bad seq, flags combo, etc.)."""


@dataclass
class StreamMux:
    """Connection-level frame multiplexer with per-stream flow control.

    Wire format: 9-byte header (stream_id u32, seq u16, flags u8, payload_len u16)
    followed by variable-length payload.

    Usage:
        mux = StreamMux(send_window=16384, recv_window=16384)
        sid = mux.open_stream()
        mux.send(sid, b"hello")
        frames = mux.flush()           # => list[bytes]
        # … on the other end …
        mux.ingest(received_bytes)     # => list[(stream_id, bytes)]
    """

    send_window: int = 65536
    recv_window: int = 65536
    _streams: dict[int, StreamState] = field(default_factory=dict)
    _new_id: Callable[[], int] = field(default_factory=_stream_id_generator)
    _pending_frames: list[Frame] = field(default_factory=list)
    _recv_buffer: bytearray = field(default_factory=bytearray)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _clock: Callable[[], float] = field(default=_monotonic_now, repr=False)
    _closed: bool = False

    # ── stream management ─────────────────────────────────────────────────

    def open_stream(self, *, stream_id: int | None = None) -> int:
        """Open a stream and return its unique identifier."""
        with self._lock:
            if self._closed:
                raise MuxError("connection closed")
            if stream_id is not None:
                if stream_id in self._streams:
                    raise MuxError(f"stream {stream_id} already exists")
                sid = stream_id
            else:
                sid = self._new_id()
                while sid in self._streams:
                    sid = self._new_id()
            state = StreamState(stream_id=sid, send_window=self.send_window, recv_window=self.recv_window)
            self._streams[sid] = state
            return sid

    def close_stream(self, stream_id: int) -> None:
        """Close the outbound direction and enqueue a FIN frame."""
        with self._lock:
            st = self._get_stream(stream_id)
            if st._send_closed:
                return
            st._send_closed = True
            self._pending_frames.append(Frame(stream_id=stream_id, seq=st._send_seq, flags=FrameFlags.FIN))
            st._send_seq += 1

    def reset_stream(self, stream_id: int, error: str = "") -> None:
        """Reset a stream and enqueue its optional error payload."""
        with self._lock:
            st = self._get_stream(stream_id)
            st._rst = True
            st._error = error
            self._pending_frames.append(Frame(stream_id=stream_id, seq=0, flags=FrameFlags.RST, payload=error.encode()))

    def stream_count(self) -> int:
        """Return the number of streams tracked by this connection."""
        with self._lock:
            return len(self._streams)

    @property
    def stream_ids(self) -> list[int]:
        """Return tracked stream identifiers in ascending order."""
        with self._lock:
            return sorted(self._streams)

    def stream_state(self, stream_id: int) -> StreamState:
        """Return the state for an existing stream."""
        with self._lock:
            return self._get_stream(stream_id)

    # ── send side ──────────────────────────────────────────────────────────

    def send(self, stream_id: int, data: bytes) -> None:
        """Queue one payload when the stream and flow window allow it."""
        with self._lock:
            st = self._get_stream(stream_id)
            if not st.sendable:
                raise StreamClosedError(f"stream {stream_id} not sendable")
            if len(data) > _MAX_PAYLOAD:
                raise ValueError(f"payload {len(data)} exceeds max {_MAX_PAYLOAD}")
            if len(data) > st._send_flow.available():
                raise MuxError(f"send window exhausted on stream {stream_id}")
            frame = Frame(stream_id=stream_id, seq=st._send_seq, flags=FrameFlags.NONE, payload=data)
            st._send_flow.consume(len(data))
            self._pending_frames.append(frame)
            st._send_seq += 1

    def handle_window_update(self, stream_id: int, delta: int) -> None:
        """Advance an outbound flow-control window."""
        with self._lock:
            st = self._get_stream(stream_id)
            st._send_flow.advance(delta)

    # ── flush (build wire bytes) ───────────────────────────────────────────

    def flush(self) -> list[bytes]:
        """Encode and clear every pending outbound frame."""
        with self._lock:
            frames = list(self._pending_frames)
            self._pending_frames.clear()
            return [f.encode() for f in frames]

    # ── recv side (demux) ──────────────────────────────────────────────────

    def ingest(self, data: bytes) -> list[tuple[int, bytes]]:
        """Feed wire bytes and return complete data frames in stream order."""
        with self._lock:
            self._recv_buffer.extend(data)
            delivered: list[tuple[int, bytes]] = []
            while self._decode_one(delivered):
                pass
            return delivered

    # ── close / lifecycle ──────────────────────────────────────────────────

    def close(self) -> None:
        """Close the connection to new streams."""
        with self._lock:
            self._closed = True

    @property
    def is_closed(self) -> bool:
        """Return whether the connection is closed."""
        with self._lock:
            return self._closed

    # ── internal ───────────────────────────────────────────────────────────

    def _get_stream(self, stream_id: int) -> StreamState:
        try:
            return self._streams[stream_id]
        except KeyError as err:
            raise StreamNotFoundError(f"stream {stream_id} not found") from err

    def _decode_one(self, delivered: list[tuple[int, bytes]]) -> bool:
        if len(self._recv_buffer) < _HEADER_SIZE:
            return False
        sid, seq, flags_byte, plen = struct.unpack(_HEADER_FMT, self._recv_buffer[:_HEADER_SIZE])
        total_needed = _HEADER_SIZE + plen
        if len(self._recv_buffer) < total_needed:
            return False
        payload = bytes(self._recv_buffer[_HEADER_SIZE:total_needed])
        del self._recv_buffer[:total_needed]
        flags = FrameFlags(flags_byte)

        if flags & FrameFlags.RST:
            self._handle_rst(sid, payload)
            return True
        if flags & FrameFlags.SYN:
            self._handle_syn(sid)
            return True
        if flags & FrameFlags.FIN:
            self._handle_fin(sid)
            return True
        if flags & FrameFlags.WINDOW_UPDATE:
            self._handle_window_update_frame(sid, payload)
            return True

        st = self._get_stream(sid)
        if not st.receivable:
            return True
        if seq > st._recv_seq + st.recv_window:
            raise ProtocolError(f"seq {seq} exceeds recv window on stream {sid}")
        if seq < st._recv_seq:
            return True

        st._recv_buf[seq] = payload
        while st._recv_seq in st._recv_buf:
            delivered.append((sid, st._recv_buf.pop(st._recv_seq)))
            st._recv_flow.consume(len(delivered[-1][1]))
            st._recv_seq += 1
        return True

    def _handle_rst(self, sid: int, payload: bytes) -> None:
        st = self._streams.get(sid)
        if st is None:
            return
        st._rst = True
        st._error = payload.decode(errors="replace")

    def _handle_syn(self, sid: int) -> None:
        if sid in self._streams:
            return
        st = StreamState(stream_id=sid, send_window=self.send_window, recv_window=self.recv_window)
        self._streams[sid] = st

    def _handle_fin(self, sid: int) -> None:
        try:
            st = self._get_stream(sid)
        except StreamNotFoundError:
            return
        st._recv_closed = True

    def _handle_window_update_frame(self, sid: int, payload: bytes) -> None:
        try:
            delta = struct.unpack(">I", payload)[0]
        except struct.error:
            return
        try:
            st = self._get_stream(sid)
        except StreamNotFoundError:
            return
        st._send_flow.advance(delta)
