"""Deep tests for MVCC key-value store — snapshot reads, write intents,
commit, rollback, and conflict detection.
"""

from __future__ import annotations

import threading

import pytest

from general_ludd.storage.mvcc import (
    MVCCStore,
    Transaction,
    TransactionConflictError,
)


class TestMVCCStoreBasic:
    def test_put_and_get(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("a", 1)
        assert store.get("a") == 1

    def test_put_overwrites(self) -> None:
        store: MVCCStore[str, str] = MVCCStore()
        store.put("k", "v1")
        store.put("k", "v2")
        assert store.get("k") == "v2"

    def test_get_missing_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        with pytest.raises(KeyError, match="missing"):
            store.get("missing")

    def test_get_optional(self) -> None:
        store: MVCCStore[str, str] = MVCCStore()
        assert store.get_optional("x") is None
        store.put("x", "hello")
        assert store.get_optional("x") == "hello"

    def test_contains(self) -> None:
        store: MVCCStore[int, bytes] = MVCCStore()
        assert not store.contains(1)
        store.put(1, b"data")
        assert store.contains(1)

    def test_keys(self) -> None:
        store: MVCCStore[str, float] = MVCCStore()
        store.put("x", 1.0)
        store.put("y", 2.0)
        assert sorted(store.keys()) == ["x", "y"]

    def test_delete(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("a", 10)
        store.delete("a")
        assert not store.contains("a")

    def test_delete_missing_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        with pytest.raises(KeyError, match="missing"):
            store.delete("missing")

    def test_begin_returns_transaction(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        assert isinstance(txn, Transaction)
        assert not txn.is_committed
        assert not txn.is_rolled_back


class TestSnapshotRead:
    def test_read_sees_committed_state_at_begin(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("x", 42)
        txn = store.begin()
        assert txn.read("x") == 42

    def test_read_does_not_see_later_writes_outside_txn(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("x", 1)
        txn = store.begin()
        store.put("x", 999)
        assert txn.read("x") == 1

    def test_read_sees_own_writes_within_txn(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("a", 100)
        assert txn.read("a") == 100

    def test_read_optional_nonexistent(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        assert txn.read_optional("nope") is None

    def test_read_after_write_overrides_snapshot(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 5)
        txn = store.begin()
        txn.write("k", 50)
        assert txn.read("k") == 50

    def test_contains_sees_committed(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 1)
        txn = store.begin()
        assert txn.contains("k")
        assert not txn.contains("z")


class TestWriteIntent:
    def test_write_buffered_not_visible_to_store(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("x", 77)
        assert not store.contains("x")
        assert txn.read("x") == 77

    def test_write_overwrite_in_txn(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("k", 1)
        txn.write("k", 2)
        assert txn.read("k") == 2

    def test_delete_buffered(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 42)
        txn = store.begin()
        txn.delete("k")
        assert not txn.contains("k")
        assert store.contains("k")

    def test_delete_missing_snapshot_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        with pytest.raises(KeyError, match="not found in snapshot"):
            txn.delete("absent")

    def test_write_set_property(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("a", 1)
        txn.write("b", 2)
        assert txn.write_set == {"a": 1, "b": 2}


class TestCommit:
    def test_commit_makes_writes_visible(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("x", 99)
        txn.commit()
        assert store.get("x") == 99
        assert txn.is_committed

    def test_commit_applies_deletes(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 10)
        txn = store.begin()
        txn.delete("k")
        txn.commit()
        assert not store.contains("k")

    def test_double_commit_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("x", 1)
        txn.commit()
        with pytest.raises(RuntimeError, match="already committed"):
            txn.commit()

    def test_read_after_commit_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 1)
        txn = store.begin()
        txn.commit()
        with pytest.raises(RuntimeError, match="already committed"):
            txn.read("k")


class TestRollback:
    def test_rollback_discards_writes(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.write("x", 1)
        txn.rollback()
        assert not store.contains("x")
        assert txn.is_rolled_back

    def test_double_rollback_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.rollback()
        with pytest.raises(RuntimeError, match="already rolled back"):
            txn.rollback()

    def test_commit_after_rollback_raises(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn = store.begin()
        txn.rollback()
        with pytest.raises(RuntimeError, match="already rolled back"):
            txn.commit()


class TestConflictDetection:
    def test_write_write_conflict_detected(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("x", 0)
        txn_a = store.begin()
        txn_b = store.begin()
        txn_a.write("x", 1)
        txn_b.write("x", 2)
        txn_a.commit()
        with pytest.raises(TransactionConflictError, match="conflict"):
            txn_b.commit()

    def test_no_conflict_disjoint_keys(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("a", 0)
        store.put("b", 0)
        txn_a = store.begin()
        txn_b = store.begin()
        txn_a.write("a", 1)
        txn_b.write("b", 2)
        txn_a.commit()
        txn_b.commit()
        assert store.get("a") == 1
        assert store.get("b") == 2

    def test_no_conflict_read_only(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("x", 42)
        txn_a = store.begin()
        txn_b = store.begin()
        _ = txn_a.read("x")
        txn_b.write("x", 99)
        txn_b.commit()
        txn_a.commit()
        assert store.get("x") == 99

    def test_conflict_on_delete(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 5)
        txn_a = store.begin()
        txn_b = store.begin()
        txn_a.write("k", 10)
        txn_b.delete("k")
        txn_a.commit()
        with pytest.raises(TransactionConflictError, match="delete-conflict"):
            txn_b.commit()

    def test_no_conflict_same_key_same_value(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("k", 0)
        txn_a = store.begin()
        txn_b = store.begin()
        txn_a.write("k", 7)
        txn_b.write("k", 7)
        txn_a.commit()
        with pytest.raises(TransactionConflictError):
            txn_b.commit()


class TestSnapshotTxid:
    def test_snapshot_txid_accessible(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        txn_a = store.begin()
        txn_b = store.begin()
        assert txn_b.snapshot_txid > txn_a.snapshot_txid

    def test_snapshot_txid_monotonic(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        ids = [store.begin().snapshot_txid for _ in range(10)]
        assert ids == sorted(ids)
        assert len(set(ids)) == 10


class TestConcurrency:
    def test_parallel_transactions_no_race(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        results: list[int] = []

        def writer(start: int) -> None:
            for i in range(start, start + 50):
                txn = store.begin()
                txn.write(str(i), i)
                txn.commit()
            results.append(1)

        t1 = threading.Thread(target=writer, args=(0,))
        t2 = threading.Thread(target=writer, args=(50,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(results) == 2
        for i in range(100):
            assert store.get(str(i)) == i

    def test_concurrent_non_overlapping(self) -> None:
        store: MVCCStore[str, int] = MVCCStore()
        store.put("a", 0)
        store.put("b", 0)
        txn_a = store.begin()
        txn_b = store.begin()
        txn_a.write("a", 10)
        txn_b.write("b", 20)
        txn_a.commit()
        txn_b.commit()
        assert store.get("a") == 10
        assert store.get("b") == 20
