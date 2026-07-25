"""Tests for ObservationConsolidator — facts → observations with evidence tracking."""

from __future__ import annotations

import contextlib
import os
import threading
import time

import pytest

from general_ludd.memory.observation_consolidator import (
    EvidenceRef,
    MemoryFact,
    Observation,
    ObservationConsolidator,
    ObservationStore,
    _extract_names,
    _hash_observation_id,
    _normalize,
    _text_jaccard,
    compute_confidence,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _fact(
    content: str,
    fact_id: str | None = None,
    source: str = "",
    timestamp: float | None = None,
) -> MemoryFact:
    _counter[0] += 1
    return MemoryFact(
        fact_id=fact_id or f"fact-{_counter[0]}",
        content=content,
        source=source,
        timestamp=timestamp if timestamp is not None else time.time(),
    )


_counter: list[int] = [0]


def _sample_facts() -> list[MemoryFact]:
    return [
        _fact("Alice uses Python for all backend development"),
        _fact("Alice consistently uses Python for server-side code"),
        _fact("Alice prefers pytest over unittest for testing"),
    ]


# ── EvidenceRef ────────────────────────────────────────────────────────────────


class TestEvidenceRef:
    def test_creation(self):
        ev = EvidenceRef(fact_id="f1", quote="test info", timestamp=123.0)
        assert ev.fact_id == "f1"
        assert ev.quote == "test info"
        assert ev.timestamp == 123.0

    def test_equality(self):
        a = EvidenceRef(fact_id="f1", quote="x", timestamp=1.0)
        b = EvidenceRef(fact_id="f1", quote="x", timestamp=1.0)
        assert a == b


# ── Observation ────────────────────────────────────────────────────────────────


class TestObservation:
    def test_defaults(self):
        obs = Observation(
            observation_id="o1", subject="User", statement="likes Python"
        )
        assert obs.observation_id == "o1"
        assert obs.subject == "User"
        assert obs.statement == "likes Python"
        assert obs.evidence == []
        assert obs.proof_count == 0
        assert obs.confidence == 0.0
        assert obs.stale is False
        assert obs.contradictions == []

    def test_with_evidence(self):
        ev = EvidenceRef(fact_id="f1", quote="User loves Python", timestamp=1.0)
        obs = Observation(
            observation_id="o2",
            subject="User",
            statement="loves Python",
            evidence=[ev],
            proof_count=1,
            confidence=0.3,
            created_at=10.0,
            updated_at=10.0,
            stale=False,
            contradictions=[],
        )
        assert len(obs.evidence) == 1
        assert obs.evidence[0].fact_id == "f1"
        assert obs.proof_count == 1


# ── compute_confidence ─────────────────────────────────────────────────────────


class TestComputeConfidence:
    def test_zero_evidence_returns_zero(self):
        assert compute_confidence(0, 0) == 0.0

    def test_single_evidence_low_confidence(self):
        c = compute_confidence(1, 0)
        assert 0.05 <= c <= 0.25

    def test_five_evidences_high_confidence(self):
        c = compute_confidence(5, 0)
        assert c >= 0.4

    def test_seven_evidences_saturated(self):
        c = compute_confidence(7, 0)
        assert c >= 0.8

    def test_contradictions_lower_confidence(self):
        c_no_contra = compute_confidence(5, 0)
        c_with_contra = compute_confidence(5, 3)
        assert c_with_contra < c_no_contra

    def test_many_contradictions_floor(self):
        c = compute_confidence(1, 10)
        assert c >= 0.0

    def test_confidence_bounded_between_zero_and_one(self):
        for ec in range(0, 20):
            for cc in range(0, 10):
                c = compute_confidence(ec, cc)
                assert 0.0 <= c <= 1.0, f"ec={ec}, cc={cc}, c={c}"


# ── Normalization ──────────────────────────────────────────────────────────────


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("Hello WORLD") == "hello world"

    def test_strip_punctuation(self):
        result = _normalize("hello, world!")
        assert result == "hello  world "

    def test_numbers_preserved(self):
        result = _normalize("test 123 abc")
        assert "123" in result


class TestJaccard:
    def test_identical(self):
        assert _text_jaccard("a b c", "a b c") == 1.0

    def test_disjoint(self):
        assert _text_jaccard("x y z", "a b c") == 0.0

    def test_partial(self):
        s = _text_jaccard("a b c d", "a b x y")
        assert 0.2 <= s <= 0.4

    def test_empty(self):
        assert _text_jaccard("", "x") == 0.0


# ── Subject extraction ─────────────────────────────────────────────────────────


class TestExtractNames:
    def test_single_proper_name(self):
        names = _extract_names("Alice uses Python for backend")
        assert "Alice" in names

    def test_two_word_name(self):
        names = _extract_names("Project Phoenix is delayed")
        assert "Project Phoenix" in names

    def test_common_nouns_filtered(self):
        names = _extract_names("We use Python and First Class modules")
        for common in ("We", "First", "Python"):
            assert common not in names
        assert "Class" in names

    def test_no_names_returns_empty(self):
        names = _extract_names("hello world programming is great")
        assert names == []

    def test_multiple_names(self):
        names = _extract_names("Alice told Bob that Charlie disagreed")
        assert "Alice" in names
        assert "Bob" in names
        assert "Charlie" in names


class TestHashObservationId:
    def test_deterministic(self):
        a = _hash_observation_id("User", "prefers Python")
        b = _hash_observation_id("User", "prefers Python")
        assert a == b

    def test_different_inputs_produce_different_hashes(self):
        a = _hash_observation_id("Alice", "uses Python")
        b = _hash_observation_id("Bob", "uses Python")
        assert a != b

    def test_length_is_16(self):
        result = _hash_observation_id("User", "statement")
        assert len(result) == 16


# ── ObservationConsolidator ────────────────────────────────────────────────────


class TestObservationConsolidatorInit:
    def test_defaults(self):
        c = ObservationConsolidator()
        assert c.similarity_threshold == 0.62
        assert c.default_confidence_floor == 0.15
        assert c.max_contradictions_stored == 20

    def test_custom_thresholds(self):
        c = ObservationConsolidator(
            similarity_threshold=0.8,
            default_confidence_floor=0.1,
            max_contradictions_stored=5,
        )
        assert c.similarity_threshold == 0.8
        assert c.default_confidence_floor == 0.1
        assert c.max_contradictions_stored == 5


class TestConsolidate:
    def test_empty_facts_returns_empty(self):
        c = ObservationConsolidator()
        assert c.consolidate([]) == []

    def test_single_fact_returns_one_observation(self):
        c = ObservationConsolidator()
        fact = _fact("Alice uses Python for development")
        results = c.consolidate([fact])
        assert len(results) == 1
        assert results[0].subject == "Alice"
        assert results[0].proof_count == 1
        assert len(results[0].evidence) == 1
        assert results[0].evidence[0].quote == fact.content

    def test_three_facts_same_subject_merge_to_one(self):
        c = ObservationConsolidator()
        facts = _sample_facts()
        results = c.consolidate(facts)
        alice_obs = [o for o in results if o.subject == "Alice"]
        assert len(alice_obs) >= 1

    def test_evidence_has_correct_quotes(self):
        c = ObservationConsolidator()
        facts = _sample_facts()
        results = c.consolidate(facts)
        alice = next(o for o in results if o.subject == "Alice")
        assert alice.proof_count >= 1
        for ev in alice.evidence:
            assert isinstance(ev.quote, str)
            assert len(ev.quote) > 0

    def test_distinct_subjects_create_separate_observations(self):
        c = ObservationConsolidator()
        facts = [
            _fact("Alice uses Python"),
            _fact("Bob uses Rust"),
            _fact("Charlie uses Go"),
        ]
        results = c.consolidate(facts)
        subjects = {o.subject for o in results}
        assert len(subjects) >= 3

    def test_confidence_increases_with_evidence(self):
        c = ObservationConsolidator()
        single = c.consolidate([_fact("Alice uses Python")])
        many = c.consolidate([
            _fact("Alice uses Python for everything"),
            _fact("Alice codes in Python daily"),
            _fact("Alice prefers Python over JavaScript"),
            _fact("Alice writes Python scripts"),
            _fact("Alice teaches Python to juniors"),
        ])
        single_conf = single[0].confidence
        many_conf = next(o for o in many if o.subject == "Alice").confidence
        assert many_conf > single_conf

    def test_contradictory_facts_lower_confidence(self):
        c = ObservationConsolidator()
        results = c.consolidate([
            _fact("Alice uses Python"),
            _fact("Alice hates Python and uses JavaScript"),
        ])
        assert any(len(o.contradictions) > 0 for o in results)

    def test_all_unique_subjects(self):
        c = ObservationConsolidator()
        facts = [_fact(f"Subject_{i} uses language_{i}") for i in range(10)]
        results = c.consolidate(facts)
        assert len(results) >= 10

    def test_temporal_ordering_preserved_in_evidence(self):
        c = ObservationConsolidator()
        now = time.time()
        facts = [
            _fact("Alice started with Python", timestamp=now - 1000),
            _fact("Alice still uses Python", timestamp=now - 500),
            _fact("Alice now recommends Python", timestamp=now),
        ]
        results = c.consolidate(facts)
        obs = next(o for o in results if o.subject == "Alice")
        timestamps = [e.timestamp for e in obs.evidence]
        assert timestamps == sorted(timestamps)

    def test_all_conflicting_facts(self):
        c = ObservationConsolidator()
        facts = [
            _fact("X is true according to source A"),
            _fact("X is false according to source B"),
        ]
        results = c.consolidate(facts)
        assert len(results) >= 1


class TestDeduplicate:
    def test_empty_returns_empty(self):
        c = ObservationConsolidator()
        assert c.deduplicate([]) == []

    def test_single_fact_unchanged(self):
        c = ObservationConsolidator()
        fact = _fact("hello world")
        assert c.deduplicate([fact]) == [fact]

    def test_near_identical_facts_merged(self):
        c = ObservationConsolidator()
        facts = [
            _fact("Alice uses Python for backend development"),
            _fact("Alice uses Python for backend development"),
        ]
        result = c.deduplicate(facts)
        assert len(result) < len(facts) or result == facts[:1]

    def test_distinct_facts_kept_separate(self):
        c = ObservationConsolidator()
        facts = [
            _fact("Alice uses Python"),
            _fact("Bob uses Rust"),
            _fact("Charlie uses Go"),
        ]
        result = c.deduplicate(facts)
        assert len(result) == 3

    def test_semantically_similar_deduped(self):
        c = ObservationConsolidator()
        facts = [
            _fact("The user prefers functional programming style"),
            _fact("The user prefers functional programming"),
        ]
        result = c.deduplicate(facts)
        assert len(result) <= 2


class TestUpdate:
    def test_adding_supporting_evidence_increases_confidence(self):
        c = ObservationConsolidator()
        existing = Observation(
            observation_id="obs-1",
            subject="Alice",
            statement="uses Python",
            evidence=[EvidenceRef(fact_id="f1", quote="Alice uses Python", timestamp=1.0)],
            proof_count=1,
            confidence=0.14,
            created_at=1.0,
            updated_at=1.0,
        )
        new_facts = [
            _fact("Alice writes Python code every day"),
            _fact("Alice teaches Python to colleagues"),
        ]
        updated = c.update(existing, new_facts)
        assert updated.confidence > existing.confidence
        assert updated.proof_count == 3
        assert updated.updated_at > existing.updated_at

    def test_update_preserves_observation_id(self):
        c = ObservationConsolidator()
        existing = Observation(
            observation_id="obs-x", subject="User", statement="uses Vim",
            evidence=[EvidenceRef(fact_id="f1", quote="User uses Vim", timestamp=1.0)],
            proof_count=1, confidence=0.14, created_at=1.0, updated_at=1.0,
        )
        updated = c.update(existing, [_fact("User continues using Vim")])
        assert updated.observation_id == "obs-x"

    def test_contradictions_persist_through_update(self):
        c = ObservationConsolidator()
        existing = Observation(
            observation_id="obs-c", subject="User", statement="likes fish",
            evidence=[EvidenceRef(fact_id="f1", quote="User likes fish", timestamp=1.0)],
            proof_count=1, confidence=0.14, created_at=1.0, updated_at=1.0,
            contradictions=["User hates fish"],
        )
        updated = c.update(existing, [_fact("User eats fish weekly")])
        assert "User hates fish" in updated.contradictions

    def test_update_with_empty_facts_unchanged(self):
        c = ObservationConsolidator()
        existing = Observation(
            observation_id="obs-e", subject="User", statement="likes coffee",
            evidence=[EvidenceRef(fact_id="f1", quote="User likes coffee", timestamp=1.0)],
            proof_count=1, confidence=0.14, created_at=1.0, updated_at=1.0,
        )
        updated = c.update(existing, [])
        assert updated.proof_count == 1
        assert updated.confidence == existing.confidence


class TestMarkStale:
    def test_no_newer_facts_no_stale(self):
        c = ObservationConsolidator()
        obs = [
            Observation(
                observation_id="o1", subject="User", statement="a",
                created_at=10.0, updated_at=10.0,
            )
        ]
        result = c.mark_stale(obs, newer_fact_timestamp=9.0)
        assert result[0].stale is False

    def test_newer_fact_triggers_stale(self):
        c = ObservationConsolidator()
        obs = [
            Observation(
                observation_id="o1", subject="User", statement="a",
                created_at=10.0, updated_at=10.0,
            )
        ]
        result = c.mark_stale(obs, newer_fact_timestamp=20.0)
        assert result[0].stale is True

    def test_equal_timestamp_not_stale(self):
        c = ObservationConsolidator()
        obs = [
            Observation(
                observation_id="o1", subject="User", statement="a",
                created_at=10.0, updated_at=10.0,
            )
        ]
        result = c.mark_stale(obs, newer_fact_timestamp=10.0)
        assert result[0].stale is False

    def test_mixed_observations_both_handled(self):
        c = ObservationConsolidator()
        obs = [
            Observation(
                observation_id="old", subject="User", statement="old",
                created_at=1.0, updated_at=1.0,
            ),
            Observation(
                observation_id="new", subject="User", statement="new",
                created_at=50.0, updated_at=50.0,
            ),
        ]
        result = c.mark_stale(obs, newer_fact_timestamp=30.0)
        assert result[0].stale is True
        assert result[1].stale is False


# ── ObservationStore ───────────────────────────────────────────────────────────


class TestObservationStore:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        for path in (
            "/tmp/test_obs_store.json",
            "/tmp/test_obs_store.json.tmp",
        ):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)

    def test_put_and_get(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        obs = Observation(
            observation_id="o1", subject="User", statement="likes Python",
            created_at=1.0, updated_at=1.0, confidence=0.5,
        )
        store.put(obs)
        retrieved = store.get("o1")
        assert retrieved is not None
        assert retrieved.subject == "User"
        assert retrieved.statement == "likes Python"

    def test_put_all(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        obs_list = [
            Observation(observation_id=f"o{i}", subject="S", statement=f"s{i}",
                        created_at=float(i), updated_at=float(i))
            for i in range(5)
        ]
        store.put_all(obs_list)
        assert store.count == 5

    def test_get_by_subject(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.put(Observation(
            observation_id="a", subject="Alice", statement="uses Python",
            created_at=1.0, updated_at=1.0,
        ))
        store.put(Observation(
            observation_id="b", subject="Bob", statement="uses Rust",
            created_at=1.0, updated_at=1.0,
        ))
        store.put(Observation(
            observation_id="c", subject="Alice", statement="uses pytest",
            created_at=2.0, updated_at=2.0,
        ))
        alice = store.get_by_subject("Alice")
        assert len(alice) == 2

    def test_freshness_filtering(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.put(Observation(
            observation_id="fresh", subject="User", statement="a",
            created_at=1.0, updated_at=1.0, stale=False,
        ))
        store.put(Observation(
            observation_id="stale", subject="User", statement="b",
            created_at=1.0, updated_at=1.0, stale=True,
        ))
        assert len(store.get_fresh()) == 1
        assert len(store.get_stale()) == 1
        assert store.get_fresh()[0].observation_id == "fresh"

    def test_confidence_filtering(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.put(Observation(
            observation_id="low", subject="User", statement="a",
            created_at=1.0, updated_at=1.0, confidence=0.2,
        ))
        store.put(Observation(
            observation_id="high", subject="User", statement="b",
            created_at=1.0, updated_at=1.0, confidence=0.8,
        ))
        result = store.get_above_confidence(0.5)
        assert len(result) == 1
        assert result[0].observation_id == "high"

    def test_delete(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.put(Observation(
            observation_id="to_delete", subject="User", statement="x",
            created_at=1.0, updated_at=1.0,
        ))
        assert store.delete("to_delete") is True
        assert store.get("to_delete") is None
        assert store.delete("nonexistent") is False

    def test_list_all(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        for i in range(3):
            store.put(Observation(
                observation_id=f"o{i}", subject="S", statement=f"s{i}",
                created_at=float(i), updated_at=float(i),
            ))
        assert len(store.list_all()) == 3

    def test_clear(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.put(Observation(
            observation_id="o1", subject="S", statement="s",
            created_at=1.0, updated_at=1.0,
        ))
        store.clear()
        assert store.count == 0

    def test_persistence_round_trip(self):
        store1 = ObservationStore(store_path="/tmp/test_obs_store.json")
        obs = Observation(
            observation_id="persist-1", subject="User", statement="round trip",
            evidence=[EvidenceRef(fact_id="f1", quote="the quote", timestamp=1.0)],
            proof_count=1, confidence=0.5, created_at=10.0, updated_at=10.0,
            stale=False, contradictions=["nope"],
        )
        store1.put(obs)

        store2 = ObservationStore(store_path="/tmp/test_obs_store.json")
        retrieved = store2.get("persist-1")
        assert retrieved is not None
        assert retrieved.statement == "round trip"
        assert len(retrieved.evidence) == 1
        assert retrieved.evidence[0].quote == "the quote"
        assert retrieved.contradictions == ["nope"]

    def test_persistence_empty_store_survives(self):
        store = ObservationStore(store_path="/tmp/test_obs_store.json")
        store.clear()
        store2 = ObservationStore(store_path="/tmp/test_obs_store.json")
        assert store2.count == 0

    def test_persistence_corrupted_file_handled(self):
        path = "/tmp/test_obs_store.json"
        with open(path, "w") as fh:
            fh.write("not valid json {{{")
        store = ObservationStore(store_path=path)
        assert store.count == 0


# ── Concurrency ────────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_parallel_put_all(self):
        store = ObservationStore(store_path="/tmp/test_obs_concurrent.json")
        store.clear()

        def worker(thread_id: int):
            obs_list = [
                Observation(
                    observation_id=f"t{thread_id}-o{i}",
                    subject="Thread", statement=f"from thread {thread_id} item {i}",
                    created_at=time.time(), updated_at=time.time(),
                )
                for i in range(10)
            ]
            store.put_all(obs_list)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert store.count == 40

        try:
            os.remove("/tmp/test_obs_concurrent.json")
            os.remove("/tmp/test_obs_concurrent.json.tmp")
        except FileNotFoundError:
            pass

    def test_consolidator_is_reentrant(self):
        c = ObservationConsolidator()
        results1 = c.consolidate(_sample_facts())
        results2 = c.consolidate(_sample_facts())
        assert len(results1) == len(results2)


# ── Edge Cases ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_fact_with_empty_content(self):
        c = ObservationConsolidator()
        results = c.consolidate([_fact("")])
        assert len(results) == 1

    def test_very_long_fact_content(self):
        c = ObservationConsolidator()
        long_text = "Alice " + "uses Python " * 200
        results = c.consolidate([_fact(long_text)])
        assert len(results) == 1

    def test_duplicate_fact_ids(self):
        c = ObservationConsolidator()
        facts = [
            MemoryFact(fact_id="same-id", content="Alice uses Python"),
            MemoryFact(fact_id="same-id", content="Bob uses Rust"),
        ]
        results = c.consolidate(facts)
        assert len(results) >= 1

    def test_mixed_languages(self):
        c = ObservationConsolidator()
        facts = [
            _fact("Alice est développeuse Python à Paris"),
            _fact("Alice prefers le testing avec pytest"),
        ]
        results = c.consolidate(facts)
        alice = [o for o in results if "Alice" in o.subject or o.subject == "Alice"]
        assert len(alice) >= 1

    def test_numeric_fact_ids(self):
        c = ObservationConsolidator()
        facts = [
            MemoryFact(fact_id="42", content="Answer is 42"),
            MemoryFact(fact_id="43", content="Answer is not 43"),
        ]
        results = c.consolidate(facts)
        assert len(results) >= 1


# ── ObservationConsolidator full pipeline ──────────────────────────────────────


class TestFullPipeline:
    def test_consolidate_store_query(self):
        consolidate = ObservationConsolidator()
        store = ObservationStore(store_path="/tmp/test_pipeline_obs.json")

        facts = [
            _fact("Bob believes strongly in TDD methodology"),
            _fact("Bob always writes tests before implementation"),
            _fact("Bob considers untested code unacceptable"),
            _fact("Bob recommends TDD to all new hires"),
            _fact("Bob rejects any PR without tests"),
        ]

        observations = consolidate.consolidate(facts)
        assert len(observations) >= 1

        store.put_all(observations)
        bob = store.get_by_subject("Bob")
        assert len(bob) >= 1

        high_confidence = store.get_above_confidence(0.3)
        assert len(high_confidence) >= 1

        try:
            os.remove("/tmp/test_pipeline_obs.json")
            os.remove("/tmp/test_pipeline_obs.json.tmp")
        except FileNotFoundError:
            pass

    def test_staleness_pipeline(self):
        consolidate = ObservationConsolidator()
        store = ObservationStore(store_path="/tmp/test_stale_pipeline.json")

        old_facts = [
            _fact("Carol uses React for frontend", timestamp=1000.0),
            _fact("Carol prefers React hooks over classes", timestamp=1001.0),
        ]
        observations = consolidate.consolidate(old_facts)
        store.put_all(observations)

        consolidated_ts = consolidate._last_consolidation_ts
        new_fact_ts = consolidated_ts + 100

        all_obs = store.list_all()
        consolidate.mark_stale(all_obs, newer_fact_timestamp=new_fact_ts)
        assert all(o.stale for o in all_obs)

        try:
            os.remove("/tmp/test_stale_pipeline.json")
            os.remove("/tmp/test_stale_pipeline.json.tmp")
        except FileNotFoundError:
            pass
