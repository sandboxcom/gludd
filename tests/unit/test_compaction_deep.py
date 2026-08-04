"""Deep compaction tests: leveled, tiered, overlapping ranges, tombstone cleanup."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.storage.lsm_tree import (
    _TOMBSTONE,
    LeveledCompaction,
    LSMEngine,
    SSTable,
    TieredCompaction,
    _range_overlaps,
)


def _write_sst(entries: list[tuple[bytes, bytes]], basename: str, d: str) -> SSTable:
    return SSTable.write(entries, basename=basename, directory=d)


class TestRangeOverlaps:
    def test_full_overlap_returns_true(self) -> None:
        assert _range_overlaps(b"a", b"z", b"b", b"y") is True

    def test_partial_overlap_returns_true(self) -> None:
        assert _range_overlaps(b"a", b"m", b"k", b"z") is True

    def test_no_overlap_returns_false(self) -> None:
        assert _range_overlaps(b"a", b"e", b"f", b"z") is False

    def test_adjacent_ranges_no_overlap(self) -> None:
        assert _range_overlaps(b"a", b"m", b"n", b"z") is False

    def test_exact_boundary_touch_is_overlap(self) -> None:
        assert _range_overlaps(b"a", b"m", b"m", b"z") is True

    def test_none_bound_returns_false(self) -> None:
        assert _range_overlaps(None, b"z", b"a", b"m") is False
        assert _range_overlaps(b"a", b"z", None, b"m") is False
        assert _range_overlaps(b"a", b"z", b"m", None) is False


class TestLeveledCompaction:
    def test_merges_overlapping_tables(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"a", b"1"), (b"b", b"2")], "src", d)
            sst_b = _write_sst([(b"b", b"2b"), (b"c", b"3")], "tgt", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            result = compaction.run(d, "merged")
            assert len(result) == 1
            assert result[0].get(b"a") == b"1"
            assert result[0].get(b"b") == b"2"
            assert result[0].get(b"c") == b"3"

    def test_source_overwrites_target_on_key_collision(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"k", b"new")], "src", d)
            sst_b = _write_sst([(b"k", b"old")], "tgt", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            result = compaction.run(d, "merged")
            assert result[0].get(b"k") == b"new"

    def test_removes_tombstones_from_merged_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"k", _TOMBSTONE)], "src", d)
            sst_b = _write_sst([(b"k", b"val"), (b"x", b"y")], "tgt", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            result = compaction.run(d, "merged")
            assert result[0].get(b"k") is None
            assert result[0].get(b"x") == b"y"

    def test_empty_result_when_all_tombstones(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"k", _TOMBSTONE)], "src", d)
            sst_b = _write_sst([(b"k", b"val")], "tgt", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            result = compaction.run(d, "merged")
            assert result == []

    def test_removes_source_files_after_merge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"a", b"1")], "src", d)
            sst_b = _write_sst([(b"b", b"2")], "tgt", d)
            a_path, a_meta = sst_a.path, sst_a.meta_path
            b_path, b_meta = sst_b.path, sst_b.meta_path
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            compaction.run(d, "merged")
            assert not a_path.exists()
            assert not a_meta.exists()
            assert not b_path.exists()
            assert not b_meta.exists()

    def test_multiple_target_ssts_merged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_src = _write_sst([(b"b", b"src_b")], "src", d)
            sst_t1 = _write_sst([(b"a", b"1")], "t1", d)
            sst_t2 = _write_sst([(b"c", b"3")], "t2", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_src,
                target_ssts=[sst_t1, sst_t2],
            )
            result = compaction.run(d, "merged")
            assert len(result) == 1
            assert result[0].get(b"a") == b"1"
            assert result[0].get(b"b") == b"src_b"
            assert result[0].get(b"c") == b"3"


class TestTieredCompaction:
    def test_merges_all_sstables_at_level(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst1 = _write_sst([(b"a", b"1")], "t1", d)
            sst2 = _write_sst([(b"b", b"2")], "t2", d)
            sst3 = _write_sst([(b"c", b"3")], "t3", d)
            compaction = TieredCompaction(
                level=0,
                source_paths=[sst1.path, sst2.path, sst3.path],
                source_meta_paths=[sst1.meta_path, sst2.meta_path, sst3.meta_path],
            )
            merged = compaction.run(d, "tiered")
            assert merged.get(b"a") == b"1"
            assert merged.get(b"b") == b"2"
            assert merged.get(b"c") == b"3"

    def test_later_sstable_overwrites_earlier_on_key_collision(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst1 = _write_sst([(b"k", b"v1")], "t1", d)
            sst2 = _write_sst([(b"k", b"v2")], "t2", d)
            compaction = TieredCompaction(
                level=0,
                source_paths=[sst1.path, sst2.path],
                source_meta_paths=[sst1.meta_path, sst2.meta_path],
            )
            merged = compaction.run(d, "tiered")
            assert merged.get(b"k") == b"v2"

    def test_removes_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst1 = _write_sst([(b"a", b"1")], "t1", d)
            sst2 = _write_sst([(b"b", b"2")], "t2", d)
            p1, m1 = sst1.path, sst1.meta_path
            p2, m2 = sst2.path, sst2.meta_path
            TieredCompaction(
                level=0,
                source_paths=[p1, p2],
                source_meta_paths=[m1, m2],
            ).run(d, "tiered")
            assert not p1.exists()
            assert not m1.exists()
            assert not p2.exists()
            assert not m2.exists()

    def test_preserves_all_entries_after_merge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            entries = [(f"k{i:04d}".encode(), f"v{i:04d}".encode()) for i in range(50)]
            sst1 = _write_sst(entries[:25], "t1", d)
            sst2 = _write_sst(entries[25:], "t2", d)
            compaction = TieredCompaction(
                level=0,
                source_paths=[sst1.path, sst2.path],
                source_meta_paths=[sst1.meta_path, sst2.meta_path],
            )
            merged = compaction.run(d, "tiered")
            for i in range(50):
                assert merged.get(f"k{i:04d}".encode()) == f"v{i:04d}".encode()


class TestLSMEngineLeveledCompact:
    def test_leveled_compact_with_overlap_triggers_merge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            sst_l1 = SSTable.write(
                [(b"a", b"1"), (b"b", b"old")],
                basename="l1",
                directory=str(dpath),
            )
            sst_l0 = SSTable.write(
                [(b"b", b"new"), (b"c", b"3")],
                basename="l0",
                directory=str(dpath),
            )
            engine.levels = [[sst_l0], [sst_l1]]
            engine._next_sst_id = 10
            result = engine.leveled_compact()
            assert result is not None
            assert result.source_level == 0
            merged = engine.levels[1][0]
            assert merged.get(b"b") == b"new"
            assert merged.get(b"a") == b"1"
            assert merged.get(b"c") == b"3"

    def test_leveled_compact_no_overlap_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            assert engine.leveled_compact() is None

    def test_find_overlapping_returns_ssts_in_key_range(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            sst_l0 = SSTable.write(
                [(b"a", b"1"), (b"z", b"99")],
                basename="wide",
                directory=str(dpath),
            )
            sst_l1a = SSTable.write(
                [(b"a", b"10"), (b"m", b"20")],
                basename="l1a",
                directory=str(dpath),
            )
            sst_l1b = SSTable.write(
                [(b"n", b"30")],
                basename="l1b",
                directory=str(dpath),
            )
            engine.levels = [[sst_l0], [sst_l1a, sst_l1b]]
            overlapping = engine.find_overlapping(0, sst_l0)
            assert len(overlapping) == 2


class TestLSMEngineTieredCompact:
    def test_tiered_compact_with_two_sstables_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"a", b"1")
            engine.flush()
            engine.put(b"b", b"2")
            engine.flush()
            result = engine.tiered_compact()
            assert result is not None
            assert result.level == 0
            assert len(engine.levels[0]) == 1
            assert engine.get(b"a") == b"1"
            assert engine.get(b"b") == b"2"

    def test_tiered_compact_single_sstable_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"a", b"1")
            engine.flush()
            assert engine.tiered_compact() is None


class TestTombstoneCleanup:
    def test_tombstone_in_sst_cleaned_up_by_leveled_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            sst_l1 = SSTable.write(
                [(b"doomed", b"val"), (b"survivor", b"alive")],
                basename="l1",
                directory=str(dpath),
            )
            sst_l0 = SSTable.write(
                [(b"doomed", _TOMBSTONE), (b"extra", b"ok")],
                basename="l0",
                directory=str(dpath),
            )
            engine.levels = [[sst_l0], [sst_l1]]
            engine._next_sst_id = 10
            result = engine.leveled_compact()
            assert result is not None
            merged = engine.levels[1][0]
            assert merged.get(b"doomed") is None
            assert merged.get(b"survivor") == b"alive"
            assert merged.get(b"extra") == b"ok"

    def test_tombstone_only_key_absent_from_merged_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sst_a = _write_sst([(b"deleted", _TOMBSTONE)], "src", d)
            sst_b = _write_sst([(b"survivor", b"val")], "tgt", d)
            compaction = LeveledCompaction(
                source_level=0,
                source_sst=sst_a,
                target_ssts=[sst_b],
            )
            result = compaction.run(d, "merged")
            assert len(result) == 1
            assert result[0].get(b"deleted") is None
            assert result[0].get(b"survivor") == b"val"


class TestCompactionIdempotence:
    def test_tiered_compact_then_again_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"a", b"1")
            engine.flush()
            engine.put(b"b", b"2")
            engine.flush()
            first = engine.tiered_compact()
            assert first is not None
            second = engine.tiered_compact()
            assert second is None

    def test_leveled_compact_then_again_no_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            sst_l1 = SSTable.write(
                [(b"b", b"old")],
                basename="l1",
                directory=str(dpath),
            )
            sst_l0 = SSTable.write(
                [(b"b", b"new")],
                basename="l0",
                directory=str(dpath),
            )
            engine.levels = [[sst_l0], [sst_l1]]
            engine._next_sst_id = 5
            first = engine.leveled_compact()
            assert first is not None
            second = engine.leveled_compact()
            assert second is None

    def test_compaction_preserves_get_after_multiple_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            engine = LSMEngine(directory=dpath)
            engine.put(b"persist", b"value")
            engine.flush()
            engine.put(b"persist", b"value2")
            engine.flush()
            engine.tiered_compact()
            engine.put(b"persist", b"value3")
            engine.flush()
            engine.tiered_compact()
            assert engine.get(b"persist") == b"value3"
