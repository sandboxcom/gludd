"""Structural tests for model_weights/store.py — edge cases and error paths."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from general_ludd.model_weights.store import ModelWeightStore
from general_ludd.schemas.benchmark import TaskRole


class TestModelWeightStoreErrors:
    def test_set_invalid_source_raises_valueerror(self):
        store = ModelWeightStore()
        with pytest.raises(ValueError, match="source"):
            store.set("model", TaskRole.PLANNER, 0.5, source="invalid")

    def test_set_valid_sources(self):
        store = ModelWeightStore()
        for src in ("benchmark", "operator", "manual"):
            entry = store.set("model", TaskRole.PLANNER, 0.5, source=src)
            assert entry.source == src

    def test_get_returns_none(self):
        store = ModelWeightStore()
        assert store.get("none", TaskRole.PLANNER) is None

    def test_save_corrupt_json_raises(self):
        store = ModelWeightStore()
        store.set("model", TaskRole.PLANNER, 0.5)
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "weights.json"
            path.write_text("{corrupt")
            with pytest.raises(Exception):
                ModelWeightStore.load(path)

    def test_load_non_json_file_raises(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "weights.json"
            path.write_text("not json at all")
            with pytest.raises(Exception):
                ModelWeightStore.load(path)


class TestModelWeightStoreAllRoles:
    def test_each_role_independent(self):
        store = ModelWeightStore()
        store.set("m1", TaskRole.PLANNER, 0.5)
        store.set("m1", TaskRole.EDITOR, 0.7)
        store.set("m1", TaskRole.COMPACTOR, 0.9)
        store.set("m1", TaskRole.ENUMERATOR, 0.3)
        store.set("m1", TaskRole.CODER, 0.6)
        store.set("m1", TaskRole.REVIEWER, 0.8)
        assert len(store.all_weights()) == 6
        assert store.get("m1", TaskRole.PLANNER).weight == 0.5
        assert store.get("m1", TaskRole.EDITOR).weight == 0.7

    def test_sort_stable_with_equal_weights(self):
        store = ModelWeightStore()
        store.set("b", TaskRole.PLANNER, 0.5)
        store.set("a", TaskRole.PLANNER, 0.5)
        store.set("c", TaskRole.PLANNER, 0.5)
        results = store.list_by_role(TaskRole.PLANNER)
        assert len(results) == 3
        for r in results:
            assert r.weight == 0.5
