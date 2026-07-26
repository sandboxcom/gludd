"""E2E coverage for memory-system expiry, isolation, fallback, and consolidation.

These workflows exercise the public local-memory and observation APIs together,
while deliberately driving defensive branches that are difficult to reach from
the happy-path integration suite.
"""

from __future__ import annotations

import json
import time

import pytest

from general_ludd.memory.local import LocalAgentMemory
from general_ludd.memory.memory_bank import (
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryEntry,
)
from general_ludd.memory.observation_consolidator import (
    MemoryFact,
    ObservationConsolidator,
    ObservationStore,
)


@pytest.mark.asyncio
async def test_local_memory_expiry_and_purge_are_observable(tmp_path, monkeypatch):
    """Expired records disappear from get/list and purge reports removals."""
    store = LocalAgentMemory(tmp_path / "memory")
    try:
        await store.set("agent", "short-lived", "value", ttl_seconds=1)
        original_time = time.time()
        monkeypatch.setattr(
            "general_ludd.memory.local.time.time", lambda: original_time + 2
        )

        assert await store.get("agent", "short-lived") is None
        assert await store.list_by_namespace("agent") == []

        monkeypatch.setattr("general_ludd.memory.local.time.time", lambda: original_time)
        record = await store.set("agent", "another", "value", ttl_seconds=1)
        record.created_at = original_time - 2
        # Keep the cache entry present so purge_expired exercises its cleanup
        # branch instead of diskcache's native expiry removing it first.
        store._cache.set(
            store._data_key("agent", "another", "default"),
            record.as_dict(),
            expire=None,
        )
        monkeypatch.setattr(
            "general_ludd.memory.local.time.time", lambda: original_time + 2
        )
        assert await store.purge_expired() == 1
        assert await store.get("agent", "another") is None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_local_memory_project_isolation_and_missing_delete(tmp_path):
    """Identical keys remain isolated by project and missing deletes are false."""
    store = LocalAgentMemory(tmp_path / "memory")
    try:
        await store.set("agent", "shared", "alpha", project_id="project-a")
        await store.set("agent", "shared", "beta", project_id="project-b")

        first = await store.get("agent", "shared", project_id="project-a")
        second = await store.get("agent", "shared", project_id="project-b")
        assert first is not None and first.value == "alpha"
        assert second is not None and second.value == "beta"
        assert await store.get("agent", "shared") is None
        assert await store.delete("agent", "missing", project_id="project-a") is False
    finally:
        store.close()


def test_memory_bank_registry_isolation_and_lifecycle():
    """Registry lifecycle keeps bank contents isolated and handles absent IDs."""
    registry = MemoryBankRegistry()
    bank_a = registry.create_bank(MemoryBankConfig(bank_id="a"))
    bank_b = registry.get_or_create_bank(MemoryBankConfig(bank_id="b"))
    bank_a.retain(MemoryEntry(content="alpha deployment", tags=["alpha"]))
    bank_b.retain(MemoryEntry(content="beta deployment", tags=["beta"]))

    assert [fact.content for fact in bank_a.get_facts("alpha")] == ["alpha deployment"]
    assert bank_a.get_facts("beta") == []
    assert [fact.content for fact in bank_b.get_facts("beta")] == ["beta deployment"]
    assert registry.get_bank("missing") is None
    assert registry.delete_bank("missing") is False
    assert registry.delete_bank("a") is True
    assert registry.bank_count() == 1

    with pytest.raises(ValueError, match="already exists"):
        registry.create_bank(MemoryBankConfig(bank_id="b"))


def test_observation_store_malformed_file_fallback_and_stale_queries(tmp_path):
    """Malformed persisted data is ignored, while valid observations round-trip."""
    path = tmp_path / "observations.json"
    path.write_text("not-json")
    store = ObservationStore(str(path))
    assert store.count == 0

    facts = [
        MemoryFact("f1", "Alice uses Python for automation", timestamp=1.0),
        MemoryFact("f2", "Alice uses Python for automation", timestamp=2.0),
        MemoryFact("f3", "Alice uses Go for services", timestamp=3.0),
    ]
    consolidator = ObservationConsolidator(similarity_threshold=0.4)
    observations = consolidator.consolidate(facts)
    assert observations
    store.put_all(observations)
    assert store.count == len(observations)

    stale = consolidator.mark_stale(
        observations, newer_fact_timestamp=time.time() + 10.0
    )
    assert all(item.stale for item in stale)
    store.put_all(stale)
    assert store.get_stale()
    assert store.get_fresh() == []
    assert store.get_above_confidence(0.0)

    reloaded = ObservationStore(str(path))
    assert reloaded.count == len(observations)
    assert reloaded.delete("missing") is False
    assert reloaded.delete(observations[0].observation_id) is True

    persisted = json.loads(path.read_text())
    assert observations[0].observation_id not in persisted


def test_consolidator_empty_and_contradiction_branches():
    """Empty input and contradictory subjects yield deterministic results."""
    consolidator = ObservationConsolidator(max_contradictions_stored=1)
    assert consolidator.consolidate([]) == []

    facts = [
        MemoryFact("same-1", "Project Orion uses Python daily"),
        MemoryFact("same-2", "Project Orion uses Python daily"),
        MemoryFact("other", "Project Orion uses Rust exclusively"),
    ]
    observations = consolidator.consolidate(facts)
    assert len(observations) >= 1
    assert all(obs.subject == "Project Orion" for obs in observations)
    assert any(obs.proof_count >= 1 for obs in observations)
    assert all(len(obs.contradictions) <= 1 for obs in observations)
