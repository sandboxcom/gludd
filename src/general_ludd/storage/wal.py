"""Write-ahead log with checksummed entries, replay, and checkpoint recovery."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_HEADER_FMT = "!QI"
_HEADER_LEN = struct.calcsize(_HEADER_FMT)
_CHECKSUM_LEN = 32


@dataclass(frozen=True, slots=True)
class WalEntry:
    """Represent one sequenced, checksummed write-ahead-log record."""

    seq: int
    data: bytes

    def serialize(self) -> bytes:
        """Encode the header and data with a trailing SHA-256 checksum."""
        payload = struct.pack(_HEADER_FMT, self.seq, len(self.data)) + self.data
        checksum = hashlib.sha256(payload).digest()
        return payload + checksum

    @staticmethod
    def deserialize(raw: bytes) -> WalEntry:
        """Decode and validate one complete write-ahead-log record.

        Raises:
            WalIterError: If the record is too short, truncated, or has an
                invalid checksum.
        """
        if len(raw) < _HEADER_LEN + _CHECKSUM_LEN:
            raise WalIterError("record too short for header + checksum")
        payload = raw[:-_CHECKSUM_LEN]
        expected = hashlib.sha256(payload).digest()
        actual = raw[-_CHECKSUM_LEN:]
        if actual != expected:
            raise WalIterError("checksum mismatch")
        seq, data_len = struct.unpack(_HEADER_FMT, raw[:_HEADER_LEN])
        if len(raw) < _HEADER_LEN + data_len + _CHECKSUM_LEN:
            raise WalIterError("truncated data body")
        data = raw[_HEADER_LEN : _HEADER_LEN + data_len]
        return WalEntry(seq=seq, data=data)


class WalIterError(ValueError):
    """Raised when a write-ahead-log record is truncated or fails checksum validation."""


def _iter_entries(raw: bytes) -> Iterator[WalEntry]:
    offset = 0
    while offset < len(raw):
        if offset + _HEADER_LEN > len(raw):
            break
        _seq, data_len = struct.unpack(_HEADER_FMT, raw[offset : offset + _HEADER_LEN])
        entry_len = _HEADER_LEN + data_len + _CHECKSUM_LEN
        if offset + entry_len > len(raw):
            break
        entry_bytes = raw[offset : offset + entry_len]
        try:
            yield WalEntry.deserialize(entry_bytes)
        except WalIterError:
            break
        offset += entry_len


def _verify_all_entries(raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        if offset + _HEADER_LEN > len(raw):
            break
        _seq, data_len = struct.unpack(_HEADER_FMT, raw[offset : offset + _HEADER_LEN])
        entry_len = _HEADER_LEN + data_len + _CHECKSUM_LEN
        if offset + entry_len > len(raw):
            break
        WalEntry.deserialize(raw[offset : offset + entry_len])
        offset += entry_len


def _entry_endpoints(raw: bytes) -> list[tuple[int, int, int]]:
    result: list[tuple[int, int, int]] = []
    offset = 0
    while offset < len(raw):
        if offset + _HEADER_LEN > len(raw):
            break
        seq, data_len = struct.unpack(_HEADER_FMT, raw[offset : offset + _HEADER_LEN])
        entry_len = _HEADER_LEN + data_len + _CHECKSUM_LEN
        entry_end = offset + entry_len
        if entry_end > len(raw):
            break
        try:
            WalEntry.deserialize(raw[offset:entry_end])
            result.append((offset, seq, entry_end))
        except WalIterError:
            break
        offset = entry_end
    return result


class WriteAheadLog:
    """Append, replay, truncate, and checkpoint checksummed log records."""

    def __init__(self, path: str) -> None:
        """Open or create a log and continue its next valid sequence number."""
        self._path = Path(path)
        self._file = self._path.open("a+b")
        self._file.seek(0)
        self._seq = self._next_seq_from_disk()

    def _next_seq_from_disk(self) -> int:
        max_seq = -1
        for entry in _iter_entries(self._file.read()):
            max_seq = max(max_seq, entry.seq)
        self._file.seek(0, 2)
        return max_seq + 1

    @property
    def seq(self) -> int:
        """Return the sequence number assigned to the next appended record."""
        return self._seq

    def append(self, data: bytes) -> None:
        """Append and flush one checksummed record to durable storage."""
        entry = WalEntry(seq=self._seq, data=data)
        self._file.write(entry.serialize())
        self._file.flush()
        self._seq += 1

    def close(self) -> None:
        """Close the underlying log file."""
        self._file.close()

    def truncate_after(self, last_kept_seq: int) -> None:
        """Discard valid records after the requested sequence number."""
        self._file.seek(0)
        raw = self._file.read()
        cutoff = 0
        for _offset, seq, end in _entry_endpoints(raw):
            if seq <= last_kept_seq:
                cutoff = end
        if cutoff == 0:
            return
        self._file.seek(0)
        self._file.truncate()
        self._file.write(raw[:cutoff])
        self._file.flush()
        self._seq = last_kept_seq + 1

    def checkpoint(self, ckpt_path: Path) -> int:
        """Write valid records to a checkpoint and return their count."""
        if self._file.closed:
            raw = self._path.read_bytes()
        else:
            self._file.seek(0)
            raw = self._file.read()
            self._file.seek(0, 2)
        entries = list(_iter_entries(raw))
        with ckpt_path.open("wb") as f:
            for entry in entries:
                f.write(entry.serialize())
        return len(entries)

    @staticmethod
    def replay(path: str) -> Iterator[WalEntry]:
        """Yield valid records until the first incomplete or corrupt tail.

        Raises:
            FileNotFoundError: If the log path does not exist.
        """
        if not Path(path).exists():
            raise FileNotFoundError(f"WAL file not found: {path}")
        raw = Path(path).read_bytes()
        yield from _iter_entries(raw)

    @staticmethod
    def recover(path: str) -> list[WalEntry]:
        """Validate and return all complete records in an existing log.

        Raises:
            FileNotFoundError: If the log path does not exist.
            WalIterError: If a complete record has an invalid checksum.
        """
        if not Path(path).exists():
            raise FileNotFoundError(f"WAL file not found: {path}")
        raw = Path(path).read_bytes()
        _verify_all_entries(raw)
        return list(_iter_entries(raw))


class WalRecovery:
    """Expose strict and best-effort recovery policies for stored logs."""

    @staticmethod
    def recover(path: str) -> list[WalEntry]:
        """Return records using strict checksum validation."""
        return WriteAheadLog.recover(path)

    @staticmethod
    def force_recover(path: str) -> list[WalEntry]:
        """Return the valid prefix, stopping at corruption or truncation.

        Raises:
            FileNotFoundError: If the log path does not exist.
        """
        if not Path(path).exists():
            raise FileNotFoundError(f"WAL file not found: {path}")
        raw = Path(path).read_bytes()
        return list(_iter_entries(raw))
