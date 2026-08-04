"""Deep LSM tree storage engine tests: MemTable, SSTable, compaction, recovery."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from general_ludd.storage.lsm_tree import (
    _TOMBSTONE,
    LevelCompaction,
    LSMEngine,
    MemTable,
    SSTable,
    _BloomFilter,
)


class TestMemTable:
    def test_put_and_get_returns_value(self) -> None:
        mt = MemTable()
        mt.put(b"a", b"1")
        assert mt.get(b"a") == b"1"

    def test_get_missing_returns_none(self) -> None:
        mt = MemTable()
        assert mt.get(b"nonexistent") is None

    def test_delete_marks_tombstone(self) -> None:
        mt = MemTable()
        mt.put(b"x", b"val")
        mt.delete(b"x")
        assert mt.get(b"x") is None
        assert mt.data[b"x"] == _TOMBSTONE

    def test_contains_excludes_tombstone(self) -> None:
        mt = MemTable()
        mt.put(b"a", b"1")
        mt.delete(b"a")
        assert b"a" not in mt

    def test_len_reflects_entry_count(self) -> None:
        mt = MemTable()
        mt.put(b"k1", b"v1")
        mt.put(b"k2", b"v2")
        assert len(mt) == 2

    def test_items_returns_sorted_by_key(self) -> None:
        mt = MemTable()
        mt.put(b"c", b"3")
        mt.put(b"a", b"1")
        mt.put(b"b", b"2")
        keys = [k for k, _ in mt.items()]
        assert keys == [b"a", b"b", b"c"]

    def test_snapshot_roundtrip_preserves_data(self) -> None:
        mt = MemTable()
        mt.put(b"hello", b"world")
        mt.delete(b"deleted")
        raw = mt.snapshot()
        mt2 = MemTable.from_snapshot(raw)
        assert mt2.get(b"hello") == b"world"
        assert mt2.get(b"deleted") is None
        assert len(mt2) == 2

    def test_overwrite_replaces_value(self) -> None:
        mt = MemTable()
        mt.put(b"k", b"v1")
        mt.put(b"k", b"v2")
        assert mt.get(b"k") == b"v2"
        assert len(mt) == 1


class TestBloomFilter:
    def test_add_then_may_contain_is_true(self) -> None:
        bf = _BloomFilter.new(100)
        bf.add(b"foo")
        assert bf.may_contain(b"foo") is True

    def test_not_added_is_never_false_positive(self) -> None:
        bf = _BloomFilter.new(1000)
        bf.add(b"present")
        assert bf.may_contain(b"present") is True

    def test_empty_filter_rejects_all(self) -> None:
        bf = _BloomFilter.new(100)
        assert bf.may_contain(b"anything") is False


class TestSSTable:
    def test_write_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write(
                [(b"a", b"1"), (b"b", b"2"), (b"c", b"3")],
                basename="test",
                directory=d,
            )
            assert sst.get(b"a") == b"1"
            assert sst.get(b"b") == b"2"
            assert sst.get(b"c") == b"3"
            assert sst._entry_count == 3

    def test_get_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write([(b"a", b"1")], basename="t", directory=d)
            assert sst.get(b"z") is None

    def test_tombstone_value_returns_none_on_get(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write([(b"a", _TOMBSTONE)], basename="tomb", directory=d)
            assert sst.get(b"a") is None

    def test_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write(
                [(b"k1", b"v1"), (b"k2", b"v2")],
                basename="round",
                directory=d,
            )
            loaded = SSTable.load(sst.path, sst.meta_path)
            assert loaded.get(b"k1") == b"v1"
            assert loaded.get(b"k2") == b"v2"
            assert loaded._entry_count == 2

    def test_meta_file_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write([(b"z", b"9")], basename="meta", directory=d)
            meta = json.loads(sst.meta_path.read_text())
            assert meta["entry_count"] == 1
            assert meta["min_key"] is not None

    def test_large_index_spans_entries(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            entries = [(f"key{i:04d}".encode(), f"val{i:04d}".encode()) for i in range(2000)]
            sst = SSTable.write(entries, basename="large", directory=d)
            assert sst.get(b"key0000") == b"val0000"
            assert sst.get(b"key1500") == b"val1500"
            assert sst.get(b"key1999") == b"val1999"
            assert sst.get(b"key9999") is None

    def test_empty_table(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst = SSTable.write([], basename="empty", directory=d)
            assert sst._entry_count == 0
            assert sst.get(b"anything") is None


class TestLSMEnginePutGetDelete:
    def test_put_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"a", b"1")
            assert engine.get(b"a") == b"1"

    def test_delete_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"a", b"1")
            engine.delete(b"a")
            assert engine.get(b"a") is None

    def test_string_keys_coerced_to_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put("strkey", "strval")
            assert engine.get("strkey") == b"strval"

    def test_get_missing_key_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            assert engine.get(b"does-not-exist") is None

    def test_overwrite_returns_latest_value(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"k", b"v1")
            engine.put(b"k", b"v2")
            assert engine.get(b"k") == b"v2"


class TestLSMFlush:
    def test_flush_clears_memtable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"a", b"1")
            engine.put(b"b", b"2")
            engine.flush()
            assert len(engine.memtable) == 0
            assert engine.get(b"a") == b"1"
            assert engine.get(b"b") == b"2"

    def test_flush_creates_sst_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"x", b"y")
            sst = engine.flush()
            assert sst.path.exists()
            assert sst.meta_path.exists()

    def test_data_persists_across_engines_after_flush(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine1 = LSMEngine(directory=dpath)
            engine1.put(b"persist", b"me")
            engine1.flush()

            engine2 = LSMEngine(directory=dpath)
            engine2.recover()
            assert engine2.get(b"persist") == b"me"

    def test_empty_flush_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            sst = engine.flush()
            assert sst is not None


class TestLSMCompaction:
    def test_compaction_merges_sstables(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"k1", b"v1")
            engine.flush()
            engine.put(b"k2", b"v2")
            engine.flush()
            assert len(engine.levels[0]) == 2
            compaction = engine.compact()
            assert compaction is not None
            assert len(engine.levels[0]) == 1
            assert engine.get(b"k1") == b"v1"
            assert engine.get(b"k2") == b"v2"

    def test_compaction_resolves_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"k", b"old")
            engine.flush()
            engine.put(b"k", b"new")
            engine.flush()
            engine.compact()
            assert engine.get(b"k") == b"new"

    def test_compaction_with_single_sstable_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"a", b"1")
            engine.flush()
            assert engine.compact() is None


class TestLSMRecovery:
    def test_recover_reads_sstables(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine1 = LSMEngine(directory=dpath)
            engine1.put(b"r1", b"v1")
            engine1.put(b"r2", b"v2")
            engine1.flush()

            engine2 = LSMEngine(directory=dpath)
            count = engine2.recover()
            assert count == 2
            assert engine2.get(b"r1") == b"v1"
            assert engine2.get(b"r2") == b"v2"

    def test_recover_no_files_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            assert engine.recover() == 0

    def test_wal_replay_on_missing_sst(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"wal_only", b"val")
            wal_path = dpath / "wal.log"
            assert wal_path.exists()

            engine2 = LSMEngine(directory=dpath)
            engine2.recover()
            assert engine2.get(b"wal_only") == b"val"
            assert not wal_path.exists()

    def test_delete_tombstone_recovered_via_wal(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"temp", b"val")
            engine.delete(b"temp")

            engine2 = LSMEngine(directory=dpath)
            engine2.recover()
            assert engine2.get(b"temp") is None

    def test_recovery_does_not_double_count_wal_after_flush(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine1 = LSMEngine(directory=dpath)
            engine1.put(b"a", b"1")
            engine1.flush()
            engine1.put(b"b", b"2")

            engine2 = LSMEngine(directory=dpath)
            count = engine2.recover()
            assert engine2.get(b"a") == b"1"
            assert engine2.get(b"b") == b"2"
            assert count > 0


class TestLSMTombstonePropagation:
    def test_tombstone_in_memtable_stops_sst_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"k", b"v1")
            engine.flush()
            engine.delete(b"k")
            assert engine.get(b"k") is None

    def test_tombstone_in_sst_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"k", b"v1")
            engine.delete(b"k")
            engine.flush()
            assert engine.get(b"k") is None


class TestLevelCompaction:
    def test_run_merges_and_removes_sources(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d)
            sst1 = SSTable.write([(b"a", b"1")], basename="c1", directory=d)
            sst2 = SSTable.write([(b"b", b"2")], basename="c2", directory=d)
            compaction = LevelCompaction(
                level=0,
                source_paths=[sst1.path, sst2.path],
                source_meta_paths=[sst1.meta_path, sst2.meta_path],
            )
            merged = compaction.run(d, "compacted")
            assert merged.get(b"a") == b"1"
            assert merged.get(b"b") == b"2"
            assert not sst1.path.exists()
            assert not sst2.path.exists()


class TestEdgeCases:
    def test_zero_byte_values(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"empty", b"")
            assert engine.get(b"empty") == b""

    def test_binary_keys_with_null_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            engine = LSMEngine(directory=Path(d))
            engine.put(b"\x00key\x01", b"\x00val\x01")
            assert engine.get(b"\x00key\x01") == b"\x00val\x01"

    def test_many_keys_in_memtable(self) -> None:
        engine = LSMEngine(directory=Path(tempfile.mkdtemp()))
        for i in range(500):
            engine.put(f"key{i:04d}".encode(), f"val{i:04d}".encode())
        for i in range(500):
            assert engine.get(f"key{i:04d}".encode()) == f"val{i:04d}".encode()

    def test_compaction_then_flush_then_recover(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            e = LSMEngine(directory=dpath)
            e.put(b"a", b"1")
            e.flush()
            e.put(b"b", b"2")
            e.flush()
            e.compact()
            e.put(b"c", b"3")
            e.flush()

            e2 = LSMEngine(directory=dpath)
            e2.recover()
            assert e2.get(b"a") == b"1"
            assert e2.get(b"b") == b"2"
            assert e2.get(b"c") == b"3"

    def test_multiple_flush_compact_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            for i in range(10):
                engine.put(f"k{i}".encode(), f"v{i}".encode())
                if i % 3 == 0:
                    engine.flush()
                if i % 6 == 0 and len(engine.levels) > 0 and len(engine.levels[0]) >= 2:
                    engine.compact()
            engine.flush()
            for i in range(10):
                assert engine.get(f"k{i}".encode()) == f"v{i}".encode()
