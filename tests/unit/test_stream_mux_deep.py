"""Deep stream multiplexing tests — 20 tests covering:
- Frame encode/decode roundtrip with all flag combinations
- Stream open/close/reset lifecycle
- Ordered send/recv within a stream
- Flow-control window exhaustion and advancement
- Multi-stream interleaved delivery
- Control frames (SYN, FIN, RST) handling
- Partial-ingest buffering and reassembly
- Error cases: double close, send on closed, missing stream, oversized payload
"""

from __future__ import annotations

import pytest

from general_ludd.network.stream_mux import (
    _HEADER_FMT,
    _HEADER_SIZE,
    _MAX_PAYLOAD,
    Frame,
    FrameFlags,
    MuxError,
    StreamClosedError,
    StreamMux,
    StreamNotFoundError,
    StreamState,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Frame — encode / decode
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrameEncodeDecode:
    def test_roundtrip_no_flags(self):
        original = Frame(stream_id=1, seq=0, flags=FrameFlags.NONE, payload=b"hello")
        decoded = Frame.decode(original.encode())
        assert decoded.stream_id == 1
        assert decoded.seq == 0
        assert decoded.flags == FrameFlags.NONE
        assert decoded.payload == b"hello"

    def test_roundtrip_syn_flag(self):
        original = Frame(stream_id=7, seq=0, flags=FrameFlags.SYN)
        decoded = Frame.decode(original.encode())
        assert decoded.flags == FrameFlags.SYN
        assert decoded.payload == b""

    def test_roundtrip_fin_flag(self):
        original = Frame(stream_id=3, seq=2, flags=FrameFlags.FIN, payload=b"last")
        decoded = Frame.decode(original.encode())
        assert decoded.flags == FrameFlags.FIN
        assert decoded.payload == b"last"

    def test_roundtrip_rst_flag(self):
        original = Frame(stream_id=42, seq=0, flags=FrameFlags.RST, payload=b"gone")
        decoded = Frame.decode(original.encode())
        assert decoded.flags == FrameFlags.RST
        assert decoded.payload == b"gone"

    def test_roundtrip_combined_flags(self):
        original = Frame(stream_id=5, seq=1, flags=FrameFlags.SYN | FrameFlags.FIN)
        decoded = Frame.decode(original.encode())
        assert decoded.flags == (FrameFlags.SYN | FrameFlags.FIN)

    def test_decode_too_short(self):
        with pytest.raises(ValueError, match="frame too short"):
            Frame.decode(b"\x00\x00\x00\x01")  # 4 bytes < 9

    def test_max_payload(self):
        payload = b"x" * _MAX_PAYLOAD
        original = Frame(stream_id=99, seq=65535, flags=FrameFlags.NONE, payload=payload)
        decoded = Frame.decode(original.encode())
        assert decoded.seq == 65535
        assert len(decoded.payload) == _MAX_PAYLOAD

    def test_empty_payload_roundtrip(self):
        original = Frame(stream_id=1, seq=0, flags=FrameFlags.ACK)
        decoded = Frame.decode(original.encode())
        assert decoded.payload == b""
        assert decoded.flags == FrameFlags.ACK


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — stream lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamLifecycle:
    def test_open_stream_returns_distinct_ids(self):
        mux = StreamMux()
        a = mux.open_stream()
        b = mux.open_stream()
        assert a != b
        assert mux.stream_count() == 2

    def test_open_stream_with_explicit_id(self):
        mux = StreamMux()
        sid = mux.open_stream(stream_id=100)
        assert sid == 100
        assert 100 in mux.stream_ids

    def test_open_stream_duplicate_id_raises(self):
        mux = StreamMux()
        mux.open_stream(stream_id=5)
        with pytest.raises(MuxError, match="already exists"):
            mux.open_stream(stream_id=5)

    def test_close_stream_marks_send_closed(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.close_stream(sid)
        st = mux.stream_state(sid)
        assert st._send_closed

    def test_double_close_is_idempotent(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.close_stream(sid)
        mux.close_stream(sid)  # no raise
        assert mux.stream_state(sid)._send_closed

    def test_reset_stream_marks_rst(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.reset_stream(sid, error="timeout")
        st = mux.stream_state(sid)
        assert st._rst
        assert st._error == "timeout"

    def test_close_then_reset(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.close_stream(sid)
        mux.reset_stream(sid, error="late reset")
        assert mux.stream_state(sid)._rst


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — send / recv ordered delivery
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendRecvOrdered:
    def test_single_message_roundtrip(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.send(sid, b"hello")
        wire = mux.flush()
        results = mux.ingest(b"".join(wire))
        assert results == [(sid, b"hello")]

    def test_multiple_messages_ordered(self):
        mux = StreamMux()
        sid = mux.open_stream()
        for i in range(5):
            mux.send(sid, f"msg{i}".encode())
        wire = mux.flush()
        results = mux.ingest(b"".join(wire))
        assert results == [(sid, f"msg{i}".encode()) for i in range(5)]

    def test_partial_ingest_buffers_and_reassembles(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.send(sid, b"hello")
        mux.send(sid, b"world")
        full = b"".join(mux.flush())
        # feed one byte at a time
        results: list[tuple[int, bytes]] = []
        for byte in full:
            results.extend(mux.ingest(bytes([byte])))
        assert results == [(sid, b"hello"), (sid, b"world")]

    def test_send_on_closed_stream_raises(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.close_stream(sid)
        with pytest.raises(StreamClosedError):
            mux.send(sid, b"nope")

    def test_send_oversized_payload_raises(self):
        mux = StreamMux()
        sid = mux.open_stream()
        with pytest.raises(ValueError, match="exceeds max"):
            mux.send(sid, b"x" * (_MAX_PAYLOAD + 1))

    def test_send_on_missing_stream_raises(self):
        mux = StreamMux()
        with pytest.raises(StreamNotFoundError):
            mux.send(999, b"nope")


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — flow control (window)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlowControl:
    def test_send_window_exhaustion(self):
        mux = StreamMux(send_window=10)
        sid = mux.open_stream()
        mux.send(sid, b"1234567890")  # exactly fills window
        with pytest.raises(MuxError, match="send window exhausted"):
            mux.send(sid, b"x")

    def test_window_update_replenishes(self):
        mux = StreamMux(send_window=10)
        sid = mux.open_stream()
        mux.send(sid, b"1234567890")
        mux.handle_window_update(sid, 5)
        mux.send(sid, b"abcde")  # now has room
        wire = mux.flush()
        results = mux.ingest(b"".join(wire))
        assert len(results) == 2
        assert results[0][1] == b"1234567890"
        assert results[1][1] == b"abcde"

    def test_window_update_beyond_original_capacity(self):
        mux = StreamMux(send_window=10)
        sid = mux.open_stream()
        mux.send(sid, b"1234567890")
        mux.handle_window_update(sid, 100)
        mux.send(sid, b"more")  # has room from large update
        assert mux.stream_state(sid)._send_flow.available() > 0


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — multi-stream interleaving
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiStream:
    def test_two_streams_interleaved(self):
        mux = StreamMux()
        s1 = mux.open_stream()
        s2 = mux.open_stream()
        mux.send(s1, b"a")
        mux.send(s2, b"b")
        mux.send(s1, b"c")
        wire = mux.flush()
        results = mux.ingest(b"".join(wire))
        s1_payloads = [p for sid, p in results if sid == s1]
        s2_payloads = [p for sid, p in results if sid == s2]
        assert s1_payloads == [b"a", b"c"]
        assert s2_payloads == [b"b"]

    def test_streams_have_independent_windows(self):
        mux = StreamMux(send_window=10)
        s1 = mux.open_stream()
        s2 = mux.open_stream()
        mux.send(s1, b"1234567890")  # exhaust s1
        mux.send(s2, b"1234567890")  # exhaust s2
        with pytest.raises(MuxError):
            mux.send(s1, b"x")
        with pytest.raises(MuxError):
            mux.send(s2, b"x")
        mux.handle_window_update(s1, 5)  # only s1 gets room
        mux.send(s1, b"abcde")


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — control frames (SYN, FIN, RST) on ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestControlFrameIngest:
    def test_syn_frame_opens_remote_stream(self):
        mux = StreamMux()
        syn = Frame(stream_id=50, seq=0, flags=FrameFlags.SYN).encode()
        results = mux.ingest(syn)
        assert results == []
        assert 50 in mux.stream_ids

    def test_fin_frame_closes_recv_side(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.send(sid, b"hello")
        mux.close_stream(sid)
        wire = mux.flush()
        data = b"".join(wire)
        # ingest everything — the FIN is the last frame
        results = mux.ingest(data)
        assert mux.stream_state(sid)._recv_closed
        # should still deliver the data frame
        assert len(results) >= 1

    def test_rst_frame_resets_stream(self):
        mux = StreamMux()
        sid = mux.open_stream()
        rst = Frame(stream_id=sid, seq=0, flags=FrameFlags.RST, payload=b"error!").encode()
        mux.ingest(rst)
        assert mux.stream_state(sid)._rst
        assert mux.stream_state(sid)._error == "error!"

    def test_ingest_rst_on_unknown_stream_does_not_crash(self):
        mux = StreamMux()
        rst = Frame(stream_id=999, seq=0, flags=FrameFlags.RST, payload=b"phantom").encode()
        mux.ingest(rst)  # no exception

    def test_ingest_fin_on_unknown_stream_does_not_crash(self):
        mux = StreamMux()
        fin = Frame(stream_id=999, seq=0, flags=FrameFlags.FIN).encode()
        mux.ingest(fin)  # no exception


# ═══════════════════════════════════════════════════════════════════════════════
# StreamMux — error / edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_close_connection_prevents_open(self):
        mux = StreamMux()
        mux.close()
        with pytest.raises(MuxError, match="connection closed"):
            mux.open_stream()

    def test_duplicate_seq_ignored(self):
        mux = StreamMux()
        sid = mux.open_stream()
        f1 = Frame(stream_id=sid, seq=0, flags=FrameFlags.NONE, payload=b"first").encode()
        f2 = Frame(stream_id=sid, seq=0, flags=FrameFlags.NONE, payload=b"dup").encode()
        results = mux.ingest(f1 + f2)
        assert results == [(sid, b"first")]

    def test_receive_after_fin_ignored(self):
        mux = StreamMux()
        sid = mux.open_stream()
        fin = Frame(stream_id=sid, seq=0, flags=FrameFlags.FIN).encode()
        data = Frame(stream_id=sid, seq=1, flags=FrameFlags.NONE, payload=b"late").encode()
        mux.ingest(fin)
        assert mux.stream_state(sid)._recv_closed
        results = mux.ingest(data)
        assert results == []

    def test_send_after_rst_raises(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.reset_stream(sid)
        with pytest.raises(StreamClosedError):
            mux.send(sid, b"nope")

    def test_flush_clears_pending(self):
        mux = StreamMux()
        sid = mux.open_stream()
        mux.send(sid, b"hello")
        assert len(mux.flush()) == 1
        assert len(mux.flush()) == 0

    def test_mux_close_is_idempotent(self):
        mux = StreamMux()
        mux.close()
        mux.close()
        assert mux.is_closed

    def test_open_stream_respects_send_window(self):
        mux = StreamMux(send_window=32)
        sid = mux.open_stream()
        st = mux.stream_state(sid)
        assert st.send_window == 32
        assert st._send_flow.available() == 32


# ═══════════════════════════════════════════════════════════════════════════════
# StreamState — internal state transitions
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamStateTransitions:
    def test_initial_state_sendable_and_receivable(self):
        st = StreamState(stream_id=1, send_window=64, recv_window=64)
        assert st.sendable
        assert st.receivable
        assert not st.is_closed
        assert not st.is_rst

    def test_closed_after_send_and_recv_closed(self):
        st = StreamState(stream_id=1, send_window=64, recv_window=64)
        assert not st.is_closed
        st._send_closed = True
        assert not st.is_closed
        st._recv_closed = True
        assert st.is_closed

    def test_rst_makes_not_sendable_or_receivable(self):
        st = StreamState(stream_id=1, send_window=64, recv_window=64)
        st._rst = True
        assert not st.sendable
        assert not st.receivable

    def test_fin_makes_not_sendable(self):
        st = StreamState(stream_id=1, send_window=64, recv_window=64)
        st._send_closed = True
        assert not st.sendable
        assert st.receivable

    def test_fin_makes_not_receivable(self):
        st = StreamState(stream_id=1, send_window=64, recv_window=64)
        st._recv_closed = True
        assert st.sendable
        assert not st.receivable


# ═══════════════════════════════════════════════════════════════════════════════
# Wire-format constants — structural assertions
# ═══════════════════════════════════════════════════════════════════════════════


class TestWireConstants:
    def test_header_size_is_nine_bytes(self):
        assert _HEADER_SIZE == 9

    def test_header_format_is_big_endian(self):
        assert _HEADER_FMT.startswith(">")

    def test_max_payload_is_sixteen_bit_range(self):
        assert _MAX_PAYLOAD == 65535
