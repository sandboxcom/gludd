from general_ludd.memory.hindsight_adapter import HindsightMemoryAdapter


def test_disabled_adapter_retains_and_recalls_fallback_memory():
    adapter = HindsightMemoryAdapter(enabled=False)
    memory_id = adapter.retain("The deployment uses blue green rollout.", {"team": "platform"})
    results = adapter.recall("blue rollout")
    assert memory_id.startswith("mem_")
    assert results[0]["content"].startswith("The deployment")
    assert adapter.search("platform")[0]["metadata"]["team"] == "platform"
