from general_ludd.memory.observation_consolidator import MemoryFact, ObservationConsolidator, ObservationStore


def test_consolidator_persists_evidence_backed_observation(tmp_path):
    facts = [
        MemoryFact("1", "Alice owns deployment safety", timestamp=1),
        MemoryFact("2", "Alice owns deployment safety", timestamp=2),
    ]
    observations = ObservationConsolidator().consolidate(facts)
    assert len(observations) == 1
    assert observations[0].proof_count == 1
    store = ObservationStore(str(tmp_path / "observations.json"))
    store.put(observations[0])
    assert store.get(observations[0].observation_id) is not None
