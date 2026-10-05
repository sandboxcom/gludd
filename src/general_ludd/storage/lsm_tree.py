"""LSM tree storage engine: MemTable, SSTable, compaction, recovery."""

from __future__ import annotations

import bisect
import contextlib
import json
import os
import re
import struct
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

_MAGIC = b"LSMT"
_VERSION = 1
_HEADER_FMT = ">4sIQQ"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_ENTRY_FMT_PREFIX = ">IQ"
_TOMBSTONE = b"__LSM_TOMBSTONE__"


class _IndexEntry(NamedTuple):
    key_bytes: bytes
    offset: int
    length: int


@dataclass
class _BloomFilter:
    size: int
    bits: bytearray
    hash_count: int = field(default=3)

    @classmethod
    def new(cls, capacity: int, error_rate: float = 0.01) -> _BloomFilter:
        import math

        size = max(1, int(-capacity * math.log(error_rate) / (math.log(2) ** 2)))
        hash_count = max(1, int(size / capacity * math.log(2)))
        return cls(size=size, bits=bytearray(size), hash_count=hash_count)

    def _hashes(self, key: bytes) -> Iterator[int]:
        h = 0
        for i in range(self.hash_count):
            h = hash(key + struct.pack(">I", i))
            yield abs(h) % self.size

    def add(self, key: bytes) -> None:
        for h in self._hashes(key):
            self.bits[h] = 1

    def may_contain(self, key: bytes) -> bool:
        return all(self.bits[h] for h in self._hashes(key))


@dataclass
class MemTable:
    """In-memory sorted key-value store backed by dict + sorted-key list."""

    data: dict[bytes, bytes] = field(default_factory=dict)
    _keys: list[bytes] = field(default_factory=list)

    def put(self, key: bytes, value: bytes) -> None:
        """Insert or update a key, maintaining sorted-key order."""
        if key not in self.data:
            bisect.insort(self._keys, key)
        self.data[key] = value

    def get(self, key: bytes) -> bytes | None:
        """Return the stored value, or None for absent or tombstoned keys."""
        value = self.data.get(key)
        if value == _TOMBSTONE:
            return None
        return value

    def delete(self, key: bytes) -> None:
        """Mark a key deleted via a tombstone entry."""
        self.put(key, _TOMBSTONE)

    def __len__(self) -> int:
        """Return the number of stored keys (including tombstones)."""
        return len(self.data)

    def __contains__(self, key: bytes) -> bool:
        """Return True when the key holds a live (non-tombstone) value."""
        value = self.data.get(key)
        return value is not None and value != _TOMBSTONE

    def items(self) -> Iterator[tuple[bytes, bytes]]:
        """Yield live entries in sorted-key order."""
        for k in self._keys:
            yield k, self.data[k]

    def snapshot(self) -> bytes:
        """Serialize entire MemTable to JSON bytes for flush."""
        records: list[list[str]] = []
        for k, v in self.items():
            records.append([k.hex(), v.hex()])
        return json.dumps({"version": _VERSION, "entries": records}).encode("utf-8")

    @classmethod
    def from_snapshot(cls, raw: bytes) -> MemTable:
        """Rebuild a MemTable from serialized snapshot JSON bytes."""
        payload = json.loads(raw)
        mt = cls()
        for k_hex, v_hex in payload["entries"]:
            mt.put(bytes.fromhex(k_hex), bytes.fromhex(v_hex))
        return mt


@dataclass
class SSTable:
    """Immutable on-disk Sorted String Table with binary search via sparse index."""

    path: Path
    meta_path: Path
    _entry_count: int = 0
    _min_key: bytes | None = None
    _max_key: bytes | None = None
    _bloom: _BloomFilter | None = None
    _index: list[_IndexEntry] = field(default_factory=list)

    @classmethod
    def write(
        cls,
        entries: list[tuple[bytes, bytes]],
        basename: str,
        directory: str = "",
    ) -> SSTable:
        """Serialize sorted entries into a new SSTable + metadata file."""
        entries.sort(key=lambda e: e[0])
        key_count = len(entries)
        directory = directory or tempfile.mkdtemp(prefix="lsmt_")
        os.makedirs(directory, exist_ok=True)
        path = Path(directory) / f"{basename}.sst"
        meta_path = Path(directory) / f"{basename}.meta"

        bloom = _BloomFilter.new(max(1, key_count))
        index: list[_IndexEntry] = []

        instance = cls(
            path=path,
            meta_path=meta_path,
            _entry_count=key_count,
            _min_key=entries[0][0] if entries else None,
            _max_key=entries[-1][0] if entries else None,
            _bloom=bloom,
            _index=index,
        )

        data_start = _HEADER_SIZE

        index_offsets: list[int] = []
        with open(path, "wb") as f:
            f.seek(data_start)
            for i, (k, v) in enumerate(entries):
                if i % 128 == 0:
                    index_offsets.append(f.tell())
                k_len = len(k)
                v_len = len(v)
                entry_header = struct.pack(_ENTRY_FMT_PREFIX, k_len, v_len)
                f.write(entry_header)
                f.write(k)
                f.write(v)
                bloom.add(k)

            data_end = f.tell()
            f.seek(0)
            header = struct.pack(
                _HEADER_FMT,
                _MAGIC,
                _VERSION,
                key_count,
                int(time.time()),
            )
            f.write(header)

        for idx_pos, offset in enumerate(index_offsets):
            span_end = index_offsets[idx_pos + 1] if idx_pos + 1 < len(index_offsets) else data_end
            key_idx = idx_pos * 128
            index.append(_IndexEntry(entries[key_idx][0], offset, span_end - offset))

        instance._write_meta()
        return instance

    def _write_meta(self) -> None:
        bloom_bytes = bytes(self._bloom.bits).hex() if self._bloom else ""
        meta = {
            "path": str(self.path),
            "entry_count": self._entry_count,
            "min_key": self._min_key.hex() if self._min_key else None,
            "max_key": self._max_key.hex() if self._max_key else None,
            "version": _VERSION,
            "bloom_size": self._bloom.size if self._bloom else 0,
            "bloom_hash_count": self._bloom.hash_count if self._bloom else 3,
            "bloom_bits": bloom_bytes,
            "index": [{"key": e.key_bytes.hex(), "offset": e.offset, "length": e.length} for e in self._index],
        }
        self.meta_path.write_text(json.dumps(meta, indent=2))

    @classmethod
    def load(cls, path: str | Path, meta_path: str | Path) -> SSTable:
        """Reconstruct an SSTable from its data + metadata files."""
        path = Path(path)
        meta_path = Path(meta_path)
        meta = json.loads(meta_path.read_text())
        index = [
            _IndexEntry(
                bytes.fromhex(e["key"]),
                e["offset"],
                e["length"],
            )
            for e in meta["index"]
        ]
        entry_count = meta["entry_count"]
        bloom_size = meta.get("bloom_size", 0)
        if bloom_size > 0 and meta.get("bloom_bits"):
            bloom = _BloomFilter(
                size=bloom_size,
                bits=bytearray(bytes.fromhex(meta["bloom_bits"])),
                hash_count=meta.get("bloom_hash_count", 3),
            )
        else:
            bloom = _BloomFilter.new(max(1, entry_count))
            if entry_count > 0 and path.stat().st_size > 0:
                with open(path, "rb") as f:
                    f.seek(_HEADER_SIZE)
                    for e in index:
                        f.seek(e.offset)
                        prefix = f.read(struct.calcsize(_ENTRY_FMT_PREFIX))
                        k_len, _ = struct.unpack(_ENTRY_FMT_PREFIX, prefix)
                        key_bytes = f.read(k_len)
                        bloom.add(key_bytes)

        return cls(
            path=path,
            meta_path=meta_path,
            _entry_count=entry_count,
            _min_key=bytes.fromhex(meta["min_key"]) if meta["min_key"] else None,
            _max_key=bytes.fromhex(meta["max_key"]) if meta["max_key"] else None,
            _bloom=bloom,
            _index=index,
        )

    def _index_seek(self, key: bytes) -> int:
        lo, hi = 0, len(self._index)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._index[mid].key_bytes < key:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1)

    def get(self, key: bytes) -> bytes | None:
        """Look up a key via bloom filter, range check, and sparse index."""
        if self._bloom and not self._bloom.may_contain(key):
            return None
        if self._min_key is None or self._max_key is None:
            return None
        if key < self._min_key or key > self._max_key:
            return None

        start_idx = self._index_seek(key)
        try:
            with open(self.path, "rb") as f:
                idx = start_idx
                while idx < len(self._index):
                    entry = self._index[idx]
                    f.seek(entry.offset)
                    buf = f.read(entry.length)
                    pos = 0
                    while pos < len(buf):
                        if pos + struct.calcsize(_ENTRY_FMT_PREFIX) > len(buf):
                            break
                        k_len, v_len = struct.unpack(
                            _ENTRY_FMT_PREFIX,
                            buf[pos : pos + struct.calcsize(_ENTRY_FMT_PREFIX)],
                        )
                        pos += struct.calcsize(_ENTRY_FMT_PREFIX)
                        k = buf[pos : pos + k_len]
                        pos += k_len
                        v = buf[pos : pos + v_len]
                        pos += v_len
                        if k == key:
                            return None if v == _TOMBSTONE else v
                        if k > key:
                            return None
                    idx += 1
        except OSError:
            return None
        return None

    def iter_all(self) -> Iterator[tuple[bytes, bytes]]:
        """Yield every entry stored in this SSTable."""
        if self._entry_count == 0:
            return
        with open(self.path, "rb") as f:
            f.seek(_HEADER_SIZE)
            buf = f.read()
            pos = 0
            while pos < len(buf):
                if pos + struct.calcsize(_ENTRY_FMT_PREFIX) > len(buf):
                    break
                k_len, v_len = struct.unpack(
                    _ENTRY_FMT_PREFIX,
                    buf[pos : pos + struct.calcsize(_ENTRY_FMT_PREFIX)],
                )
                pos += struct.calcsize(_ENTRY_FMT_PREFIX)
                k = buf[pos : pos + k_len]
                pos += k_len
                v = buf[pos : pos + v_len]
                pos += v_len
                yield k, v


@dataclass
class LevelCompaction:
    """Tiered compaction: merge all SSTables at a level into one."""

    level: int
    source_paths: list[Path]
    source_meta_paths: list[Path]

    def run(self, directory: str, basename: str) -> SSTable:
        """Merge the level's SSTables into one, then replace the sources."""
        merged: dict[bytes, bytes] = {}
        for sp, mp in zip(self.source_paths, self.source_meta_paths, strict=False):
            try:
                sst = SSTable.load(sp, mp)
            except (OSError, json.JSONDecodeError):
                continue
            for k, v in sst.iter_all():
                merged[k] = v

        for sp in self.source_paths:
            with contextlib.suppress(OSError):
                sp.unlink(missing_ok=True)
        for mp in self.source_meta_paths:
            with contextlib.suppress(OSError):
                mp.unlink(missing_ok=True)

        return SSTable.write(
            [(k, v) for k, v in merged.items()],
            basename=basename,
            directory=directory,
        )


def _range_overlaps(
    min_a: bytes | None,
    max_a: bytes | None,
    min_b: bytes | None,
    max_b: bytes | None,
) -> bool:
    if min_a is None or max_a is None or min_b is None or max_b is None:
        return False
    return max_a >= min_b and max_b >= min_a


@dataclass
class TieredCompaction:
    """Merge all SSTables at a level into one, removing tombstones."""

    level: int
    source_paths: list[Path]
    source_meta_paths: list[Path]

    def run(self, directory: str, basename: str) -> SSTable:
        """Merge the level's SSTables into one, then replace the sources."""
        merged: dict[bytes, bytes] = {}
        for sp, mp in zip(self.source_paths, self.source_meta_paths, strict=False):
            try:
                sst = SSTable.load(sp, mp)
            except (OSError, json.JSONDecodeError):
                continue
            for k, v in sst.iter_all():
                merged[k] = v

        for sp in self.source_paths:
            with contextlib.suppress(OSError):
                sp.unlink(missing_ok=True)
        for mp in self.source_meta_paths:
            with contextlib.suppress(OSError):
                mp.unlink(missing_ok=True)

        return SSTable.write(
            [(k, v) for k, v in merged.items()],
            basename=basename,
            directory=directory,
        )


@dataclass
class LeveledCompaction:
    """Merge overlapping SSTables across adjacent levels, removing tombstones."""

    source_level: int
    source_sst: SSTable
    target_ssts: list[SSTable]

    def run(self, directory: str, basename: str) -> list[SSTable]:
        """Merge overlapping SSTables across adjacent levels into one."""
        merged: dict[bytes, bytes] = {}
        for sst in self.target_ssts:
            for k, v in sst.iter_all():
                merged[k] = v
        for k, v in self.source_sst.iter_all():
            merged[k] = v

        tombstone_keys = [k for k, v in merged.items() if v == _TOMBSTONE]
        for k in tombstone_keys:
            del merged[k]

        for sst in self.target_ssts:
            with contextlib.suppress(OSError):
                sst.path.unlink(missing_ok=True)
                sst.meta_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.source_sst.path.unlink(missing_ok=True)
            self.source_sst.meta_path.unlink(missing_ok=True)

        if not merged:
            return []

        sst = SSTable.write(
            [(k, v) for k, v in merged.items()],
            basename=basename,
            directory=directory,
        )
        return [sst]


@dataclass
class LSMEngine:
    """LSM tree storage engine with MemTable, SSTable levels, flush, and compaction."""

    directory: Path
    memtable: MemTable = field(default_factory=MemTable)
    levels: list[list[SSTable]] = field(default_factory=list)
    _wal_path: Path | None = None
    _next_sst_id: int = 0

    def __post_init__(self) -> None:
        """Create the storage directory and WAL path on construction."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self._wal_path = self.directory / "wal.log"

    def _wal_append(self, op: str, key: bytes, value: bytes) -> None:
        if self._wal_path is None:
            return
        with open(self._wal_path, "ab") as f:
            entry = json.dumps({"op": op, "key": key.hex(), "value": value.hex()}).encode("utf-8")
            f.write(entry + b"\n")

    def put(self, key: str | bytes, value: str | bytes) -> None:
        """Write a key-value pair to the memtable and append to the WAL."""
        k = key if isinstance(key, bytes) else key.encode("utf-8")
        v = value if isinstance(value, bytes) else value.encode("utf-8")
        self.memtable.put(k, v)
        self._wal_append("put", k, v)

    def get(self, key: str | bytes) -> bytes | None:
        """Look up a key in the memtable, then in each SSTable level."""
        k = key if isinstance(key, bytes) else key.encode("utf-8")
        value = self.memtable.get(k)
        if value is not None:
            return value
        if k in self.memtable.data and self.memtable.data[k] == _TOMBSTONE:
            return None
        for level in self.levels:
            for sst in level:
                v = sst.get(k)
                if v is not None:
                    return v
        return None

    def delete(self, key: str | bytes) -> None:
        """Mark a key deleted in the memtable and WAL."""
        k = key if isinstance(key, bytes) else key.encode("utf-8")
        self.memtable.delete(k)
        self._wal_append("delete", k, b"")

    def flush(self) -> SSTable:
        """Persist the current memtable into a new SSTable at level 0."""
        entries = list(self.memtable.items())
        if not entries:
            entries = []
        sst = SSTable.write(
            entries,
            basename=f"sst_{self._next_sst_id:04d}",
            directory=str(self.directory),
        )
        self._next_sst_id += 1
        self.memtable = MemTable()
        if self._wal_path and self._wal_path.exists():
            self._wal_path.unlink()
        if len(self.levels) == 0:
            self.levels.append([])
        self.levels[0].append(sst)
        return sst

    def compact(self) -> LevelCompaction | None:
        """Merge the first level with 2+ SSTables; None when nothing to do."""
        for level_idx, level in enumerate(self.levels):
            if len(level) >= 2:
                compaction = LevelCompaction(
                    level=level_idx,
                    source_paths=[s.path for s in level],
                    source_meta_paths=[s.meta_path for s in level],
                )
                merged = compaction.run(
                    str(self.directory),
                    f"sst_merged_L{level_idx}_{self._next_sst_id:04d}",
                )
                self._next_sst_id += 1
                self.levels[level_idx] = [merged]
                if level_idx + 1 >= len(self.levels):
                    self.levels.append([])
                self.levels[level_idx + 1].append(merged)
                return compaction
        return None

    def find_overlapping(self, source_level: int, source_sst: SSTable) -> list[SSTable]:
        """Return target-level SSTables whose key ranges overlap the source."""
        overlapping: list[SSTable] = []
        target_level = source_level + 1
        if target_level >= len(self.levels):
            return overlapping
        for sst in self.levels[target_level]:
            if sst._min_key is None or sst._max_key is None:
                continue
            if source_sst._min_key is None or source_sst._max_key is None:
                continue
            if _range_overlaps(
                source_sst._min_key,
                source_sst._max_key,
                sst._min_key,
                sst._max_key,
            ):
                overlapping.append(sst)
        return overlapping

    def leveled_compact(self) -> LeveledCompaction | None:
        """Run one cross-level overlapping compaction; None when idle."""
        for level_idx in range(len(self.levels)):
            if level_idx + 1 >= len(self.levels):
                break
            for sst in list(self.levels[level_idx]):
                overlapping = self.find_overlapping(level_idx, sst)
                if not overlapping:
                    continue
                compaction = LeveledCompaction(
                    source_level=level_idx,
                    source_sst=sst,
                    target_ssts=overlapping,
                )
                result = compaction.run(
                    str(self.directory),
                    f"sst_leveled_L{level_idx}_{self._next_sst_id:04d}",
                )
                self._next_sst_id += 1
                self.levels[level_idx].remove(sst)
                for old in overlapping:
                    self.levels[level_idx + 1].remove(old)
                if result:
                    if level_idx + 1 >= len(self.levels):
                        self.levels.append([])
                    self.levels[level_idx + 1].extend(result)
                return compaction
        return None

    def tiered_compact(self) -> TieredCompaction | None:
        """Merge the first level with 2+ SSTables; None when idle."""
        for level_idx, level in enumerate(self.levels):
            if len(level) >= 2:
                compaction = TieredCompaction(
                    level=level_idx,
                    source_paths=[s.path for s in level],
                    source_meta_paths=[s.meta_path for s in level],
                )
                merged = compaction.run(
                    str(self.directory),
                    f"sst_tiered_L{level_idx}_{self._next_sst_id:04d}",
                )
                self._next_sst_id += 1
                self.levels[level_idx] = [merged]
                return compaction
        return None

    def recover(self, sst_paths: list[Path] | None = None) -> int:
        """Rebuild in-memory state from SSTable metadata and WAL records."""
        count = 0
        if sst_paths is None:
            sst_paths = sorted(self.directory.glob("*.meta"))
        if self.levels is None or len(self.levels) == 0:
            self.levels = [[]]
        for meta_path in sst_paths:
            sst_path = Path(str(meta_path).replace(".meta", ".sst"))
            if not sst_path.exists():
                continue
            try:
                sst = SSTable.load(sst_path, meta_path)
            except (OSError, json.JSONDecodeError):
                continue
            self.levels[0].append(sst)
            count += sst._entry_count
            stem = meta_path.stem
            digits = re.search(r"(\d+)$", stem)
            self._next_sst_id = max(
                self._next_sst_id,
                int(digits.group(1)) + 1 if digits else self._next_sst_id,
            )
        if self._wal_path and self._wal_path.exists():
            wal_data = self._wal_path.read_bytes()
            for line in wal_data.split(b"\n"):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    k = bytes.fromhex(rec["key"])
                    v = bytes.fromhex(rec["value"])
                    if rec["op"] == "put":
                        self.memtable.put(k, v)
                    elif rec["op"] == "delete":
                        self.memtable.delete(k)
                except (json.JSONDecodeError, KeyError):
                    continue
            self._wal_path.unlink()
        return count
