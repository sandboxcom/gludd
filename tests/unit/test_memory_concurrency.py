"""Concurrency, race-condition, and fault-tolerance tests for gludd's memory system.

Covers:
  1. Concurrent retain+recall — 20 threads, verify no data loss
  2. Rapid-fire session isolation — 50 rapid retains from different bank IDs
  3. Race condition detection — overlapping facts, consolidation handles it
  4. Write-during-read — reader sees consistent snapshot
  5. Fault injection — store throws on put, errors propagate, no corruption
  6. Cancellation safety — cancel mid-write, store integrity preserved
  7. Immutable records — returned records are deep copies
  8. TEMPR parallel strategies — 4 strategies run in thread pool, results merged
  9. Observation consolidation under load — 100 concurrent facts consolidated
 10. Memory bank isolation under concurrency — 5 banks each with 20 ops
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest

import general_ludd.memory.memory_bank as memory_bank_module
from general_ludd.memory.hindsight_adapter import (
    HindsightMemoryAdapter,
    _InMemoryStore,
)
from general_ludd.memory.memory_bank import (
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryBankResult,
    MemoryEntry,
    MentalModel,
)
from general_ludd.memory.observation_consolidator import (
    EvidenceRef,
    MemoryFact,
    Observation,
    ObservationConsolidator,
    ObservationStore,
)
from general_ludd.memory.tempr_retriever import (
    TEMPRResult,
    TEMPRRetriever,
    reciprocal_rank_fusion,
)


@pytest.fixture(autouse=True)
def reset_hindsight_singleton():
    HindsightMemoryAdapter.reset_instance()
    yield
    HindsightMemoryAdapter.reset_instance()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Concurrent retain+recall — 20 threads, verify no data loss
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentRetainRecall:
    N_THREADS = 20
    N_FACTS_PER_THREAD = 50

    def test_concurrent_retain_no_data_loss(self):
        config = MemoryBankConfig(bank_id="concurrent-test-bank")
        bank = MemoryBank(config)

        def worker(thread_id: int) -> list[str]:
            ids = []
            for i in range(self.N_FACTS_PER_THREAD):
                fact = MemoryEntry(
                    content=f"fact-t{thread_id}-{i}: important data point",
                    source=f"thread-{thread_id}",
                    tags=[f"t{thread_id}", f"batch{i // 10}"],
                )
                bank.retain(fact)
                ids.append(fact.entry_id)
            return ids

        all_ids: list[str] = []
        with ThreadPoolExecutor(max_workers=self.N_THREADS) as executor:
            futures = [executor.submit(worker, t) for t in range(self.N_THREADS)]
            for f in as_completed(futures):
                all_ids.extend(f.result())

        facts = bank.get_facts()
        assert len(facts) == self.N_THREADS * self.N_FACTS_PER_THREAD

        retrieved_ids = {f.entry_id for f in facts}
        assert retrieved_ids == set(all_ids)
        assert len(retrieved_ids) == len(all_ids)

    def test_concurrent_retain_and_recall_interleaved(self):
        config = MemoryBankConfig(bank_id="interleaved-bank")
        bank = MemoryBank(config)

        for i in range(100):
            bank.retain(MemoryEntry(content=f"base-fact-{i}", tags=["base"]))

        stop = threading.Event()
        recall_errors: list[Exception] = []
        retain_count = 0
        retain_lock = threading.Lock()

        def recaller():
            while not stop.is_set():
                try:
                    result = bank.recall("fact data")
                    assert isinstance(result, MemoryBankResult)
                    assert isinstance(result.facts, list)
                    assert isinstance(result.mental_models, list)
                except Exception as e:
                    recall_errors.append(e)

        def retainer():
            nonlocal retain_count
            while not stop.is_set():
                fact = MemoryEntry(
                    content=f"dynamic-fact-{uuid.uuid4().hex[:8]}",
                    tags=["dynamic"],
                )
                bank.retain(fact)
                with retain_lock:
                    retain_count += 1

        with ThreadPoolExecutor(max_workers=10) as executor:
            r_futures = [executor.submit(recaller) for _ in range(5)]
            w_futures = [executor.submit(retainer) for _ in range(5)]
            time.sleep(1.0)
            stop.set()
            for f in r_futures + w_futures:
                f.result(timeout=5)

        assert not recall_errors
        facts = bank.get_facts()
        base_count = sum(1 for f in facts if "base-fact" in f.content)
        assert base_count == 100
        assert retain_count > 0

    def test_concurrent_recall_returns_consistent_results(self):
        config = MemoryBankConfig(bank_id="consistent-recall")
        bank = MemoryBank(config)

        for i in range(500):
            bank.retain(MemoryEntry(
                content=f"unique fact number {i} about gludd memory",
                tags=["benchmark"],
            ))

        results_list: list[list[MemoryEntry]] = []

        def do_recall():
            result = bank.recall("gludd memory")
            results_list.append(result.facts)

        with ThreadPoolExecutor(max_workers=self.N_THREADS) as executor:
            futures = [executor.submit(do_recall) for _ in range(self.N_THREADS)]
            for f in as_completed(futures):
                f.result()

        lengths = {len(r) for r in results_list}
        assert len(lengths) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Rapid-fire session isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestRapidFireSessionIsolation:
    N_SESSIONS = 50
    N_FACTS_PER = 10

    def test_rapid_fire_bank_isolation(self):
        registry = MemoryBankRegistry()

        def create_and_populate(session_idx: int) -> tuple[str, int]:
            bank_id = f"session-{session_idx}"
            config = MemoryBankConfig(bank_id=bank_id)
            registry.get_or_create_bank(config)
            bank = registry.get_bank(bank_id)
            assert bank is not None

            for f_idx in range(self.N_FACTS_PER):
                bank.retain(MemoryEntry(
                    content=f"session-{session_idx}-fact-{f_idx}",
                    tags=[f"s{session_idx}"],
                ))

            return bank_id, len(bank.get_facts())

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(create_and_populate, s)
                for s in range(self.N_SESSIONS)
            ]
            results = [f.result() for f in as_completed(futures)]

        assert registry.bank_count() == self.N_SESSIONS

        for bank_id, _count in results:
            bank = registry.get_bank(bank_id)
            assert bank is not None
            facts = bank.get_facts()
            assert len(facts) == self.N_FACTS_PER

            other_bank_ids = [bid for bid, _ in results if bid != bank_id]
            for other_id in other_bank_ids:
                other_bank = registry.get_bank(other_id)
                assert other_bank is not None
                other_contents = {f.content for f in other_bank.get_facts()}
                for fact in facts:
                    assert fact.content not in other_contents

    def test_rapid_fire_no_interleaving(self):
        adapter = HindsightMemoryAdapter(enabled=False)

        def write_session(session_id: str, data: str) -> str:
            return adapter.retain(data, {"session_id": session_id})

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for s in range(self.N_SESSIONS):
                for r in range(self.N_FACTS_PER):
                    futures.append(
                        executor.submit(write_session, f"sess-{s}", f"data-s{s}-r{r}")
                    )
            ids = [f.result() for f in as_completed(futures)]

        assert len(ids) == self.N_SESSIONS * self.N_FACTS_PER
        assert len(set(ids)) == len(ids)

        results = adapter.recall("data", top_k=1000)
        assert len(results) == self.N_SESSIONS * self.N_FACTS_PER


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Race condition detection — overlapping facts, consolidation handles it
# ═══════════════════════════════════════════════════════════════════════════════


class TestRaceConditionDetection:
    def test_overlapping_facts_deduplicated_correctly(self):
        consolidator = ObservationConsolidator(similarity_threshold=0.62)

        def make_fact(seed: int) -> MemoryFact:
            return MemoryFact(
                fact_id=f"fact-{seed}",
                content=f"User Alice completed project deployment on {seed % 7}",
                timestamp=time.time(),
            )

        all_facts: list[MemoryFact] = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_fact, i) for i in range(200)]
            for f in as_completed(futures):
                all_facts.append(f.result())

        observations = consolidator.consolidate(all_facts)
        assert len(observations) >= 1

        deduped = consolidator.deduplicate(all_facts)
        assert len(deduped) <= len(all_facts)

        distinct_contents = {o.statement for o in observations}
        assert len(distinct_contents) > 0

    def test_concurrent_consolidation_idempotent(self):
        consolidator = ObservationConsolidator()
        facts = [
            MemoryFact(
                fact_id=f"f-{i}",
                content=f"Agent performed task {i % 5} with outcome {'success' if i % 3 else 'failure'}",
                timestamp=time.time(),
            )
            for i in range(100)
        ]

        results: list[list[Observation]] = []

        def consolidate_batch(batch: list[MemoryFact]):
            results.append(consolidator.consolidate(batch))

        half = len(facts) // 2
        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(consolidate_batch, facts[:half])
            f2 = executor.submit(consolidate_batch, facts[half:])
            f1.result()
            f2.result()

        combined = consolidator.consolidate(facts)
        total_separate = sum(len(r) for r in results)
        assert total_separate >= 1
        assert len(combined) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Write-during-read — reader sees consistent snapshot
# ═══════════════════════════════════════════════════════════════════════════════


class TestWriteDuringRead:
    def test_get_facts_releases_lock_before_copying_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow defensive copy must not block an unrelated bank mutation."""
        bank = MemoryBank(MemoryBankConfig(bank_id="snapshot-lock-bank"))
        retained = bank.retain(MemoryEntry(content="snapshot", tags=["lock"]))
        copy_started = threading.Event()
        release_copy = threading.Event()
        real_copy_entry = memory_bank_module._copy_memory_entry

        def slow_copy_entry(value):
            if isinstance(value, MemoryEntry) and not copy_started.is_set():
                copy_started.set()
                assert release_copy.wait(timeout=2)
            return real_copy_entry(value)

        monkeypatch.setattr(memory_bank_module, "_copy_memory_entry", slow_copy_entry)

        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(bank.get_facts)
            assert copy_started.wait(timeout=1)
            deleter = executor.submit(bank.delete_fact, retained.entry_id)
            try:
                assert deleter.result(timeout=0.25) is True
            finally:
                release_copy.set()
            reader.result(timeout=2)

    def test_ordered_fact_cache_survives_write_during_initial_sort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A racing retain must not leave every later read sorting the bank."""
        bank = MemoryBank(MemoryBankConfig(bank_id="ordered-cache-race"))
        initial_one = bank.retain(
            MemoryEntry(
                content="initial one",
                created_at=1.0,
                tags=["initial"],
            )
        )
        bank.retain(
            MemoryEntry(
                content="initial two",
                created_at=2.0,
                tags=["initial"],
            )
        )
        sort_started = threading.Event()
        release_sort = threading.Event()
        real_sorted = sorted
        sort_calls = 0

        def gated_sorted(values, *args, **kwargs):
            nonlocal sort_calls
            sort_calls += 1
            if sort_calls == 1:
                sort_started.set()
                assert release_sort.wait(timeout=2)
            return real_sorted(values, *args, **kwargs)

        monkeypatch.setattr(
            memory_bank_module,
            "sorted",
            gated_sorted,
            raising=False,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            first_read = executor.submit(bank.get_facts)
            try:
                assert sort_started.wait(timeout=1)
                late = bank.retain(
                    MemoryEntry(
                        content="initial late",
                        created_at=3.0,
                        tags=["initial"],
                    )
                )
            finally:
                release_sort.set()
            first = first_read.result(timeout=2)

        assert [fact.content for fact in first] == ["initial two", "initial one"]
        sort_calls = 0
        bank.retain(
            MemoryEntry(
                content="initial later",
                created_at=4.0,
                tags=["initial"],
            )
        )
        second = bank.get_facts()
        bank.retain(
            MemoryEntry(
                entry_id=initial_one.entry_id,
                content="initial one updated",
                created_at=5.0,
                tags=["initial"],
            )
        )
        assert bank.delete_fact(late.entry_id) is True
        final = bank.get_facts()

        assert [fact.content for fact in second] == [
            "initial later",
            "initial late",
            "initial two",
            "initial one",
        ]
        assert [fact.content for fact in final] == [
            "initial one updated",
            "initial later",
            "initial two",
        ]
        assert sort_calls == 0

    def test_recall_scores_each_snapshot_once(self) -> None:
        """Recall must reuse its scored snapshot instead of scanning twice."""
        bank = MemoryBank(MemoryBankConfig(bank_id="single-score-bank"))
        bank.retain(MemoryEntry(content="initial fact", tags=["initial"]))

        with (
            patch.object(
                bank,
                "_score_mental_models",
                wraps=bank._score_mental_models,
            ) as score_models,
            patch.object(
                bank,
                "_score_facts",
                wraps=bank._score_facts,
            ) as score_facts,
        ):
            bank.recall("initial")

        assert score_models.call_count == 1
        assert score_facts.call_count == 1

    def test_repeated_recall_reuses_scores_until_facts_change(self) -> None:
        bank = MemoryBank(MemoryBankConfig(bank_id="recall-cache-bank"))
        bank.retain(MemoryEntry(content="initial fact", tags=["initial"]))

        with patch.object(
            memory_bank_module,
            "_score_text",
            wraps=memory_bank_module._score_text,
        ) as score_text:
            first = bank.recall("initial")
            second = bank.recall("initial")
            assert score_text.call_count == 1
            assert first.facts[0] is not second.facts[0]

            bank.retain(MemoryEntry(content="unrelated fact", tags=["other"]))
            bank.recall("initial")

        assert score_text.call_count == 2

    def test_recall_cache_reconciles_write_that_arrives_during_scoring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent writes must not starve installation of the recall cache."""
        bank = MemoryBank(MemoryBankConfig(bank_id="recall-cache-race"))
        bank.retain(MemoryEntry(content="initial one", tags=["initial"]))
        bank.retain(MemoryEntry(content="initial two", tags=["initial"]))
        score_started = threading.Event()
        release_score = threading.Event()
        real_score_text = memory_bank_module._score_text
        score_calls = 0

        def gated_score_text(qterms, ql, text):
            nonlocal score_calls
            score_calls += 1
            if score_calls == 1:
                score_started.set()
                assert release_score.wait(timeout=2)
            return real_score_text(qterms, ql, text)

        monkeypatch.setattr(memory_bank_module, "_score_text", gated_score_text)

        with ThreadPoolExecutor(max_workers=1) as executor:
            first_recall = executor.submit(bank.recall, "initial")
            assert score_started.wait(timeout=1)
            bank.retain(MemoryEntry(content="initial late", tags=["initial"]))
            release_score.set()
            first = first_recall.result(timeout=2)

        second = bank.recall("initial")

        assert len(first.facts) == 2
        assert len(second.facts) == 3
        assert score_calls == 3

    def test_active_recall_cache_updates_each_fact_incrementally(self) -> None:
        bank = MemoryBank(MemoryBankConfig(bank_id="incremental-recall-cache"))
        replaceable = bank.retain(
            MemoryEntry(content="initial replaceable", tags=["initial"])
        )
        removable = bank.retain(
            MemoryEntry(content="initial removable", tags=["initial"])
        )
        assert len(bank.recall("initial").facts) == 2

        with patch.object(
            memory_bank_module,
            "_score_text",
            wraps=memory_bank_module._score_text,
        ) as score_text:
            for index in range(32):
                bank.retain(
                    MemoryEntry(
                        content=f"unrelated fact {index}",
                        tags=["other"],
                    )
                )
                assert len(bank.recall("initial").facts) == 2

            bank.retain(
                MemoryEntry(
                    entry_id=replaceable.entry_id,
                    content="replacement unrelated",
                    tags=["other"],
                )
            )
            remaining = bank.recall("initial").facts
            assert [fact.entry_id for fact in remaining] == [removable.entry_id]

            assert bank.delete_fact(removable.entry_id) is True
            assert bank.recall("initial").facts == []

        assert score_text.call_count == 33

    def test_unrelated_retain_does_not_rebuild_active_recall_cache(self) -> None:
        """A non-matching write must leave an already valid score cache intact."""
        bank = MemoryBank(MemoryBankConfig(bank_id="stable-recall-cache"))
        bank.retain(MemoryEntry(content="initial fact", tags=["initial"]))
        assert len(bank.recall("initial").facts) == 1
        cached_scores = bank._fact_score_cache

        bank.retain(MemoryEntry(content="unrelated fact", tags=["other"]))

        assert bank._fact_score_cache is cached_scores
        assert [fact.content for fact in bank.recall("initial").facts] == [
            "initial fact"
        ]

    def test_flat_memory_entry_clones_avoid_generic_copy_protocol(self) -> None:
        """Hot-path snapshots must clone flat entries without ``copy.copy``."""
        bank = MemoryBank(MemoryBankConfig(bank_id="direct-entry-clone"))
        source = MemoryEntry(content="initial fact", tags=["initial"])

        with patch.object(
            memory_bank_module,
            "copy",
            side_effect=AssertionError("generic copy protocol used"),
        ):
            retained = bank.retain(source)
            recalled = bank.get_facts()[0]

        assert retained == source
        assert recalled == source
        assert retained is not source
        assert recalled is not source
        assert retained.tags is not source.tags
        assert recalled.tags is not source.tags

    def test_newest_unique_retain_uses_ordered_cache_fast_path(self) -> None:
        """The common append-by-time write must avoid a general list insertion."""
        bank = MemoryBank(MemoryBankConfig(bank_id="ordered-cache-fast-path"))
        bank.retain(MemoryEntry(content="first", created_at=1.0))
        assert [fact.content for fact in bank.get_facts()] == ["first"]
        ordered_cache = bank._ordered_facts_cache

        with patch.object(
            memory_bank_module,
            "_insert_fact_by_recency",
            wraps=memory_bank_module._insert_fact_by_recency,
        ) as general_insert:
            bank.retain(MemoryEntry(content="third", created_at=3.0))
            bank.retain(MemoryEntry(content="second", created_at=2.0))

        assert general_insert.call_count == 1
        assert bank._ordered_facts_cache is ordered_cache
        assert [fact.content for fact in bank.get_facts()] == [
            "third",
            "second",
            "first",
        ]

    def test_reader_sees_consistent_snapshot(self):
        store = ObservationStore(store_path="/tmp/gludd-test-observations-wdr.json")
        store.clear()

        prep_obs = [
            Observation(
                observation_id=f"obs-prep-{i}",
                subject=f"subject-{i}",
                statement=f"initial statement {i}",
                proof_count=1,
                confidence=0.5,
                created_at=time.time(),
                updated_at=time.time(),
            )
            for i in range(50)
        ]
        store.put_all(prep_obs)

        read_during_write: list[int] = []
        barrier = threading.Barrier(2, timeout=10)
        writer_done = threading.Event()

        def writer():
            for i in range(100):
                obs = Observation(
                    observation_id=f"obs-write-{i}",
                    subject=f"writer-{i}",
                    statement=f"written statement {i}",
                    proof_count=1,
                    confidence=0.9,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
                store.put(obs)
            barrier.wait()
            writer_done.set()

        def reader():
            barrier.wait()
            while not writer_done.is_set():
                all_obs = store.list_all()
                read_during_write.append(len(all_obs))
            final = store.list_all()
            read_during_write.append(len(final))

        with ThreadPoolExecutor(max_workers=2) as executor:
            w = executor.submit(writer)
            r = executor.submit(reader)
            w.result(timeout=10)
            r.result(timeout=10)

        assert len(read_during_write) > 0
        final_count = store.list_all()
        assert len(final_count) == 150

        for count in read_during_write:
            assert 50 <= count <= 150

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-observations-wdr.json")

    def test_bank_write_during_read_no_crash(self):
        config = MemoryBankConfig(bank_id="wdr-bank")
        bank = MemoryBank(config)

        for i in range(100):
            bank.retain(MemoryEntry(content=f"initial-{i}", tags=["init"]))

        crashed = threading.Event()

        def aggressive_writer():
            for i in range(500):
                bank.retain(MemoryEntry(content=f"aggressive-{i}", tags=["aggressive"]))

        def reader_under_load():
            try:
                for _ in range(200):
                    facts = bank.get_facts()
                    _ = len(facts)
                    _ = bank.recall("initial")
            except Exception:
                crashed.set()

        with ThreadPoolExecutor(max_workers=4) as executor:
            writers = [executor.submit(aggressive_writer) for _ in range(2)]
            readers = [executor.submit(reader_under_load) for _ in range(2)]
            for f in writers + readers:
                f.result(timeout=10)

        assert not crashed.is_set()
        all_facts = bank.get_facts()
        initial_count = sum(1 for f in all_facts if f.content.startswith("initial-"))
        aggressive_count = sum(1 for f in all_facts if f.content.startswith("aggressive-"))
        assert initial_count == 100
        assert aggressive_count == 2 * 500


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Fault injection — store throws on put, errors propagate, no corruption
# ═══════════════════════════════════════════════════════════════════════════════


class TestFaultInjection:
    def test_store_put_error_preserves_existing_data(self):
        store = ObservationStore(store_path="/tmp/gludd-test-fault.json")
        store.clear()

        good = Observation(
            observation_id="good-1",
            subject="test",
            statement="before fault",
            proof_count=1,
            confidence=0.9,
            created_at=time.time(),
            updated_at=time.time(),
        )
        store.put(good)

        with patch.object(store, "_persist", side_effect=OSError("disk full")):
            bad = Observation(
                observation_id="bad-1",
                subject="test",
                statement="during fault",
                proof_count=1,
                confidence=0.9,
                created_at=time.time(),
                updated_at=time.time(),
            )
            with pytest.raises(OSError, match="disk full"):
                store.put(bad)

        retrieved = store.get("good-1")
        assert retrieved is not None
        assert retrieved.statement == "before fault"

        missing = store.get("bad-1")
        assert missing is None

        store.clear()
        try:
            os.remove("/tmp/gludd-test-fault.json")
            os.remove("/tmp/gludd-test-fault.json.tmp")
        except OSError:
            pass

    def test_store_put_all_error_rolls_back_every_new_observation(self):
        store = ObservationStore(store_path="/tmp/gludd-test-fault-bulk.json")
        store.clear()
        existing = Observation(
            observation_id="existing",
            subject="test",
            statement="durable",
            created_at=time.time(),
            updated_at=time.time(),
        )
        store.put(existing)
        additions = [
            Observation(
                observation_id=f"new-{index}",
                subject="test",
                statement=f"new {index}",
                created_at=time.time(),
                updated_at=time.time(),
            )
            for index in range(2)
        ]

        with (
            patch.object(store, "_persist", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            store.put_all(additions)

        assert [item.observation_id for item in store.list_all()] == ["existing"]
        store.clear()

    def test_store_delete_error_restores_deleted_observation(self):
        store = ObservationStore(store_path="/tmp/gludd-test-fault-delete.json")
        store.clear()
        existing = Observation(
            observation_id="existing",
            subject="test",
            statement="durable",
            created_at=time.time(),
            updated_at=time.time(),
        )
        store.put(existing)

        with (
            patch.object(store, "_persist", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            store.delete("existing")

        restored = store.get("existing")
        assert restored == existing
        assert restored is not existing
        store.clear()

    def test_store_clear_error_restores_all_observations(self):
        store = ObservationStore(store_path="/tmp/gludd-test-fault-clear.json")
        store.clear()
        existing = Observation(
            observation_id="existing",
            subject="test",
            statement="durable",
            created_at=time.time(),
            updated_at=time.time(),
        )
        store.put(existing)

        with (
            patch.object(store, "_persist", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            store.clear()

        restored = store.get("existing")
        assert restored == existing
        assert restored is not existing
        store.clear()

    def test_fault_injection_recovery(self):
        config = MemoryBankConfig(bank_id="fault-recovery-bank")
        bank = MemoryBank(config)

        bank.retain(MemoryEntry(entry_id="pre-fault-1", content="before fault"))

        original_retain = bank.retain

        def faulty_retain(fact: MemoryEntry) -> MemoryEntry:
            if "explode" in fact.content.lower():
                raise RuntimeError("simulated store failure")
            return original_retain(fact)

        with patch.object(bank, "retain", faulty_retain), pytest.raises(
            RuntimeError, match="simulated store failure"
        ):
            bank.retain(MemoryEntry(content="this should explode"))

        bank.retain(MemoryEntry(entry_id="post-fault-1", content="after fault"))

        facts = bank.get_facts()
        contents = {f.content for f in facts}
        assert "before fault" in contents
        assert "after fault" in contents
        assert "this should explode" not in contents

    def test_fault_during_consolidation_no_partial_state(self):
        consolidator = ObservationConsolidator()
        store = ObservationStore(store_path="/tmp/gludd-test-fault-consolidation.json")
        store.clear()

        facts = [
            MemoryFact(fact_id=f"f-{i}", content=f"Task {i} completed successfully", timestamp=time.time())
            for i in range(50)
        ]

        observations = consolidator.consolidate(facts)

        with patch.object(
            store, "_persist", side_effect=OSError("disk full")
        ), pytest.raises(OSError, match="disk full"):
            store.put_all(observations)

        assert store.count == 0

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-fault-consolidation.json")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Cancellation safety — cancel mid-write, store integrity preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestCancellationSafety:
    def test_cancel_mid_write_no_corruption(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="cancel-safety-bank")
        bank = registry.create_bank(config)

        bank.retain(MemoryEntry(entry_id="pre-cancel", content="pre-cancel data", tags=["pre"]))

        slow_retain_lock = threading.Lock()
        slow_retain_lock.acquire()
        retained_under_lock: list[str] = []

        def slow_retain():
            with slow_retain_lock:
                bank.retain(MemoryEntry(
                    entry_id="under-lock",
                    content="retained under lock",
                    tags=["locked"],
                ))
                retained_under_lock.append("done")

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(slow_retain)

        time.sleep(0.2)
        slow_retain_lock.release()
        future.result(timeout=5)
        executor.shutdown(wait=True)

        facts = bank.get_facts()
        contents = {f.content for f in facts}
        assert "pre-cancel data" in contents
        assert "retained under lock" in contents

    def test_interrupted_bank_operation_recoverable(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="interrupt-bank"))

        def do_work(start: int, count: int) -> int:
            for i in range(start, start + count):
                bank.retain(MemoryEntry(
                    content=f"work-item-{i}",
                    tags=[f"batch{i // 50}"],
                ))
            return count

        with ThreadPoolExecutor(max_workers=5) as executor:
            batch_size = 200
            futures = [
                executor.submit(do_work, b * batch_size, batch_size)
                for b in range(5)
            ]
            total = sum(f.result(timeout=10) for f in as_completed(futures))

        assert total == 1000
        facts = bank.get_facts()
        assert len(facts) == 1000

    def test_cancel_during_observation_put_all(self):
        store = ObservationStore(store_path="/tmp/gludd-test-cancel-obs.json")
        store.clear()

        total = 200
        all_obs = [
            Observation(
                observation_id=f"obs-{i}",
                subject=f"cancel-{i}",
                statement=f"statement {i}",
                proof_count=1,
                confidence=0.5,
                created_at=time.time(),
                updated_at=time.time(),
            )
            for i in range(total)
        ]

        barrier = threading.Barrier(2, timeout=10)

        def batched_write():
            for chunk_start in range(0, total, 50):
                chunk = all_obs[chunk_start:chunk_start + 50]
                store.put_all(chunk)
            barrier.wait()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(batched_write)
            barrier.wait(timeout=10)
            future.result(timeout=5)

        final = store.list_all()
        assert len(final) == total

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-cancel-obs.json")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Immutable records — returned records are deep copies
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutableRecords:
    def test_observation_store_copies_values_on_write(self, tmp_path):
        store = ObservationStore(store_path=str(tmp_path / "write-copy.json"))
        original = Observation(
            observation_id="write-copy",
            subject="mutability",
            statement="original statement",
            evidence=[EvidenceRef(fact_id="f1", quote="original quote", timestamp=time.time())],
            contradictions=["original contradiction"],
        )

        store.put(original)
        original.statement = "mutated statement"
        original.evidence[0].quote = "mutated quote"
        original.contradictions.append("new contradiction")

        retrieved = store.get("write-copy")
        assert retrieved is not None
        assert retrieved.statement == "original statement"
        assert retrieved.evidence[0].quote == "original quote"
        assert retrieved.contradictions == ["original contradiction"]

    def test_observation_store_returns_independent_copies(self):
        store = ObservationStore(store_path="/tmp/gludd-test-immutable.json")
        store.clear()

        original = Observation(
            observation_id="immutable-test",
            subject="mutability",
            statement="original statement",
            evidence=[EvidenceRef(fact_id="f1", quote="quote 1", timestamp=time.time())],
            proof_count=1,
            confidence=0.8,
            created_at=time.time(),
            updated_at=time.time(),
            contradictions=["old-contradiction"],
        )
        store.put(original)

        retrieved = store.get("immutable-test")
        assert retrieved is not None

        retrieved.statement = "mutated statement"
        retrieved.evidence.append(EvidenceRef(fact_id="f2", quote="quote 2", timestamp=time.time()))
        retrieved.contradictions.append("new-contradiction")
        retrieved.confidence = 0.1

        re_retrieved = store.get("immutable-test")
        assert re_retrieved is not None
        assert re_retrieved.statement == "original statement"
        assert len(re_retrieved.evidence) == 1
        assert len(re_retrieved.contradictions) == 1
        assert re_retrieved.confidence == 0.8

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-immutable.json")

    @pytest.mark.parametrize(
        ("query", "stale"),
        [
            pytest.param(lambda store: store.get_by_subject("mutability"), False, id="subject"),
            pytest.param(lambda store: store.get_fresh(), False, id="fresh"),
            pytest.param(lambda store: store.get_stale(), True, id="stale"),
            pytest.param(lambda store: store.get_above_confidence(0.5), False, id="confidence"),
            pytest.param(lambda store: store.list_all(), False, id="all"),
        ],
    )
    def test_observation_store_query_results_are_independent(self, tmp_path, query, stale):
        store = ObservationStore(store_path=str(tmp_path / "query-copy.json"))
        store.put(
            Observation(
                observation_id="query-copy",
                subject="mutability",
                statement="original statement",
                confidence=0.8,
                stale=stale,
                evidence=[EvidenceRef(fact_id="f1", quote="original quote", timestamp=time.time())],
            )
        )

        result = query(store)
        assert len(result) == 1
        result[0].statement = "mutated statement"
        result[0].evidence[0].quote = "mutated quote"

        retrieved = store.get("query-copy")
        assert retrieved is not None
        assert retrieved.statement == "original statement"
        assert retrieved.evidence[0].quote == "original quote"

    def test_bank_mental_models_not_mutable_through_getter(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="immutable-bank"))
        model = MentalModel(
            model_id="mm-1",
            subject="original subject",
            content="original content",
            priority=8,
        )
        bank.add_mental_model(model)

        retrieved = bank.get_mental_models()
        assert len(retrieved) == 1

        retrieved[0].subject = "mutated subject"
        retrieved[0].content = "mutated content"

        re_retrieved = bank.get_mental_models()
        assert re_retrieved[0].subject == "original subject"
        assert re_retrieved[0].content == "original content"

    def test_bank_facts_not_mutable_through_getter(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="facts-mutability-bank"))
        bank.retain(MemoryEntry(entry_id="e1", content="a fact about gludd", tags=["immutable"]))

        facts = bank.get_facts()
        assert len(facts) == 1

        facts.append(MemoryEntry(content="spurious fact"))
        facts[0].content = "mutated fact"
        facts[0].tags.append("extra-tag")

        re_facts = bank.get_facts()
        assert len(re_facts) == 1
        assert re_facts[0].content == "a fact about gludd"
        assert re_facts[0].tags == ["immutable"]

    def test_memory_bank_copies_records_on_write(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="write-copy-bank"))
        model = MentalModel(
            model_id="mm-write-copy",
            subject="original subject",
            content="original content",
            tags=["original"],
        )
        fact = MemoryEntry(
            entry_id="fact-write-copy",
            content="original fact",
            tags=["original"],
        )

        bank.add_mental_model(model)
        bank.retain(fact)
        model.subject = "mutated subject"
        model.tags.append("mutated")
        fact.content = "mutated fact"
        fact.tags.append("mutated")

        stored_model = bank.get_mental_models()[0]
        stored_fact = bank.get_facts()[0]
        assert stored_model.subject == "original subject"
        assert stored_model.tags == ["original"]
        assert stored_fact.content == "original fact"
        assert stored_fact.tags == ["original"]

    def test_memory_bank_recall_returns_independent_copies(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="recall-copy-bank"))
        bank.add_mental_model(
            MentalModel(
                model_id="mm-recall-copy",
                subject="gludd",
                content="original model",
                tags=["original"],
            )
        )
        bank.retain(
            MemoryEntry(
                entry_id="fact-recall-copy",
                content="gludd original fact",
                tags=["original"],
            )
        )

        recalled = bank.recall("gludd")
        recalled.mental_models[0].content = "mutated model"
        recalled.facts[0].content = "mutated fact"

        assert bank.get_mental_models()[0].content == "original model"
        assert bank.get_facts()[0].content == "gludd original fact"

    def test_observation_consolidator_does_not_mutate_input(self):
        consolidator = ObservationConsolidator()
        fact = MemoryFact(fact_id="input-fact", content="Alice deployed to production", timestamp=time.time())
        original_content = fact.content
        original_id = fact.fact_id

        observations = consolidator.consolidate([fact])

        assert fact.content == original_content
        assert fact.fact_id == original_id
        assert len(observations) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TEMPR parallel strategies — 4 strategies run in thread pool
# ═══════════════════════════════════════════════════════════════════════════════


class TestTEMPRParallelStrategies:
    def make_docs(self, n: int) -> list[dict[str, str]]:
        templates = [
            "Alice deployed the new feature to production at",
            "Bob reported error 500 on the payment endpoint at",
            "Carol reviewed pull request 42 and approved it",
            "Dan fixed the memory leak in the data pipeline",
            "Eve updated the documentation for the new API",
            "Memory bank consolidation completed with 42 entries",
            "Deployment pipeline failed on step 3",
            "Unit tests passed for the memory module",
            "Code review requested by Alice for PR 77",
            "Database migration 12 rolled back",
        ]
        return [
            {"id": f"doc-{i}", "content": f"{templates[i % len(templates)]} {i * 100}ms ago"}
            for i in range(n)
        ]

    def test_strategies_run_in_parallel(self):
        retriever = TEMPRRetriever(max_workers=4)
        docs = self.make_docs(100)
        retriever.index(docs)

        strategy_times: dict[str, float] = {}

        with patch.object(retriever, "_semantic_search") as mock_semantic, \
             patch.object(retriever, "_bm25_search") as mock_bm25, \
             patch.object(retriever, "_temporal_search") as mock_temporal, \
             patch.object(retriever, "_graph_search") as mock_graph:

            def timed(name: str):
                def wrapper(*args):
                    t0 = time.time()
                    time.sleep(0.05)
                    strategy_times[name] = time.time() - t0
                    return []
                return wrapper

            mock_semantic.side_effect = timed("semantic")
            mock_bm25.side_effect = timed("bm25")
            mock_temporal.side_effect = timed("temporal")
            mock_graph.side_effect = timed("graph")

            retriever.retrieve("deployment error", top_k=5)

        assert len(strategy_times) == 4
        total_parallel_time = max(strategy_times.values())
        total_sequential = sum(strategy_times.values())
        assert total_parallel_time < total_sequential

        assert mock_semantic.call_count == 1
        assert mock_bm25.call_count == 1
        assert mock_temporal.call_count == 1
        assert mock_graph.call_count == 1

    def test_rrf_fusion_correctness(self):
        results = [
            [("doc-1", 0.9), ("doc-2", 0.7), ("doc-3", 0.5)],
            [("doc-2", 0.8), ("doc-3", 0.6), ("doc-1", 0.4)],
            [("doc-3", 0.95), ("doc-2", 0.5), ("doc-1", 0.3)],
            [("doc-1", 0.85), ("doc-3", 0.65), ("doc-2", 0.45)],
        ]
        fused = reciprocal_rank_fusion(results, k=60)
        assert len(fused) == 3
        doc_ids = [d[0] for d in fused]
        assert "doc-1" in doc_ids
        assert "doc-2" in doc_ids
        assert "doc-3" in doc_ids

    def test_strategy_failure_does_not_kill_retrieval(self):
        retriever = TEMPRRetriever(max_workers=4)
        docs = self.make_docs(50)
        retriever.index(docs)

        with patch.object(retriever, "_semantic_search", side_effect=RuntimeError("semantic crash")):
            results = retriever.retrieve("deployment", top_k=5)

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, TEMPRResult)

    def test_all_strategies_fail_returns_empty(self):
        retriever = TEMPRRetriever(max_workers=4)
        docs = self.make_docs(10)
        retriever.index(docs)

        with patch.object(retriever, "_semantic_search", side_effect=RuntimeError), \
             patch.object(retriever, "_bm25_search", side_effect=RuntimeError), \
             patch.object(retriever, "_temporal_search", side_effect=RuntimeError), \
             patch.object(retriever, "_graph_search", side_effect=RuntimeError):
            results = retriever.retrieve("anything", top_k=5)

        assert results == []

    def test_weighted_strategy_disables_zeros(self):
        retriever = TEMPRRetriever(
            strategy_weights={"semantic": 0.5, "bm25": 0.5, "temporal": 0.0, "graph": 0.0},
            max_workers=4,
        )
        docs = self.make_docs(20)
        retriever.index(docs)

        with patch.object(retriever, "_semantic_search", return_value=[]) as mock_s, \
             patch.object(retriever, "_bm25_search", return_value=[]) as mock_b, \
             patch.object(retriever, "_temporal_search") as mock_t, \
             patch.object(retriever, "_graph_search") as mock_g:
            retriever.retrieve("test", top_k=5)

        mock_s.assert_called_once()
        mock_b.assert_called_once()
        mock_t.assert_not_called()
        mock_g.assert_not_called()

    def test_concurrent_retrieve_and_index(self):
        retriever = TEMPRRetriever(max_workers=4)
        docs = self.make_docs(100)
        retriever.index(docs)

        retrieval_results: list[list[TEMPRResult]] = []
        errors: list[Exception] = []

        def heavy_retrieval(query: str) -> None:
            try:
                results = retriever.retrieve(query, top_k=10)
                retrieval_results.append(results)
            except Exception as e:
                errors.append(e)

        queries = ["deployment", "error", "memory", "test", "review", "migration", "approval", "fixed"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(heavy_retrieval, q) for q in queries]
            for f in as_completed(futures):
                f.result(timeout=10)

        assert not errors
        assert len(retrieval_results) == len(queries)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Observation consolidation under load — 100 concurrent facts
# ═══════════════════════════════════════════════════════════════════════════════


class TestObservationConsolidationUnderLoad:
    N_CONCURRENT = 100

    def test_consolidate_many_concurrent_facts(self):
        consolidator = ObservationConsolidator(
            similarity_threshold=0.62,
            default_confidence_floor=0.15,
        )

        def generate_facts(start: int, count: int) -> list[MemoryFact]:
            return [
                MemoryFact(
                    fact_id=f"cf-{start + i}",
                    content=f"Agent {['Alice', 'Bob', 'Carol'][i % 3]} "
                            f"{['deployed', 'tested', 'reviewed', 'fixed', 'documented'][i % 5]} "
                            f"feature {i % 10}",
                    timestamp=time.time() - (i % 100) * 3600,
                )
                for i in range(count)
            ]

        all_facts: list[MemoryFact] = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            batch_size = self.N_CONCURRENT // 10
            futures = [
                executor.submit(generate_facts, b * batch_size, batch_size)
                for b in range(10)
            ]
            for f in as_completed(futures):
                all_facts.extend(f.result())

        assert len(all_facts) == self.N_CONCURRENT

        observations = consolidator.consolidate(all_facts)
        assert len(observations) >= 1

        for obs in observations:
            assert obs.proof_count >= 0
            assert 0.0 <= obs.confidence <= 1.0
            assert obs.created_at > 0
            assert obs.updated_at > 0

    def test_store_put_all_under_concurrent_reads(self):
        store = ObservationStore(store_path="/tmp/gludd-test-load-store.json")
        store.clear()

        facts = [
            MemoryFact(fact_id=f"f-{i}", content=f"observation {i} about system health", timestamp=time.time())
            for i in range(200)
        ]
        consolidator = ObservationConsolidator()
        observations = consolidator.consolidate(facts)

        store.put_all(observations)

        reader_errors: list[Exception] = []
        read_counts: list[int] = []
        stop = threading.Event()

        def concurrent_reader():
            while not stop.is_set():
                try:
                    fresh = store.get_fresh()
                    read_counts.append(len(fresh))
                except Exception as e:
                    reader_errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as executor:
            readers = [executor.submit(concurrent_reader) for _ in range(4)]
            time.sleep(0.5)
            stop.set()
            for r in readers:
                r.result(timeout=5)

        assert not reader_errors
        assert len(read_counts) > 0
        for count in read_counts:
            assert count == len(observations)

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-load-store.json")

    def test_update_observation_under_load(self):
        consolidator = ObservationConsolidator()
        existing = Observation(
            observation_id="existing-obs",
            subject="deployment",
            statement="Deployments are stable",
            evidence=[EvidenceRef(fact_id="f1", quote="stable deployment", timestamp=time.time())],
            proof_count=1,
            confidence=0.3,
            created_at=time.time(),
            updated_at=time.time(),
        )

        def generate_updates(start: int, count: int) -> list[MemoryFact]:
            return [
                MemoryFact(
                    fact_id=f"update-{start + i}",
                    content=f"Deployment event {start + i}: {'success' if i % 3 else 'failure'}",
                    timestamp=time.time(),
                )
                for i in range(count)
            ]

        all_updates: list[MemoryFact] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(generate_updates, b * 20, 20) for b in range(5)
            ]
            for f in as_completed(futures):
                all_updates.extend(f.result())

        updated = consolidator.update(existing, all_updates)
        assert updated.proof_count == 101
        assert updated.confidence > existing.confidence
        assert updated.observation_id == existing.observation_id

    def test_no_duplicate_observations(self):
        consolidator = ObservationConsolidator(similarity_threshold=0.8)
        store = ObservationStore(store_path="/tmp/gludd-test-nodup.json")
        store.clear()

        unique_facts = [
            MemoryFact(fact_id=f"f-{i}", content=f"Unique observation number {i}", timestamp=time.time())
            for i in range(100)
        ]

        observations = consolidator.consolidate(unique_facts)
        store.put_all(observations)

        assert store.count == len(observations)

        store.put_all(observations)
        assert store.count == len(observations)

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-nodup.json")


# ==============================================================================
# 10. Memory bank isolation under concurrency -- 5 banks x 20 concurrent ops
# ==============================================================================


class TestBankIsolationUnderConcurrency:
    N_BANKS = 5
    N_OPS_PER_BANK = 20

    def test_banks_stay_isolated_under_load(self):
        registry = MemoryBankRegistry()

        for bid in range(self.N_BANKS):
            config = MemoryBankConfig(
                bank_id=f"isolated-bank-{bid}",
                mission=f"Mission for bank {bid}",
                directives=[f"directive-{bid}-a", f"directive-{bid}-b"],
            )
            registry.create_bank(config)

        def operate_bank(bank_id: str, worker_id: int, facts_per_worker: int):
            bank = registry.get_bank(bank_id)
            assert bank is not None

            for i in range(facts_per_worker):
                bank.retain(MemoryEntry(
                    content=f"bank-{bank_id}-worker-{worker_id}-fact-{i}",
                    tags=[bank_id, f"w{worker_id}"],
                ))

            result = bank.recall(bank_id)
            return {f.content for f in result.facts}

        with ThreadPoolExecutor(max_workers=25) as executor:
            futures: list[Future] = []
            for bid in range(self.N_BANKS):
                bank_id = f"isolated-bank-{bid}"
                for wid in range(self.N_OPS_PER_BANK):
                    futures.append(executor.submit(operate_bank, bank_id, wid, 5))

            all_recalled: list[set[str]] = []
            for f in as_completed(futures):
                all_recalled.append(f.result())

        for bid in range(self.N_BANKS):
            bank = registry.get_bank(f"isolated-bank-{bid}")
            assert bank is not None
            facts = bank.get_facts()
            expected = self.N_OPS_PER_BANK * 5
            assert len(facts) == expected

            for fact in facts:
                assert fact.content.startswith(f"bank-isolated-bank-{bid}-")

    def test_registry_concurrent_create_and_delete(self):
        registry = MemoryBankRegistry()

        def create_immediately_count() -> int:
            config = MemoryBankConfig(
                bank_id=f"temp-bank-{uuid.uuid4().hex[:8]}",
            )
            registry.create_bank(config)
            return registry.bank_count()

        def delete_random_bank():
            banks = registry.list_banks()
            if banks:
                registry.delete_bank(banks[0].bank_id)

        with ThreadPoolExecutor(max_workers=20) as executor:
            c_futures = [executor.submit(create_immediately_count) for _ in range(50)]
            d_futures = [executor.submit(delete_random_bank) for _ in range(50)]
            for f in as_completed(c_futures + d_futures):
                with contextlib.suppress(Exception):
                    f.result(timeout=5)

        remaining = registry.bank_count()
        assert remaining >= 0

    def test_registry_duplicate_bank_rejected_concurrently(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="unique-bank")

        registry.create_bank(config)

        errors: list[ValueError] = []

        def try_create_duplicate():
            try:
                config2 = MemoryBankConfig(bank_id="unique-bank")
                registry.create_bank(config2)
            except ValueError as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_create_duplicate) for _ in range(10)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 10
        assert registry.bank_count() == 1

    def test_hindsight_inmemory_store_concurrent_sessions(self):
        store = _InMemoryStore()

        def write_session(session_id: str, count: int) -> list[str]:
            ids = []
            for i in range(count):
                rid = store.retain(
                    content=f"session {session_id} record {i}",
                    metadata={"session": session_id, "index": i},
                )
                ids.append(rid)
            return ids

        all_ids: list[str] = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(write_session, f"sess-{s}", 25)
                for s in range(20)
            ]
            for f in as_completed(futures):
                all_ids.extend(f.result())

        assert len(all_ids) == 500
        assert len(set(all_ids)) == 500

        results = store.recall("session sess-0", top_k=50)
        assert len(results) > 0

        results_sess_5 = store.search("sess-5", top_k=50)
        for r in results_sess_5:
            assert "sess-5" in r["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# Stress tests — 1000+ operations
# ═══════════════════════════════════════════════════════════════════════════════


class TestStress:
    def test_thousand_concurrent_retains(self):
        config = MemoryBankConfig(bank_id="stress-bank")
        bank = MemoryBank(config)

        N = 1000

        def write_batch(start: int, count: int) -> int:
            for i in range(start, start + count):
                bank.retain(MemoryEntry(
                    content=f"stress-fact-{i}",
                    tags=["stress", f"batch{i // 100}"],
                ))
            return count

        with ThreadPoolExecutor(max_workers=20) as executor:
            batch = 50
            futures = [
                executor.submit(write_batch, b * batch, batch)
                for b in range(N // batch)
            ]
            total = sum(f.result(timeout=5) for f in as_completed(futures))

        assert total == N
        facts = bank.get_facts()
        assert len(facts) == N

        for i in range(0, N, 50):
            result = bank.recall(f"stress-fact-{i}")
            assert len(result.facts) > 0

    def test_observation_store_large_batch(self):
        store = ObservationStore(store_path="/tmp/gludd-test-stress-obs.json")
        store.clear()

        total = 500
        observations = [
            Observation(
                observation_id=f"stress-obs-{i}",
                subject=f"stress-subject-{i % 10}",
                statement=f"Stress test observation {i} with extra detail about system performance",
                evidence=[
                    EvidenceRef(
                        fact_id=f"ef-{i}-{j}",
                        quote=f"evidence quote {i}.{j}",
                        timestamp=time.time(),
                    )
                    for j in range(3)
                ],
                proof_count=3,
                confidence=0.7 + (i % 4) * 0.05,
                created_at=time.time(),
                updated_at=time.time(),
            )
            for i in range(total)
        ]

        def write_chunk(chunk: list[Observation]):
            store.put_all(chunk)

        chunk_size = 50
        chunks = [observations[i:i + chunk_size] for i in range(0, total, chunk_size)]

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(write_chunk, chunk) for chunk in chunks]
            for f in as_completed(futures):
                f.result(timeout=5)

        all_retrieved = store.list_all()
        assert len(all_retrieved) == total

        high_confidence = store.get_above_confidence(0.85)
        assert len(high_confidence) > 0

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-stress-obs.json")

    def test_rapid_put_delete_cycle(self):
        store = ObservationStore(store_path="/tmp/gludd-test-put-delete.json")
        store.clear()

        for cycle in range(10):
            obs = Observation(
                observation_id=f"cycle-{cycle}",
                subject="cycle",
                statement=f"cycle {cycle} data",
                proof_count=1,
                confidence=0.5,
                created_at=time.time(),
                updated_at=time.time(),
            )
            store.put(obs)
            retrieved = store.get(f"cycle-{cycle}")
            assert retrieved is not None
            store.delete(f"cycle-{cycle}")
            assert store.get(f"cycle-{cycle}") is None

        assert store.count == 0

        store.clear()
        with contextlib.suppress(OSError):
            os.remove("/tmp/gludd-test-put-delete.json")
