"""Unit tests for Mental Models and Memory Banks.

Covers:
  - Disposition validation and serialization
  - MemoryBankConfig creation and serialization
  - MentalModel CRUD (add, get, update, delete)
  - MemoryEntry (fact) CRUD
  - Recall: mental models returned before facts
  - Reflect: disposition and directives affect output
  - Bank isolation: data in bank A not visible in bank B
  - Registry CRUD (create, get, list, delete, duplicate rejection)
  - Bank templates loading from YAML
  - Priority ordering: high-priority models surface first
  - Edge cases: empty bank, models-only, facts-only
  - Thread safety: concurrent operations do not corrupt state
"""

from __future__ import annotations

import threading
import time

import pytest_asyncio

from general_ludd.memory.memory_bank import (
    Disposition,
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryBankResult,
    MemoryEntry,
    MentalModel,
    load_bank_templates,
)

# === Disposition ============================================================


class TestDisposition:
    def test_defaults(self):
        d = Disposition()
        assert d.skepticism == 3
        assert d.literalism == 3
        assert d.empathy == 3

    def test_custom_values(self):
        d = Disposition(skepticism=5, literalism=1, empathy=4)
        assert d.skepticism == 5
        assert d.literalism == 1
        assert d.empathy == 4

    def test_value_clamping_rejects_zero(self):
        import pytest
        with pytest.raises(ValueError, match=r"must be 1-5"):
            Disposition(skepticism=0)

    def test_value_clamping_rejects_six(self):
        import pytest
        with pytest.raises(ValueError, match=r"must be 1-5"):
            Disposition(literalism=6)

    def test_to_dict(self):
        d = Disposition(skepticism=4, literalism=2, empathy=3)
        result = d.to_dict()
        assert result == {"skepticism": 4, "literalism": 2, "empathy": 3}

    def test_from_dict(self):
        d = Disposition.from_dict({"skepticism": 5, "literalism": 1, "empathy": 4})
        assert d.skepticism == 5
        assert d.literalism == 1
        assert d.empathy == 4

    def test_from_dict_defaults(self):
        d = Disposition.from_dict({})
        assert d.skepticism == 3
        assert d.literalism == 3
        assert d.empathy == 3


# === MemoryBankConfig =======================================================


class TestMemoryBankConfig:
    def test_minimal_config(self):
        c = MemoryBankConfig(bank_id="test-bank")
        assert c.bank_id == "test-bank"
        assert c.mission == ""
        assert c.directives == []
        assert c.disposition.skepticism == 3

    def test_full_config(self):
        disp = Disposition(skepticism=4, literalism=2, empathy=1)
        c = MemoryBankConfig(
            bank_id="secure-bank",
            mission="Be careful",
            directives=["rule1", "rule2"],
            disposition=disp,
        )
        assert c.mission == "Be careful"
        assert c.directives == ["rule1", "rule2"]
        assert c.disposition.skepticism == 4

    def test_to_dict_and_from_dict_roundtrip(self):
        original = MemoryBankConfig(
            bank_id="roundtrip",
            mission="test mission",
            directives=["d1"],
            disposition=Disposition(skepticism=5, literalism=1, empathy=2),
        )
        restored = MemoryBankConfig.from_dict(original.to_dict())
        assert restored.bank_id == original.bank_id
        assert restored.mission == original.mission
        assert restored.directives == original.directives
        assert restored.disposition.skepticism == 5
        assert restored.disposition.literalism == 1
        assert restored.disposition.empathy == 2


# === MentalModel ============================================================


class TestMentalModel:
    def test_defaults(self):
        m = MentalModel()
        assert m.model_id
        assert m.subject == ""
        assert m.content == ""
        assert m.priority == 5
        assert m.created_by == "system"

    def test_priority_clamped(self):
        m = MentalModel(priority=15)
        assert m.priority == 10
        m2 = MentalModel(priority=0)
        assert m2.priority == 1

    def test_to_dict_and_from_dict_roundtrip(self):
        original = MentalModel(
            model_id="abc123",
            subject="testing",
            content="Always test first",
            priority=8,
            created_by="user",
            tags=["tdd", "quality"],
        )
        restored = MentalModel.from_dict(original.to_dict())
        assert restored.model_id == "abc123"
        assert restored.subject == "testing"
        assert restored.content == "Always test first"
        assert restored.priority == 8
        assert restored.created_by == "user"
        assert restored.tags == ["tdd", "quality"]


# === MemoryEntry ============================================================


class TestMemoryEntry:
    def test_defaults(self):
        e = MemoryEntry()
        assert e.entry_id
        assert e.content == ""
        assert e.tags == []

    def test_roundtrip(self):
        e = MemoryEntry(
            entry_id="e1", content="some fact", source="file.py:42", tags=["bug"],
        )
        restored = MemoryEntry.from_dict(e.to_dict())
        assert restored.entry_id == "e1"
        assert restored.content == "some fact"
        assert restored.source == "file.py:42"
        assert restored.tags == ["bug"]


# === MemoryBankResult =======================================================


class TestMemoryBankResult:
    def test_defaults(self):
        r = MemoryBankResult()
        assert r.mental_models == []
        assert r.facts == []
        assert r.synthesized == ""


# === MemoryBank =============================================================


@pytest_asyncio.fixture
def coding_bank():
    config = MemoryBankConfig(
        bank_id="coding",
        mission="optimize for correctness",
        directives=["write tests first", "never suppress lint warnings"],
        disposition=Disposition(skepticism=4, literalism=3, empathy=2),
    )
    return MemoryBank(config)


@pytest_asyncio.fixture
def secure_bank():
    config = MemoryBankConfig(
        bank_id="secure",
        mission="safety-first operations",
        directives=["validate backups exist"],
        disposition=Disposition(skepticism=5, literalism=5, empathy=1),
    )
    return MemoryBank(config)


class TestMemoryBankCreation:
    def test_created_with_config(self, coding_bank):
        assert coding_bank.bank_id == "coding"
        assert coding_bank.config.mission == "optimize for correctness"

    def test_created_empty(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="empty"))
        assert bank.bank_id == "empty"
        assert bank.get_mental_models() == []
        assert bank.get_facts() == []


class TestMentalModelCRUD:
    def test_add_and_retrieve_model(self, coding_bank):
        model = MentalModel(subject="tdd", content="Always write tests first", priority=8)
        coding_bank.add_mental_model(model)
        models = coding_bank.get_mental_models()
        assert len(models) == 1
        assert models[0].subject == "tdd"

    def test_get_models_with_subject_filter(self, coding_bank):
        coding_bank.add_mental_model(MentalModel(subject="security", content="Use parameterized queries"))
        coding_bank.add_mental_model(MentalModel(subject="performance", content="Profile before optimizing"))
        result = coding_bank.get_mental_models(subject_filter="security")
        assert len(result) == 1
        assert result[0].subject == "security"

    def test_get_models_with_tag_filter(self, coding_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="linting", content="Run lint", tags=["quality", "ci"])
        )
        coding_bank.add_mental_model(
            MentalModel(subject="formatting", content="Use black", tags=["style"])
        )
        result = coding_bank.get_mental_models(subject_filter="ci")
        assert len(result) == 1
        assert result[0].subject == "linting"

    def test_update_mental_model(self, coding_bank):
        coding_bank.add_mental_model(
            MentalModel(model_id="mm1", subject="python", content="Python is great")
        )
        updated = coding_bank.update_mental_model("mm1", "Python is awesome")
        assert updated is not None
        assert updated.content == "Python is awesome"

    def test_update_nonexistent_model(self, coding_bank):
        result = coding_bank.update_mental_model("nope", "new content")
        assert result is None

    def test_delete_mental_model(self, coding_bank):
        coding_bank.add_mental_model(MentalModel(model_id="mm-del", subject="tmp"))
        assert coding_bank.delete_mental_model("mm-del") is True
        assert coding_bank.get_mental_models() == []

    def test_delete_nonexistent_model(self, coding_bank):
        assert coding_bank.delete_mental_model("nope") is False


class TestFactCRUD:
    def test_retain_fact(self, coding_bank):
        fact = MemoryEntry(content="File daemon.py has 1200 lines", tags=["codebase"])
        coding_bank.retain(fact)
        facts = coding_bank.get_facts()
        assert len(facts) == 1
        assert facts[0].content == "File daemon.py has 1200 lines"

    def test_retain_multiple_facts(self, coding_bank):
        coding_bank.retain(MemoryEntry(content="fact 1"))
        coding_bank.retain(MemoryEntry(content="fact 2"))
        assert len(coding_bank.get_facts()) == 2

    def test_get_facts_with_tag_filter(self, coding_bank):
        coding_bank.retain(MemoryEntry(content="a", tags=["critical"]))
        coding_bank.retain(MemoryEntry(content="b", tags=["low"]))
        result = coding_bank.get_facts(tag_filter="critical")
        assert len(result) == 1
        assert result[0].content == "a"

    def test_delete_fact(self, coding_bank):
        coding_bank.retain(MemoryEntry(entry_id="f1", content="remove me"))
        assert coding_bank.delete_fact("f1") is True
        assert coding_bank.get_facts() == []

    def test_delete_nonexistent_fact(self, coding_bank):
        assert coding_bank.delete_fact("nope") is False


class TestModelsBeforeFactsInRecall:
    def test_mental_models_come_first(self, coding_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="error handling", content="Use try/except", priority=9)
        )
        coding_bank.retain(MemoryEntry(content="daemon.py uses try/except in 3 places"))
        result = coding_bank.recall("error handling")
        assert len(result.mental_models) >= 1
        assert result.mental_models[0].subject == "error handling"

    def test_recall_with_context(self, coding_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="config", content="Use YAML for config")
        )
        result = coding_bank.recall("config", context={"session": "test"})
        assert len(result.mental_models) == 1


class TestReflect:
    def test_mission_appears_in_reflect(self, coding_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="testing", content="Write tests before code")
        )
        output = coding_bank.reflect("how should I code?")
        assert "optimize for correctness" in output.lower()

    def test_directives_appear_in_reflect(self, coding_bank):
        output = coding_bank.reflect("what rules?")
        assert "write tests first" in output.lower()

    def test_disposition_appears_in_reflect(self, coding_bank):
        output = coding_bank.reflect("tell me about yourself")
        assert "skepticism=4" in output
        assert "literalism=3" in output
        assert "empathy=2" in output

    def test_empty_bank_reflect(self, coding_bank):
        output = coding_bank.reflect("anything")
        assert "optimize for correctness" in output.lower()
        assert "write tests first" in output.lower()
        assert "no relevant mental models or facts found" in output.lower()

    def test_reflect_with_high_skepticism(self):
        bank = MemoryBank(
            MemoryBankConfig(
                bank_id="critic",
                disposition=Disposition(skepticism=5, literalism=5, empathy=1),
            )
        )
        bank.add_mental_model(
            MentalModel(subject="deployment", content="Always use blue/green")
        )
        output = bank.reflect("how to deploy?")
        assert "skepticism=5" in output

    def test_reflect_with_high_empathy(self):
        bank = MemoryBank(
            MemoryBankConfig(
                bank_id="mentor",
                disposition=Disposition(skepticism=1, literalism=2, empathy=5),
            )
        )
        bank.add_mental_model(
            MentalModel(subject="learning", content="Take small steps")
        )
        output = bank.reflect("how to learn?")
        assert "empathy=5" in output


class TestBankIsolation:
    def test_models_not_shared_across_banks(self, coding_bank, secure_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="code-style", content="Use ruff")
        )
        secure_models = secure_bank.get_mental_models()
        assert len(secure_models) == 0

    def test_facts_not_shared_across_banks(self, coding_bank, secure_bank):
        coding_bank.retain(MemoryEntry(content="coding fact"))
        secure_facts = secure_bank.get_facts()
        assert len(secure_facts) == 0

    def test_full_isolation_roundtrip(self, coding_bank, secure_bank):
        coding_bank.add_mental_model(
            MentalModel(subject="x", content="model in coding")
        )
        coding_bank.retain(MemoryEntry(content="fact in coding"))

        secure_bank.add_mental_model(
            MentalModel(subject="y", content="model in secure")
        )

        assert len(coding_bank.get_mental_models()) == 1
        assert len(coding_bank.get_facts()) == 1
        assert len(secure_bank.get_mental_models()) == 1
        assert len(secure_bank.get_facts()) == 0

        c_result = coding_bank.recall("x")
        assert any(m.subject == "x" for m in c_result.mental_models)
        s_result = secure_bank.recall("x")
        assert len(s_result.mental_models) == 0


# === MemoryBankRegistry =====================================================


class TestRegistry:
    @pytest_asyncio.fixture
    def registry(self):
        return MemoryBankRegistry()

    def test_create_and_get_bank(self, registry):
        config = MemoryBankConfig(bank_id="b1")
        bank = registry.create_bank(config)
        assert bank.bank_id == "b1"
        retrieved = registry.get_bank("b1")
        assert retrieved is bank

    def test_create_duplicate_raises(self, registry):
        registry.create_bank(MemoryBankConfig(bank_id="dup"))
        import pytest
        with pytest.raises(ValueError, match="already exists"):
            registry.create_bank(MemoryBankConfig(bank_id="dup"))

    def test_get_or_create_bank_new(self, registry):
        config = MemoryBankConfig(bank_id="new-bank")
        bank = registry.get_or_create_bank(config)
        assert bank.bank_id == "new-bank"

    def test_get_or_create_bank_existing(self, registry):
        config = MemoryBankConfig(bank_id="existing")
        first = registry.get_or_create_bank(config)
        second = registry.get_or_create_bank(config)
        assert first is second

    def test_get_nonexistent_bank(self, registry):
        assert registry.get_bank("nope") is None

    def test_list_banks(self, registry):
        registry.create_bank(MemoryBankConfig(bank_id="a"))
        registry.create_bank(MemoryBankConfig(bank_id="b"))
        banks = registry.list_banks()
        assert len(banks) == 2
        ids = {b.bank_id for b in banks}
        assert ids == {"a", "b"}

    def test_delete_bank(self, registry):
        registry.create_bank(MemoryBankConfig(bank_id="to-delete"))
        assert registry.delete_bank("to-delete") is True
        assert registry.get_bank("to-delete") is None

    def test_delete_nonexistent_bank(self, registry):
        assert registry.delete_bank("nope") is False

    def test_bank_count(self, registry):
        assert registry.bank_count() == 0
        registry.create_bank(MemoryBankConfig(bank_id="c1"))
        assert registry.bank_count() == 1
        registry.create_bank(MemoryBankConfig(bank_id="c2"))
        assert registry.bank_count() == 2


# === Priority Ordering ======================================================


class TestPriorityOrdering:
    def test_high_priority_models_surface_first(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="prio"))
        bank.add_mental_model(MentalModel(subject="topic low", priority=1, content="A common topic"))
        bank.add_mental_model(MentalModel(subject="topic high", priority=9, content="A common topic"))
        bank.add_mental_model(MentalModel(subject="topic mid", priority=5, content="A common topic"))

        result = bank.recall("topic")
        assert len(result.mental_models) == 3
        assert result.mental_models[0].subject == "topic high"
        assert result.mental_models[-1].subject == "topic low"

    def test_equal_priority_stable(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="equal"))
        bank.add_mental_model(MentalModel(model_id="a", subject="first", priority=5))
        bank.add_mental_model(MentalModel(model_id="b", subject="second", priority=5))
        models = bank.get_mental_models()
        assert len(models) == 2


# === Edge Cases =============================================================


class TestEdgeCases:
    def test_empty_bank_recall(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="empty"))
        result = bank.recall("anything")
        assert result.mental_models == []
        assert result.facts == []
        assert "no relevant" in result.synthesized.lower()

    def test_bank_with_only_mental_models(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="models-only"))
        bank.add_mental_model(MentalModel(subject="design", content="Prefer composition"))
        result = bank.recall("design")
        assert len(result.mental_models) == 1
        assert len(result.facts) == 0

    def test_bank_with_only_facts(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="facts-only"))
        bank.retain(MemoryEntry(content="The CI pipeline takes 8 minutes"))
        result = bank.recall("pipeline")
        assert len(result.mental_models) == 0
        assert len(result.facts) == 1
        assert result.facts[0].content == "The CI pipeline takes 8 minutes"

    def test_recall_no_match(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="unrelated"))
        bank.add_mental_model(MentalModel(subject="security", content="Use TLS"))
        bank.retain(MemoryEntry(content="TLS certs expire monthly"))
        result = bank.recall("pizza delivery")
        assert len(result.mental_models) == 0
        assert len(result.facts) == 0

    def test_update_creates_timestamp(self):
        m = MentalModel(subject="x", content="old")
        before = time.time()
        time.sleep(0.01)
        m2 = MentalModel(model_id=m.model_id, subject="x", content="new")
        assert m2.updated_at >= before

    def test_add_model_assigns_id_when_empty(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="auto-id"))
        model = MentalModel(subject="test", content="content", model_id="")
        result = bank.add_mental_model(model)
        assert result.model_id
        assert len(result.model_id) == 12


# === Templates ==============================================================


class TestTemplates:
    def test_load_templates_from_yaml(self, monkeypatch, tmp_path):
        templates_yml = tmp_path / "templates.yml"
        templates_yml.write_text("""\
templates:
  coding-assistant:
    bank_id: coding-assistant
    mission: "optimize for correct working code"
    disposition:
      skepticism: 4
      literalism: 2
      empathy: 3
    directives:
      - "Write passing tests before claiming completion"
      - "Prefer standard-library and existing dependencies"
  teacher:
    bank_id: teacher
    mission: "explain clearly, be patient"
    disposition:
      skepticism: 1
      literalism: 2
      empathy: 5
    directives:
      - "Explain the why, not just the what"
""")
        loaded = load_bank_templates(str(templates_yml))
        assert "coding-assistant" in loaded
        assert "teacher" in loaded

        ca = loaded["coding-assistant"]
        assert ca.bank_id == "coding-assistant"
        assert ca.disposition.skepticism == 4
        assert len(ca.directives) == 2

        t = loaded["teacher"]
        assert t.bank_id == "teacher"
        assert t.disposition.empathy == 5

    def test_load_templates_produces_valid_banks(self, monkeypatch, tmp_path):
        templates_yml = tmp_path / "templates.yml"
        templates_yml.write_text("""\
templates:
  critic:
    bank_id: critic
    mission: "find all bugs"
    disposition:
      skepticism: 5
      literalism: 5
      empathy: 1
    directives:
      - "Be thorough"
""")
        loaded = load_bank_templates(str(templates_yml))
        registry = MemoryBankRegistry()
        for name, config in loaded.items():
            bank = registry.create_bank(config)
            bank.add_mental_model(MentalModel(subject=f"{name}-test", content="test"))
            result = bank.recall("test")
            assert len(result.mental_models) == 1

    def test_load_templates_from_actual_file(self):
        import os
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        templates_path = os.path.join(repo_root, "config", "memory_bank_templates.yml")
        if not os.path.exists(templates_path):
            import pytest
            pytest.skip("config/memory_bank_templates.yml not found at project root")
        loaded = load_bank_templates(templates_path)
        assert len(loaded) >= 5
        assert "coding-assistant" in loaded
        assert "code-reviewer" in loaded
        assert "researcher" in loaded
        assert "devops-operator" in loaded
        assert "teacher" in loaded
        reviewer = loaded["code-reviewer"]
        assert reviewer.disposition.skepticism == 5
        assert reviewer.disposition.empathy == 1


# === Thread Safety ==========================================================


class TestThreadSafety:
    def test_concurrent_model_additions(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="concurrent"))
        barrier = threading.Barrier(5)

        def add_model(i):
            barrier.wait()
            bank.add_mental_model(
                MentalModel(subject=f"model-{i}", content=f"content-{i}")
            )

        threads = [threading.Thread(target=add_model, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        models = bank.get_mental_models()
        assert len(models) == 5

    def test_concurrent_fact_additions(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="concurrent-facts"))
        barrier = threading.Barrier(10)

        def add_fact(i):
            barrier.wait()
            bank.retain(MemoryEntry(content=f"fact-{i}"))

        threads = [threading.Thread(target=add_fact, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(bank.get_facts()) == 10

    def test_concurrent_mixed_operations(self):
        bank = MemoryBank(MemoryBankConfig(bank_id="mixed-concurrent"))
        barrier = threading.Barrier(6)
        errors = []

        def add_model():
            try:
                barrier.wait()
                bank.add_mental_model(MentalModel(subject="mm", content="model"))
            except Exception as e:
                errors.append(e)

        def add_fact():
            try:
                barrier.wait()
                bank.retain(MemoryEntry(content="fact"))
            except Exception as e:
                errors.append(e)

        def do_recall():
            try:
                barrier.wait()
                bank.recall("test")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_model),
            threading.Thread(target=add_fact),
            threading.Thread(target=do_recall),
            threading.Thread(target=add_model),
            threading.Thread(target=add_fact),
            threading.Thread(target=do_recall),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_registry_concurrent_bank_creation(self):
        registry = MemoryBankRegistry()
        barrier = threading.Barrier(5)

        def create_bank(i):
            barrier.wait()
            registry.create_bank(MemoryBankConfig(bank_id=f"b{i}"))

        threads = [threading.Thread(target=create_bank, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert registry.bank_count() == 5
