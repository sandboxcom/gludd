"""Deep tests for skip list v2 (concurrent, indexed, lock-free)."""

from __future__ import annotations

import random
import threading
from unittest.mock import patch

from general_ludd.algorithms.skip_list_v2 import (
    IndexedSkipList,
    LockFreeSkipList,
    SkipList,
)


class TestSkipListBasics:
    def test_empty_list(self) -> None:
        sl: SkipList[int, str] = SkipList()
        assert len(sl) == 0
        assert 1 not in sl
        assert sl.items() == []
        assert sl.keys() == []
        assert sl.values() == []

    def test_insert_and_contains(self) -> None:
        sl: SkipList[int, str] = SkipList()
        assert sl.insert(5, "five")
        assert 5 in sl
        assert len(sl) == 1

    def test_insert_duplicate_overwrites(self) -> None:
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        inserted = sl.insert(1, "b")
        assert not inserted
        assert sl[1] == "b"
        assert len(sl) == 1

    def test_getitem_and_setitem(self) -> None:
        sl: SkipList[int, str] = SkipList()
        sl[10] = "ten"
        assert sl[10] == "ten"
        sl[10] = "TEN"
        assert sl[10] == "TEN"

    def test_getitem_raises_keyerror(self) -> None:
        sl: SkipList[int, str] = SkipList()
        try:
            _ = sl[99]
            raise AssertionError("expected KeyError")
        except KeyError:
            pass

    def test_delitem(self) -> None:
        sl: SkipList[int, str] = SkipList()
        for i in range(5):
            sl.insert(i, str(i))
        del sl[2]
        assert 2 not in sl
        assert len(sl) == 4
        assert sl.items() == [(0, "0"), (1, "1"), (3, "3"), (4, "4")]

    def test_delitem_raises_keyerror(self) -> None:
        sl: SkipList[int, str] = SkipList()
        try:
            del sl[42]
            raise AssertionError("expected KeyError")
        except KeyError:
            pass

    def test_get_default(self) -> None:
        sl: SkipList[int, str] = SkipList()
        sl.insert(3, "x")
        assert sl.get(3) == "x"
        assert sl.get(99) is None
        assert sl.get(99, "fallback") == "fallback"

    def test_delete_nonexistent(self) -> None:
        sl: SkipList[int, str] = SkipList()
        sl.insert(1, "a")
        assert not sl.delete(99)
        assert len(sl) == 1

    def test_min_max(self) -> None:
        sl: SkipList[int, str] = SkipList()
        assert sl.min() is None
        assert sl.max() is None
        for k in [7, 2, 9, 1, 5]:
            sl.insert(k, str(k))
        assert sl.min() == (1, "1")
        assert sl.max() == (9, "9")

    def test_range_query(self) -> None:
        sl: SkipList[int, str] = SkipList()
        for k in range(10):
            sl.insert(k, str(k))
        assert sl.range(3, 7) == [(3, "3"), (4, "4"), (5, "5"), (6, "6"), (7, "7")]
        assert sl.range(0, 0) == [(0, "0")]
        assert sl.range(100, 200) == []

    def test_many_inserts_sorted(self) -> None:
        sl: SkipList[int, int] = SkipList()
        n = 200
        for i in range(n):
            sl.insert(i, i * 10)
        assert len(sl) == n
        for i in range(n):
            assert i in sl
            assert sl.get(i) == i * 10

    def test_many_inserts_random(self) -> None:
        rng = random.Random(42)
        keys = list(range(200))
        rng.shuffle(keys)
        sl: SkipList[int, int] = SkipList()
        for k in keys:
            sl.insert(k, k * k)
        assert len(sl) == 200
        assert sl.items() == [(k, k * k) for k in sorted(keys)]

    def test_many_deletes(self) -> None:
        sl: SkipList[int, str] = SkipList()
        for k in range(100):
            sl.insert(k, str(k))
        for k in [3, 17, 42, 56, 78, 99]:
            assert sl.delete(k)
        assert len(sl) == 94
        for k in [3, 17, 42, 56, 78, 99]:
            assert k not in sl

    def test_iteration_order(self) -> None:
        sl: SkipList[int, str] = SkipList()
        for k in [9, 3, 7, 1, 5]:
            sl.insert(k, str(k))
        assert list(sl) == [1, 3, 5, 7, 9]


class TestSkipListConcurrency:
    def test_concurrent_inserts_no_crash(self) -> None:
        sl: SkipList[int, int] = SkipList()
        errors: list[Exception] = []

        def worker(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    sl.insert(i, i)
            except Exception as exc:
                errors.append(exc)

        threads = []
        for t in range(4):
            start = t * 250
            end = start + 250
            threads.append(threading.Thread(target=worker, args=(start, end)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(sl) == 1000

    def test_concurrent_inserts_and_reads(self) -> None:
        sl: SkipList[int, str] = SkipList()
        for i in range(100):
            sl.insert(i, str(i))
        errors: list[Exception] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                for i in range(100, 500):
                    sl.insert(i, str(i))
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                while not stop.is_set():
                    _ = sl.get(0)
                    _ = len(sl)
            except Exception as exc:
                errors.append(exc)

        w = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(3)]
        w.start()
        for r in readers:
            r.start()
        w.join()
        stop.set()
        for r in readers:
            r.join()

        assert len(errors) == 0
        assert 0 in sl


class TestLockFreeSkipListBasics:
    def test_insert_and_contains(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        assert sl.insert(1, "a")
        assert sl.insert(2, "b")
        assert sl.insert(3, "c")
        assert 1 in sl
        assert 2 in sl
        assert 3 in sl
        assert 4 not in sl
        assert len(sl) == 3

    def test_getitem_and_setitem(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        sl[5] = "five"
        assert sl[5] == "five"
        sl[5] = "FIVE"
        assert sl[5] == "FIVE"

    def test_delete(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        for i in range(10):
            sl.insert(i, str(i))
        assert sl.delete(5)
        assert 5 not in sl
        assert len(sl) == 9

    def test_delitem(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        sl.insert(7, "x")
        del sl[7]
        assert 7 not in sl

    def test_min_max(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        assert sl.min() is None
        assert sl.max() is None
        for k in [10, 2, 8, 1]:
            sl.insert(k, str(k))
        assert sl.min() == (1, "1")
        assert sl.max() == (10, "10")

    def test_range_query(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        for k in range(5):
            sl.insert(k, str(k))
        assert sl.range(1, 3) == [(1, "1"), (2, "2"), (3, "3")]

    def test_many_inserts_random(self) -> None:
        rng = random.Random(7)
        keys = list(range(150))
        rng.shuffle(keys)
        sl: LockFreeSkipList[int, int] = LockFreeSkipList()
        for k in keys:
            sl.insert(k, k * 3)
        assert len(sl) == 150
        for k in keys:
            assert sl.get(k) == k * 3

    def test_get_default(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        assert sl.get(99) is None
        assert sl.get(99, "missing") == "missing"

    def test_iteration(self) -> None:
        sl: LockFreeSkipList[int, str] = LockFreeSkipList()
        for k in [8, 3, 5, 1]:
            sl.insert(k, str(k))
        assert list(sl) == [1, 3, 5, 8]


class TestLockFreeConcurrency:
    def test_concurrent_inserts_wait_free_reads(self) -> None:
        sl: LockFreeSkipList[int, int] = LockFreeSkipList()
        errors: list[Exception] = []

        def writer(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    sl.insert(i, i)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(1000):
                    _ = 0 in sl
                    _ = sl.get(0)
            except Exception as exc:
                errors.append(exc)

        threads = []
        for t in range(4):
            start = t * 200
            end = start + 200
            threads.append(threading.Thread(target=writer, args=(start, end)))
        for _ in range(2):
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(sl) == 800

    def test_concurrent_insert_and_delete(self) -> None:
        sl: LockFreeSkipList[int, int] = LockFreeSkipList()
        for i in range(200):
            sl.insert(i, i)
        errors: list[Exception] = []

        def inserter(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    sl.insert(i, i)
            except Exception as exc:
                errors.append(exc)

        def deleter(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    sl.delete(i)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=inserter, args=(200, 400))
        t2 = threading.Thread(target=deleter, args=(0, 100))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0


class TestIndexedSkipListBasics:
    def test_insert_and_rank(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in [5, 2, 8, 1, 3]:
            sl.insert(k, str(k))
        assert len(sl) == 5
        assert sl.rank(1) == 0
        assert sl.rank(3) == 2
        assert sl.rank(9) == 5

    def test_select_by_rank(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in [5, 2, 8, 1, 3]:
            sl.insert(k, str(k))
        assert sl.select(0) == (1, "1")
        assert sl.select(2) == (3, "3")
        assert sl.select(4) == (8, "8")

    def test_select_out_of_range(self) -> None:
        sl: IndexedSkipList[int, int] = IndexedSkipList()
        sl.insert(1, 1)
        try:
            sl.select(5)
            raise AssertionError("expected IndexError")
        except IndexError:
            pass
        try:
            sl.select(-1)
            raise AssertionError("expected IndexError")
        except IndexError:
            pass

    def test_delete_updates_rank(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in range(10):
            sl.insert(k, str(k))
        assert sl.delete(5)
        assert len(sl) == 9
        assert sl.select(5) == (6, "6")
        assert sl.rank(7) == 6

    def test_range_query(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in range(10):
            sl.insert(k, str(k))
        assert sl.range(2, 5) == [(2, "2"), (3, "3"), (4, "4"), (5, "5")]

    def test_min_max(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        assert sl.min() is None
        assert sl.max() is None
        sl.insert(42, "a")
        assert sl.min() == (42, "a")
        assert sl.max() == (42, "a")
        sl.insert(7, "b")
        sl.insert(99, "c")
        assert sl.min() == (7, "b")
        assert sl.max() == (99, "c")

    def test_items_ordered(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in [9, 1, 6, 3]:
            sl.insert(k, str(k))
        assert sl.items() == [(1, "1"), (3, "3"), (6, "6"), (9, "9")]

    def test_higher_level_insert_preserves_residual_span(self) -> None:
        sl: IndexedSkipList[int, int] = IndexedSkipList()
        with patch(
            "general_ludd.algorithms.skip_list_v2._random_level",
            side_effect=[2, 2, 0, 2],
        ):
            for key in [0, 10, 5, 2]:
                assert sl.insert(key, key)

        assert sl.items() == [(0, 0), (2, 2), (5, 5), (10, 10)]
        assert [sl.select(rank)[0] for rank in range(4)] == [0, 2, 5, 10]
        assert [sl.rank(key) for key in [0, 2, 5, 10]] == [0, 1, 2, 3]

    def test_large_random(self) -> None:
        rng = random.Random(99)
        n = 300
        keys = list(range(n))
        rng.shuffle(keys)
        sl: IndexedSkipList[int, int] = IndexedSkipList()
        for k in keys:
            sl.insert(k, k * k)
        assert len(sl) == n
        for i in range(n):
            assert sl.select(i)[0] == i
        for k in keys:
            assert sl.rank(k) == k

    def test_getitem_contains_delitem(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        sl.insert(42, "answer")
        assert 42 in sl
        assert sl[42] == "answer"
        assert sl.get(99) is None
        del sl[42]
        assert 42 not in sl

    def test_iteration(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        for k in [4, 2, 6, 1]:
            sl.insert(k, str(k))
        assert list(sl) == [1, 2, 4, 6]

    def test_duplicate_insert(self) -> None:
        sl: IndexedSkipList[int, str] = IndexedSkipList()
        assert sl.insert(1, "a")
        assert not sl.insert(1, "b")
        assert sl[1] == "b"
        assert len(sl) == 1
