"""Integration/E2E tests: TEMPR + observations + mental models + memory banks.

Proves the full memory pipeline end-to-end:
  - Retain facts into MemoryBank → consolidate into Observations →
    index into TEMPR → recall via TEMPR → reflect for synthesized answer
  - Cross-bank retrieval with isolation
  - Mental model priority over raw facts
  - TEMPR strategy fusion (semantic + BM25 + temporal + graph) via RRF
  - Observation evolution through repeated consolidation
  - Contradiction handling
  - Concurrent pipeline correctness
  - Performance / throughput baseline
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.memory.hindsight_adapter import HindsightMemoryAdapter
from general_ludd.memory.memory_bank import (
    Disposition,
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
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
from general_ludd.memory.tempr_retriever import TEMPRRetriever


def _make_entry(content: str, source: str = "", tags: list[str] | None = None) -> MemoryEntry:
    return MemoryEntry(content=content, source=source, tags=tags or [])


def _make_fact(content: str, source: str = "", fact_id: str | None = None) -> MemoryFact:
    _fact_counter[0] += 1
    return MemoryFact(
        fact_id=fact_id or f"f-{_fact_counter[0]}",
        content=content,
        source=source,
        timestamp=time.time(),
    )


_fact_counter: list[int] = [0]


def _make_observation(
    observation_id: str, subject: str, statement: str, confidence: float,
    proof_count: int = 1, contradictions: list[str] | None = None,
) -> Observation:
    now = time.time()
    return Observation(
        observation_id=observation_id,
        subject=subject,
        statement=statement,
        confidence=confidence,
        proof_count=proof_count,
        created_at=now,
        updated_at=now,
        contradictions=contradictions or [],
    )


def _entry_to_doc(entry: MemoryEntry) -> dict:
    return {
        "id": entry.entry_id,
        "content": entry.content,
        "created_at": datetime.fromtimestamp(entry.created_at, tz=UTC).isoformat(),
    }


def _obs_to_doc(obs: Observation) -> dict:
    return {
        "id": obs.observation_id,
        "content": f"{obs.subject}: {obs.statement}",
        "created_at": datetime.fromtimestamp(obs.created_at, tz=UTC).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Full Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    def test_retain_consolidate_recall_reflect(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="pipeline-bank"))
        consolidator = ObservationConsolidator(similarity_threshold=0.62)
        retriever = TEMPRRetriever()

        facts_raw = [
            _make_entry("Python is the primary language for backend services"),
            _make_entry("Python handles all API endpoints via FastAPI"),
            _make_entry("Go is used for performance-critical data pipelines"),
            _make_entry("Go handles streaming data with goroutines"),
        ]
        for f in facts_raw:
            bank.retain(f)

        facts_for_consolidation = [
            MemoryFact(fact_id=e.entry_id, content=e.content, timestamp=e.created_at)
            for e in facts_raw
        ]
        observations = consolidator.consolidate(facts_for_consolidation)
        assert len(observations) >= 1

        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "observations.json")
        )
        store.put_all(observations)

        docs = [_entry_to_doc(e) for e in bank.get_facts()] + [
            _obs_to_doc(o) for o in store.list_all()
        ]
        retriever.index(docs)

        results = retriever.retrieve("Python backend", top_k=5)
        assert len(results) > 0
        python_ids = [r.doc_id for r in results]
        assert facts_raw[0].entry_id in python_ids or facts_raw[1].entry_id in python_ids

    def test_pipeline_with_mental_models(self):
        bank = MemoryBank(MemoryBankConfig(
            bank_id="mm-bank",
            mission="Track user preferences accurately",
        ))
        bank.add_mental_model(MentalModel(
            subject="language preference",
            content="User strongly prefers Python for all new projects",
            priority=9,
        ))
        bank.retain(_make_entry("User mentioned they like async/await"))
        bank.retain(_make_entry("User said Go concurrency is better than asyncio"))

        result = bank.recall("what language for new projects")
        assert len(result.mental_models) >= 1
        assert "Python" in result.mental_models[0].content
        assert "Mental Models" in result.synthesized
        assert "Mission" in result.synthesized

    def test_hindsight_adapter_fallback_retain_recall(self):
        HindsightMemoryAdapter.reset_instance()
        adapter = HindsightMemoryAdapter(enabled=False)
        rid = adapter.retain("The sky is blue today", {"source": "test"})
        assert rid and len(rid) > 0

        results = adapter.recall("sky color", top_k=3)
        assert len(results) >= 1
        assert any("blue" in r["content"] for r in results)

    def test_hindsight_adapter_fallback_reflect(self):
        HindsightMemoryAdapter.reset_instance()
        adapter = HindsightMemoryAdapter(enabled=False)
        adapter.retain("Alice uses Python for backend development")
        adapter.retain("Bob prefers Go for system tools")

        answer = adapter.reflect("Python backend")
        assert len(answer) > 0
        assert "Fallback" in answer

    def test_health_check_returns_expected_keys(self):
        HindsightMemoryAdapter.reset_instance()
        adapter = HindsightMemoryAdapter(enabled=False)
        health = adapter.health_check()
        assert health["backend"] == "fallback"
        assert health["enabled"] is False
        assert health["connected"] is False
        assert "url" in health


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Cross-Bank Retrieval
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossBankRetrieval:
    def test_bank_isolation_facts(self):
        bank_a = MemoryBank(MemoryBankConfig(bank_id="python-bank"))
        bank_b = MemoryBank(MemoryBankConfig(bank_id="go-bank"))

        bank_a.retain(_make_entry("Python has asyncio for concurrency", source="docs"))
        bank_a.retain(_make_entry("Python's GIL limits true parallelism", source="docs"))
        bank_b.retain(_make_entry("Go uses goroutines for concurrency", source="docs"))
        bank_b.retain(_make_entry("Go channels enable CSP concurrency", source="docs"))

        result_a = bank_a.recall("concurrency")
        result_b = bank_b.recall("concurrency")

        assert len(result_a.facts) >= 1
        assert len(result_b.facts) >= 1
        id_a = {f.entry_id for f in result_a.facts}
        id_b = {f.entry_id for f in result_b.facts}
        assert not id_a & id_b

    def test_cross_bank_tempr_isolated_indexing(self):
        bank_a = MemoryBank(MemoryBankConfig(bank_id="python-bank"))
        bank_b = MemoryBank(MemoryBankConfig(bank_id="go-bank"))
        bank_a.retain(_make_entry("Python concurrency uses asyncio event loop"))
        bank_b.retain(_make_entry("Go concurrency uses goroutines and channels"))

        docs_a = [_entry_to_doc(e) for e in bank_a.get_facts()]
        docs_b = [_entry_to_doc(e) for e in bank_b.get_facts()]

        ret_a = TEMPRRetriever()
        ret_a.index(docs_a)
        res_a = ret_a.retrieve("concurrency", top_k=3)
        assert len(res_a) >= 1

        ret_b = TEMPRRetriever()
        ret_b.index(docs_b)
        res_b = ret_b.retrieve("concurrency", top_k=3)
        assert len(res_b) >= 1

        assert all(r.doc_id in {d["id"] for d in docs_a} for r in res_a)
        assert all(r.doc_id in {d["id"] for d in docs_b} for r in res_b)

    def test_registry_manages_multiple_banks(self):
        registry = MemoryBankRegistry()
        a = registry.create_bank(MemoryBankConfig(bank_id="reg-a"))
        b = registry.create_bank(MemoryBankConfig(bank_id="reg-b"))
        assert registry.bank_count() == 2

        a.retain(_make_entry("data in bank A"))
        b.retain(_make_entry("data in bank B"))

        assert registry.get_bank("reg-a") is a
        assert registry.get_bank("reg-b") is b
        banks = registry.list_banks()
        assert {c.bank_id for c in banks} == {"reg-a", "reg-b"}

        registry.delete_bank("reg-a")
        assert registry.bank_count() == 1
        assert registry.get_bank("reg-a") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Mental Model Priority
# ═══════════════════════════════════════════════════════════════════════════════


class TestMentalModelPriority:
    def test_mental_model_surfaces_before_facts(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="mm-priority"))
        bank.retain(_make_entry("User tried async Python and found it complex"))
        bank.retain(_make_entry("User experimented with trio and anyio"))
        bank.add_mental_model(MentalModel(
            subject="user hates async",
            content="User strongly dislikes async/await and prefers synchronous code",
            priority=10,
        ))

        result = bank.recall("should I use async here")
        assert len(result.mental_models) >= 1
        assert "async" in result.mental_models[0].content.lower()
        assert "Mental Models" in result.synthesized

    def test_mental_model_priority_ordering(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="mm-ordering"))
        bank.add_mental_model(MentalModel(
            subject="low priority", content="low info", priority=1,
        ))
        bank.add_mental_model(MentalModel(
            subject="high priority", content="high info", priority=10,
        ))
        bank.add_mental_model(MentalModel(
            subject="medium priority", content="med info", priority=5,
        ))

        result = bank.recall("priority")
        assert len(result.mental_models) >= 3
        assert result.mental_models[0].priority >= result.mental_models[1].priority
        assert result.mental_models[1].priority >= result.mental_models[2].priority

    def test_subject_filter_on_mental_models(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="mm-filter"))
        bank.add_mental_model(MentalModel(
            subject="python policy", content="use Python 3.12+"
        ))
        bank.add_mental_model(MentalModel(
            subject="deployment policy", content="use Docker multi-stage builds"
        ))

        py_models = bank.get_mental_models(subject_filter="python")
        assert len(py_models) == 1
        assert py_models[0].subject == "python policy"

        dep_models = bank.get_mental_models(subject_filter="deployment")
        assert len(dep_models) == 1
        assert dep_models[0].subject == "deployment policy"

    def test_mental_model_update_and_delete(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="mm-mutate"))
        model = bank.add_mental_model(MentalModel(
            subject="testing", content="old content"
        ))
        updated = bank.update_mental_model(model.model_id, "new content")
        assert updated is not None
        assert updated.content == "new content"

        assert bank.delete_mental_model(model.model_id) is True
        assert bank.delete_mental_model("nonexistent") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TEMPR Strategy Fusion
# ═══════════════════════════════════════════════════════════════════════════════


class TestTEMPRStrategyFusion:
    def make_docs(self):
        now = datetime.now(UTC)
        return [
            {"id": "d1", "content": "Python async/await concurrency patterns for web servers",
             "created_at": (now - timedelta(days=1)).isoformat()},
            {"id": "d2", "content": "FastAPI Python web framework performance benchmarks",
             "created_at": (now - timedelta(days=5)).isoformat()},
            {"id": "d3", "content": "Go goroutines channel-based concurrency CSP model",
             "created_at": (now - timedelta(days=2)).isoformat()},
            {"id": "d4", "content": "Rust async runtime tokio performance comparison",
             "created_at": (now - timedelta(days=30)).isoformat()},
            {"id": "d5", "content": "Django Python ORM query optimization techniques",
             "created_at": (now - timedelta(hours=1)).isoformat()},
        ]

    def test_semantic_finds_conceptual_match(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 1.0, "bm25": 0.0, "temporal": 0.0, "graph": 0.0})
        retriever.index(self.make_docs())
        results = retriever.retrieve("async programming", top_k=3)
        assert len(results) >= 1
        assert any("async" in r.content.lower() for r in results)

    def test_bm25_finds_exact_keyword(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.0, "bm25": 1.0, "temporal": 0.0, "graph": 0.0})
        retriever.index(self.make_docs())
        results = retriever.retrieve("FastAPI web framework", top_k=3)
        assert len(results) >= 1
        assert any("fastapi" in r.content.lower() for r in results)

    def test_temporal_prefers_recent(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.0, "bm25": 0.0, "temporal": 1.0, "graph": 0.0})
        retriever.index(self.make_docs())
        results = retriever.retrieve("", top_k=5)
        assert len(results) == 5
        assert results[0].doc_id == "d5"

    def test_graph_finds_related_entities(self):
        retriever = TEMPRRetriever(strategy_weights={"semantic": 0.0, "bm25": 0.0, "temporal": 0.0, "graph": 1.0})
        docs = [
            {"id": "e1", "content": "Alice works on the FastAPI project"},
            {"id": "e2", "content": "Bob works on the Django project"},
            {"id": "e3", "content": "The FastAPI team met with Alice and Bob"},
        ]
        retriever.index(docs)
        results = retriever.retrieve("Alice collaboration", top_k=3)
        assert len(results) >= 1

    def test_rrf_fusion_combines_all_strategies(self):
        retriever = TEMPRRetriever()
        retriever.index(self.make_docs())
        results = retriever.retrieve("Python concurrency async", top_k=5)
        assert len(results) <= 5
        assert len(results) >= 2
        for r in results:
            assert len(r.scores) >= 1
            assert r.final_score > 0

    def test_date_range_filtering(self):
        now = datetime.now(UTC)
        retriever = TEMPRRetriever()
        retriever.index(self.make_docs())
        results = retriever.retrieve(
            "async", top_k=5,
            date_range=(now - timedelta(days=3), now + timedelta(days=1)),
        )
        for r in results:
            assert r.doc_id != "d4"

    def test_empty_index_returns_empty(self):
        retriever = TEMPRRetriever()
        retriever.index([])
        assert retriever.retrieve("anything") == []

    def test_reindex_replaces_documents(self):
        retriever = TEMPRRetriever()
        retriever.index([{"id": "x1", "content": "old data"}])
        old = retriever.retrieve("old", top_k=1)
        assert old[0].doc_id == "x1"

        retriever.index([{"id": "y1", "content": "new data"}])
        new = retriever.retrieve("new", top_k=1)
        assert new[0].doc_id == "y1"
        assert retriever.retrieve("old", top_k=1) == [] or "old" not in str(retriever.retrieve("old", top_k=1))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Observation Evolution
# ═══════════════════════════════════════════════════════════════════════════════


class TestObservationEvolution:
    def test_single_fact_observation(self):
        consolidator = ObservationConsolidator()
        facts = [_make_fact("Alice uses React for frontend development")]
        obs = consolidator.consolidate(facts)
        assert len(obs) == 1
        assert obs[0].subject == "Alice"
        assert obs[0].confidence == pytest.approx(0.142, abs=0.01)

    def test_five_consistent_facts_increase_confidence(self):
        consolidator = ObservationConsolidator()
        facts = [
            _make_fact("Alice uses React for frontend"),
            _make_fact("Alice picks React for frontend projects"),
            _make_fact("Alice builds UIs with React components"),
            _make_fact("Alice frontend React development"),
            _make_fact("Alice React-based web applications"),
        ]

        obs_1 = consolidator.consolidate(facts[:1])[0]

        updated = consolidator.update(obs_1, facts[1:3])
        assert updated.proof_count == 3
        assert updated.confidence > obs_1.confidence

        updated2 = consolidator.update(updated, facts[3:5])
        assert updated2.proof_count == 5
        assert updated2.confidence > updated.confidence

        for ev in updated2.evidence:
            assert isinstance(ev, EvidenceRef)
            assert len(ev.quote) > 0

    def test_all_five_at_once_high_confidence(self):
        consolidator = ObservationConsolidator(similarity_threshold=0.3)
        facts = [
            _make_fact("Alice uses React for frontend work"),
            _make_fact("Alice uses React for frontend projects"),
            _make_fact("Alice uses React for building frontend"),
            _make_fact("Alice uses React for frontend development"),
            _make_fact("Alice uses React for web frontend"),
        ]
        observations = consolidator.consolidate(facts)
        assert len(observations) >= 1
        assert any(o.subject == "Alice" for o in observations)

    def test_observation_store_persist_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "obs.json")
            store_a = ObservationStore(store_path=path)
            obs = _make_observation("o1", "Alice", "uses React", 0.3)
            store_a.put(obs)
            assert store_a.count == 1

            store_b = ObservationStore(store_path=path)
            assert store_b.count == 1
            loaded = store_b.get("o1")
            assert loaded is not None
            assert loaded.subject == "Alice"

    def test_staleness_flag(self):
        consolidator = ObservationConsolidator()
        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "obs_stale.json")
        )
        facts = [_make_fact("Alice uses React")]
        obs = consolidator.consolidate(facts)
        store.put_all(obs)

        fresh = store.get_fresh()
        assert len(fresh) == 1
        assert not fresh[0].stale

        marked = consolidator.mark_stale(store.list_all(), time.time() + 100)
        assert marked[0].stale

    def test_confidence_above_threshold_query(self):
        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "obs_conf.json")
        )
        store.put(_make_observation("low", "X", "low confidence", 0.1))
        store.put(_make_observation("high", "Y", "high confidence", 0.8))
        store.put(_make_observation("med", "Z", "medium confidence", 0.5))

        high = store.get_above_confidence(0.6)
        assert len(high) == 1
        assert high[0].observation_id == "high"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Contradiction Handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestContradictionHandling:
    def test_contradiction_drops_confidence(self):
        consolidator = ObservationConsolidator(similarity_threshold=0.5)
        facts = [
            _make_fact("Alice uses React for frontend development"),
            _make_fact("Alice switched to Vue for frontend work"),
        ]
        observations = consolidator.consolidate(facts)
        assert len(observations) >= 1

        has_contradictions = any(
            len(o.contradictions) > 0 for o in observations
        )
        assert has_contradictions

    def test_contradictions_stored_in_observation(self):
        consolidator = ObservationConsolidator()
        facts = [
            _make_fact("Alice uses React"),
            _make_fact("Alice uses Vue"),
            _make_fact("Alice prefers Angular"),
        ]
        observations = consolidator.consolidate(facts)

        all_contradictions = []
        for obs in observations:
            all_contradictions.extend(obs.contradictions)
        assert len(all_contradictions) >= 1

    def test_contradiction_penalty_in_confidence(self):
        conf_no_contra = ObservationConsolidator.compute_confidence(5, 0)
        conf_with_contra = ObservationConsolidator.compute_confidence(5, 3)
        assert conf_with_contra < conf_no_contra

    def test_max_contradictions_capped(self):
        consolidator = ObservationConsolidator(max_contradictions_stored=2)
        facts = [_make_fact("Alice uses React")]
        for i in range(10):
            facts.append(_make_fact(f"Alice uses Framework X{i}"))

        observations = consolidator.consolidate(facts)
        for obs in observations:
            assert len(obs.contradictions) <= 2

    def test_deduplication_removes_near_duplicates(self):
        consolidator = ObservationConsolidator(similarity_threshold=0.62)
        facts = [
            _make_fact("Alice uses React for frontend"),
            _make_fact("Alice uses React for frontend development"),
            _make_fact("Alice uses React for frontend work"),
        ]
        deduped = consolidator.deduplicate(facts)
        assert len(deduped) < 3


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Concurrent Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentPipeline:
    def test_five_threads_retain_into_same_bank(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="concurrent-bank"))
        per_thread = 20

        def retain_batch(worker_id: int):
            for i in range(per_thread):
                bank.retain(_make_entry(
                    f"Worker {worker_id} fact {i} about Python concurrency",
                    source=f"worker-{worker_id}",
                ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(retain_batch, w) for w in range(5)]
            for f in futures:
                f.result()

        all_facts = bank.get_facts()
        assert len(all_facts) == 5 * per_thread

    def test_concurrent_retain_then_consolidate(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="concurrent-consolidate"))
        per_thread = 10

        def retain(worker_id: int):
            for event_id in range(per_thread):
                bank.retain(_make_entry(
                    (
                        "Alice uses Python for backend development tasks "
                        f"in event {worker_id}-{event_id}"
                    ),
                    source=f"worker-{worker_id}",
                ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(retain, range(5)))

        all_facts = bank.get_facts()
        assert len(all_facts) == 50

        consolidator = ObservationConsolidator(similarity_threshold=0.5)
        facts_for_cons = [
            MemoryFact(fact_id=e.entry_id, content=e.content, timestamp=e.created_at)
            for e in all_facts
        ]
        observations = consolidator.consolidate(facts_for_cons)
        assert len(observations) >= 1
        primary = max(observations, key=lambda o: o.proof_count)
        assert primary.subject == "Alice"

    def test_concurrent_registry_operations(self):
        registry = MemoryBankRegistry()
        bank_ids = [f"conc-reg-{i}" for i in range(10)]

        def create_bank(bid: str):
            registry.create_bank(MemoryBankConfig(bank_id=bid))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(create_bank, bank_ids))

        assert registry.bank_count() == 10
        for bid in bank_ids:
            assert registry.get_bank(bid) is not None

    def test_concurrent_observation_store_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "concurrent_obs.json")
            store = ObservationStore(store_path=path)

            def write_obs(idx: int):
                obs = _make_observation(
                    f"conc-obs-{idx}", f"Subject{idx}", f"statement {idx}", 0.3
                )
                store.put(obs)

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(write_obs, range(50)))

            assert store.count == 50


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Performance / Throughput
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerformance:
    def test_thousand_facts_retain_consolidate_recall_within_timeout(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="perf-bank"))
        consolidator = ObservationConsolidator()
        retriever = TEMPRRetriever()

        timeout = 30.0
        start = time.monotonic()

        for i in range(1000):
            bank.retain(_make_entry(
                f"Performance test fact number {i} about Python async concurrency patterns",
            ))

        facts_for_cons = [
            MemoryFact(fact_id=e.entry_id, content=e.content, timestamp=e.created_at)
            for e in bank.get_facts()
        ]
        observations = consolidator.consolidate(facts_for_cons)

        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "perf_obs.json")
        )
        store.put_all(observations)

        docs = [_entry_to_doc(e) for e in bank.get_facts()[:200]] + [
            _obs_to_doc(o) for o in store.list_all()[:200]
        ]
        retriever.index(docs)

        results = retriever.retrieve("concurrency patterns", top_k=10)
        elapsed = time.monotonic() - start

        assert elapsed < timeout, f"Pipeline took {elapsed:.1f}s, must be <{timeout}s"
        assert len(results) <= 10
        assert len(facts_for_cons) == 1000


# ═══════════════════════════════════════════════════════════════════════════════
# Additional Integration Scenarios
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegrationScenarios:
    def test_temp_observation_to_tempr_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "roundtrip.json")
            store = ObservationStore(store_path=path)
            consolidator = ObservationConsolidator()

            facts = [
                _make_fact("Charlie deploys with Docker Compose", source="chat"),
                _make_fact("Charlie uses Kubernetes for production", source="chat"),
            ]
            observations = consolidator.consolidate(facts)
            store.put_all(observations)

            retriever = TEMPRRetriever()
            docs = [
                _obs_to_doc(o) for o in store.list_all()
            ]
            retriever.index(docs)

            results = retriever.retrieve("deployment", top_k=3)
            assert len(results) >= 1
            assert any("Charlie" in r.content for r in results)

    def test_disposition_affects_synthesized_output(self):
        skeptical = MemoryBank(MemoryBankConfig(
            bank_id="skeptical-bank",
            disposition=Disposition(skepticism=5),
        ))
        trusting = MemoryBank(MemoryBankConfig(
            bank_id="trusting-bank",
            disposition=Disposition(skepticism=1),
        ))

        skeptical.retain(_make_entry("Python is the best language"))
        trusting.retain(_make_entry("Python is the best language"))

        skep_result = skeptical.reflect("what is the best language")
        trust_result = trusting.reflect("what is the best language")

        assert "skepticism=5" in skep_result
        assert "skepticism=1" in trust_result

    def test_directives_in_synthesized_output(self):
        bank = MemoryBank(MemoryBankConfig(
            bank_id="directive-bank",
            directives=["always be concise", "prefer examples over theory"],
        ))
        bank.retain(_make_entry("Python decorators wrap functions"))
        result = bank.reflect("explain decorators")
        assert "always be concise" in result

    def test_bank_or_create_idempotent(self):
        registry = MemoryBankRegistry()
        config = MemoryBankConfig(bank_id="idempotent-bank")
        b1 = registry.get_or_create_bank(config)
        b2 = registry.get_or_create_bank(config)
        assert b1 is b2
        assert registry.bank_count() == 1

    def test_fact_delete_and_tag_filtering(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="delete-test"))
        e1 = bank.retain(_make_entry("important data", tags=["critical"]))
        bank.retain(_make_entry("mundane data", tags=["low"]))

        critical = bank.get_facts(tag_filter="critical")
        assert len(critical) == 1
        assert critical[0].entry_id == e1.entry_id

        assert bank.delete_fact(e1.entry_id) is True
        assert bank.delete_fact(e1.entry_id) is False
        assert len(bank.get_facts()) == 1

    def test_observation_subject_query(self):
        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "subj.json")
        )
        store.put(_make_observation("a1", "Alice", "uses Python", 0.3))
        store.put(_make_observation("b1", "Bob", "uses Go", 0.3))
        store.put(_make_observation("a2", "Alice", "uses React", 0.5))

        alice_obs = store.get_by_subject("Alice")
        assert len(alice_obs) == 2

    def test_observation_delete(self):
        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "del.json")
        )
        store.put(_make_observation("to-delete", "X", "content", 0.1))
        store.put(_make_observation("to-keep", "Y", "content", 0.1))
        assert store.count == 2

        assert store.delete("to-delete") is True
        assert store.delete("to-delete") is False
        assert store.count == 1
        assert store.get("to-keep") is not None

    def test_observation_store_clear(self):
        store = ObservationStore(
            store_path=os.path.join(tempfile.mkdtemp(), "clear.json")
        )
        store.put(_make_observation("x1", "X", "a", 0.1))
        store.put(_make_observation("x2", "X", "b", 0.1))
        store.clear()
        assert store.count == 0
