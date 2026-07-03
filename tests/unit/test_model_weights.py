"""Tests for the model_weights package — schema, store, loader, seed data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from general_ludd.schemas.benchmark import TaskRole


class TestModelWeightSchema:
    def test_valid_schema(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        now = datetime.now(UTC)
        entry = ModelWeightSchema(
            model_id="claude_opus_48",
            task_role=TaskRole.PLANNER,
            weight=0.85,
            updated_at=now,
            source="benchmark",
        )
        assert entry.model_id == "claude_opus_48"
        assert entry.task_role == TaskRole.PLANNER
        assert entry.weight == 0.85
        assert entry.updated_at == now
        assert entry.source == "benchmark"

    def test_weight_out_of_range_raises(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        with pytest.raises(ValueError):
            ModelWeightSchema(
                model_id="test",
                task_role=TaskRole.EDITOR,
                weight=1.5,
                updated_at=datetime.now(UTC),
                source="manual",
            )

    def test_weight_negative_raises(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        with pytest.raises(ValueError):
            ModelWeightSchema(
                model_id="test",
                task_role=TaskRole.EDITOR,
                weight=-0.1,
                updated_at=datetime.now(UTC),
                source="manual",
            )

    def test_invalid_source_raises(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        with pytest.raises(ValueError):
            ModelWeightSchema(
                model_id="test",
                task_role=TaskRole.EDITOR,
                weight=0.5,
                updated_at=datetime.now(UTC),
                source="invalid_source",
            )

    def test_defaults(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        entry = ModelWeightSchema(
            model_id="gemini_compactor",
            task_role=TaskRole.COMPACTOR,
            weight=0.90,
        )
        assert entry.source == "manual"
        assert isinstance(entry.updated_at, datetime)

    def test_model_dump(self):
        from general_ludd.model_weights.schema import ModelWeightSchema

        now = datetime(2026, 7, 3, tzinfo=UTC)
        entry = ModelWeightSchema(
            model_id="gpt41_nano",
            task_role=TaskRole.ENUMERATOR,
            weight=0.80,
            updated_at=now,
            source="benchmark",
        )
        d = entry.model_dump()
        # datetime serializes to string via mode="json"
        assert d["model_id"] == "gpt41_nano"
        assert d["weight"] == 0.80
        assert d["source"] == "benchmark"


class TestModelWeightStore:
    def test_set_and_get(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        entry = store.set(
            model_id="claude_opus_48",
            task_role=TaskRole.PLANNER,
            weight=0.85,
            source="benchmark",
        )
        assert entry.weight == 0.85

        retrieved = store.get("claude_opus_48", TaskRole.PLANNER)
        assert retrieved is not None
        assert retrieved.model_id == "claude_opus_48"
        assert retrieved.weight == 0.85
        assert retrieved.source == "benchmark"

    def test_set_updates_existing(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set(model_id="claude_opus_48", task_role=TaskRole.PLANNER, weight=0.85, source="benchmark")
        updated = store.set(model_id="claude_opus_48", task_role=TaskRole.PLANNER, weight=0.92, source="operator")
        assert updated.weight == 0.92
        assert updated.source == "operator"

        retrieved = store.get("claude_opus_48", TaskRole.PLANNER)
        assert retrieved is not None
        assert retrieved.weight == 0.92
        assert retrieved.source == "operator"

    def test_get_missing_returns_none(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        assert store.get("nonexistent", TaskRole.PLANNER) is None

    def test_list_by_role(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set("claude_opus_48", TaskRole.PLANNER, 0.85)
        store.set("claude_sonnet_46", TaskRole.PLANNER, 0.80)
        store.set("claude_haiku_45", TaskRole.EDITOR, 0.80)

        planners = store.list_by_role(TaskRole.PLANNER)
        assert len(planners) == 2
        model_ids = {e.model_id for e in planners}
        assert model_ids == {"claude_opus_48", "claude_sonnet_46"}

        editors = store.list_by_role(TaskRole.EDITOR)
        assert len(editors) == 1
        assert editors[0].model_id == "claude_haiku_45"

    def test_list_by_role_empty(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        assert store.list_by_role(TaskRole.ENUMERATOR) == []

    def test_all_weights(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set("claude_opus_48", TaskRole.PLANNER, 0.85)
        store.set("claude_haiku_45", TaskRole.EDITOR, 0.80)
        store.set("gemini_compactor", TaskRole.COMPACTOR, 0.90)

        all_w = store.all_weights()
        assert len(all_w) == 3

    def test_all_weights_empty(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        assert store.all_weights() == []

    def test_sort_by_weight_desc(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set("model_a", TaskRole.PLANNER, 0.50)
        store.set("model_b", TaskRole.PLANNER, 0.85)
        store.set("model_c", TaskRole.PLANNER, 0.75)

        sorted_weights = store.list_by_role(TaskRole.PLANNER)
        assert sorted_weights[0].weight == 0.85
        assert sorted_weights[1].weight == 0.75
        assert sorted_weights[2].weight == 0.50


class TestModelWeightStorePersistence:
    def test_save_and_load_roundtrip(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set("claude_opus_48", TaskRole.PLANNER, 0.85, source="benchmark")
        store.set("claude_haiku_45", TaskRole.EDITOR, 0.80, source="benchmark")
        store.set("gemini_compactor", TaskRole.COMPACTOR, 0.90, source="benchmark")

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "weights.json"
            store.save(path)

            loaded = ModelWeightStore.load(path)
            assert len(loaded.all_weights()) == 3

            entry = loaded.get("claude_opus_48", TaskRole.PLANNER)
            assert entry is not None
            assert entry.weight == 0.85
            assert entry.source == "benchmark"
            assert entry.task_role == TaskRole.PLANNER

    def test_load_empty_file(self):
        from general_ludd.model_weights.store import ModelWeightStore

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.json"
            path.write_text("[]")

            store = ModelWeightStore.load(path)
            assert store.all_weights() == []

    def test_load_nonexistent_file_returns_empty(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore.load(Path("/nonexistent/weights.json"))
        assert store.all_weights() == []

    def test_save_creates_parent_directories(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        store.set("test_model", TaskRole.PLANNER, 0.5)

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "subsub" / "weights.json"
            store.save(path)
            assert path.exists()
            loaded = ModelWeightStore.load(path)
            assert len(loaded.all_weights()) == 1

    def test_model_dump_mode_json_roundtrip(self):
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        datetime(2026, 7, 3, tzinfo=UTC)
        store.set("claude_opus_48", TaskRole.PLANNER, 0.85, source="benchmark")

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "weights.json"
            store.save(path)

            raw = json.loads(path.read_text())
            assert isinstance(raw, list)
            assert len(raw) == 1
            assert raw[0]["model_id"] == "claude_opus_48"
            assert raw[0]["task_role"] == "planner"
            assert raw[0]["weight"] == 0.85
            assert raw[0]["source"] == "benchmark"
            assert "updated_at" in raw[0]


class TestLoader:
    def test_load_seed_data(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        all_w = store.all_weights()
        assert len(all_w) > 0, "seed data must have at least one entry"

        # Verify each TaskRole has at least one entry
        roles_seen = {e.task_role for e in all_w}
        assert TaskRole.PLANNER in roles_seen
        assert TaskRole.EDITOR in roles_seen
        assert TaskRole.COMPACTOR in roles_seen
        assert TaskRole.ENUMERATOR in roles_seen

    def test_seed_data_weights_in_range(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        for entry in store.all_weights():
            assert 0.0 <= entry.weight <= 1.0, f"{entry.model_id} weight out of range"

    def test_apply_routing_weights(self):
        from general_ludd.model_weights.loader import apply_routing_weights
        from general_ludd.model_weights.store import ModelWeightStore

        store = ModelWeightStore()
        result = apply_routing_weights(store)
        assert isinstance(result, ModelWeightStore)

    def test_load_seed_data_source_is_benchmark(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        for entry in store.all_weights():
            assert entry.source == "benchmark", (
                f"seed entry {entry.model_id} source should be 'benchmark'"
            )

    def test_seed_data_planner_entries(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        planners = store.list_by_role(TaskRole.PLANNER)
        model_ids = {p.model_id for p in planners}
        assert "claude_opus_48" in model_ids
        assert "claude_sonnet_46" in model_ids

    def test_seed_data_editor_entries(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        editors = store.list_by_role(TaskRole.EDITOR)
        model_ids = {e.model_id for e in editors}
        assert "claude_haiku_45" in model_ids
        assert "gpt4o_mini" in model_ids

    def test_seed_data_compactor_entries(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        compactors = store.list_by_role(TaskRole.COMPACTOR)
        assert len(compactors) >= 1
        model_ids = {c.model_id for c in compactors}
        assert "gemini_compactor" in model_ids

    def test_seed_data_enumerator_entries(self):
        from general_ludd.model_weights.loader import load_seed_data

        store = load_seed_data()
        enumerators = store.list_by_role(TaskRole.ENUMERATOR)
        assert len(enumerators) >= 1
        model_ids = {e.model_id for e in enumerators}
        assert "gpt41_nano" in model_ids
        assert "qwen3_32b" in model_ids
