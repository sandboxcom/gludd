from general_ludd.memory.memory_bank import MemoryBank, MemoryBankConfig, MemoryEntry, MentalModel


def test_memory_bank_prioritizes_models_and_facts_in_reflection():
    bank = MemoryBank(MemoryBankConfig(bank_id="release", mission="Ship safely", directives=["verify CI"]))
    bank.add_mental_model(MentalModel(subject="release", content="Require full E2E", priority=10))
    bank.retain(MemoryEntry(content="CI is green", tags=["release"]))
    result = bank.recall("release CI")
    assert result.mental_models[0].subject == "release"
    assert result.facts[0].content == "CI is green"
    assert "Ship safely" in result.synthesized
