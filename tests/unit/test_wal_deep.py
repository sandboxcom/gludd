"""Deep write-ahead log tests: append, replay, truncate, corrupt detection, checkpoint."""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from pathlib import Path

import pytest

from general_ludd.storage.wal import (
    _CHECKSUM_LEN,
    _HEADER_FMT,
    _HEADER_LEN,
    WalEntry,
    WalIterError,
    WalRecovery,
    WriteAheadLog,
)


def _wal_path() -> str:
    return tempfile.mktemp(prefix="wal_", suffix=".log")


# --- WalEntry tests ---


class TestWalEntry:
    def test_serialize_roundtrip(self) -> None:
        e = WalEntry(seq=42, data=b"hello world")
        raw = e.serialize()
        e2 = WalEntry.deserialize(raw)
        assert e2.seq == 42
        assert e2.data == b"hello world"

    def test_serialize_empty_data(self) -> None:
        e = WalEntry(seq=0, data=b"")
        raw = e.serialize()
        e2 = WalEntry.deserialize(raw)
        assert e2.seq == 0
        assert e2.data == b""

    def test_serialize_large_data(self) -> None:
        data = os.urandom(4096)
        e = WalEntry(seq=1, data=data)
        raw = e.serialize()
        e2 = WalEntry.deserialize(raw)
        assert e2.seq == 1
        assert e2.data == data

    def test_deserialize_truncated_header(self) -> None:
        with pytest.raises(WalIterError):
            WalEntry.deserialize(b"\x00" * 3)

    def test_deserialize_truncated_body(self) -> None:
        payload = struct.pack(_HEADER_FMT, 0, 5) + b"x"
        raw = payload + hashlib.sha256(payload).digest()

        with pytest.raises(WalIterError, match="truncated data body"):
            WalEntry.deserialize(raw)

    def test_deserialize_corrupt_checksum(self) -> None:
        e = WalEntry(seq=7, data=b"tamper")
        raw = bytearray(e.serialize())
        raw[-1] ^= 0xFF
        with pytest.raises(WalIterError):
            WalEntry.deserialize(bytes(raw))

    def test_serialize_includes_checksum(self) -> None:
        e = WalEntry(seq=3, data=b"checksum")
        raw = e.serialize()
        assert len(raw) == _HEADER_LEN + len(b"checksum") + _CHECKSUM_LEN

    def test_header_format_constants(self) -> None:
        header = struct.pack(_HEADER_FMT, 5, 8)
        seq, length = struct.unpack(_HEADER_FMT, header)
        assert seq == 5
        assert length == 8
        assert struct.calcsize(_HEADER_FMT) == _HEADER_LEN


# --- WriteAheadLog append + replay ---


class TestWriteAheadLogAppendReplay:
    def test_append_and_replay(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"one")
            wal.append(b"two")
            wal.append(b"three")
            wal.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 3
            assert entries[0].data == b"one"
            assert entries[1].data == b"two"
            assert entries[2].data == b"three"
            assert entries[0].seq == 0
            assert entries[2].seq == 2
        finally:
            Path(path).unlink(missing_ok=True)

    def test_replay_empty_file(self) -> None:
        path = _wal_path()
        try:
            Path(path).write_bytes(b"")
            entries = list(WriteAheadLog.replay(path))
            assert entries == []
        finally:
            Path(path).unlink(missing_ok=True)

    def test_append_increases_seq(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"a")
            wal.append(b"b")
            wal.close()
            entries = list(WriteAheadLog.replay(path))
            assert [e.seq for e in entries] == [0, 1]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_replay_after_reopen(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"first")
            wal.close()
            wal2 = WriteAheadLog(path)
            wal2.append(b"second")
            wal2.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 2
            assert entries[0].data == b"first"
            assert entries[1].data == b"second"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_large_batch_append(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            count = 100
            for i in range(count):
                wal.append(f"record-{i}".encode())
            wal.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == count
            assert entries[-1].data == b"record-99"
        finally:
            Path(path).unlink(missing_ok=True)


# --- Truncation ---


class TestWriteAheadLogTruncate:
    def test_truncate_after_reduces_count(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            for i in range(10):
                wal.append(f"r{i}".encode())
            wal.close()
            wal2 = WriteAheadLog(path)
            wal2.truncate_after(4)
            wal2.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 5
            assert [e.seq for e in entries] == [0, 1, 2, 3, 4]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_truncate_after_zero_keeps_first(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            for i in range(5):
                wal.append(f"r{i}".encode())
            wal.close()
            wal2 = WriteAheadLog(path)
            wal2.truncate_after(0)
            wal2.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 1
            assert entries[0].seq == 0
        finally:
            Path(path).unlink(missing_ok=True)


# --- Corrupt detection ---


class TestWriteAheadLogCorruptDetection:
    def test_single_entry_corrupt_data(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"good")
            wal.close()
            raw = bytearray(Path(path).read_bytes())
            raw[-1] ^= 0xFF
            Path(path).write_bytes(bytes(raw))
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 0
            with pytest.raises(WalIterError):
                WriteAheadLog.recover(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_mid_file_corruption(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"a")
            wal.append(b"b")
            wal.append(b"c")
            wal.close()
            entries = list(WriteAheadLog.replay(path))
            assert len(entries) == 3
            mid_offset = Path(path).stat().st_size // 2
            raw = bytearray(Path(path).read_bytes())
            raw[mid_offset] ^= 0xFF
            Path(path).write_bytes(bytes(raw))
            remaining = list(WriteAheadLog.replay(path))
            assert len(remaining) < 3
        finally:
            Path(path).unlink(missing_ok=True)

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            list(WriteAheadLog.replay("/tmp/gludd-nonexistent-wal.log"))


# --- WalRecovery integration ---


class TestWalRecovery:
    def test_recovery_replays_all(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"x")
            wal.append(b"y")
            wal.close()
            state = WalRecovery.recover(path)
            assert len(state) == 2
            assert state[0].data == b"x"
            assert state[1].data == b"y"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_recovery_on_corrupt_raises(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"valid")
            wal.close()
            raw = bytearray(Path(path).read_bytes())
            raw[1] ^= 0xFF
            Path(path).write_bytes(bytes(raw))
            with pytest.raises(WalIterError):
                WalRecovery.recover(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_force_recovery_skips_corrupt(self) -> None:
        path = _wal_path()
        try:
            wal = WriteAheadLog(path)
            wal.append(b"valid")
            wal.close()
            raw = bytearray(Path(path).read_bytes())
            raw[1] ^= 0xFF
            Path(path).write_bytes(bytes(raw))
            state = WalRecovery.force_recover(path)
            assert len(state) == 0
        finally:
            Path(path).unlink(missing_ok=True)


# --- Checkpoint ---


class TestCheckpoint:
    def test_checkpoint_write_and_read(self) -> None:
        path = _wal_path()
        ckpt_path = _wal_path() + ".ckpt"
        try:
            wal = WriteAheadLog(path)
            wal.append(b"first")
            wal.append(b"second")
            wal.close()
            checkpointed = wal.checkpoint(Path(ckpt_path))
            assert checkpointed >= 2
            recovered = WalRecovery.recover(ckpt_path)
            assert len(recovered) >= 1
        finally:
            Path(path).unlink(missing_ok=True)
            Path(ckpt_path).unlink(missing_ok=True)

    def test_checkpoint_returns_seq_count(self) -> None:
        path = _wal_path()
        ckpt_path = _wal_path() + ".ckpt"
        try:
            wal = WriteAheadLog(path)
            for i in range(5):
                wal.append(f"data-{i}".encode())
            wal.close()
            n = wal.checkpoint(Path(ckpt_path))
            assert n == 5
        finally:
            Path(path).unlink(missing_ok=True)
            Path(ckpt_path).unlink(missing_ok=True)
