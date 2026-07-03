"""ModelWeightStore — in-memory store for model routing weights with JSON persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from general_ludd.model_weights.schema import ModelWeightSchema
from general_ludd.schemas.benchmark import TaskRole

_VALID_SOURCES = {"benchmark", "operator", "manual"}


class ModelWeightStore:
    _KEY_SEP: ClassVar[str] = "::"

    def __init__(self) -> None:
        self._data: dict[tuple[str, TaskRole], ModelWeightSchema] = {}

    @staticmethod
    def _make_key(model_id: str, task_role: TaskRole) -> tuple[str, TaskRole]:
        return (model_id, task_role)

    def get(
        self, model_id: str, task_role: TaskRole
    ) -> ModelWeightSchema | None:
        return self._data.get(self._make_key(model_id, task_role))

    def set(
        self,
        model_id: str,
        task_role: TaskRole,
        weight: float,
        source: str = "manual",
    ) -> ModelWeightSchema:
        if source not in _VALID_SOURCES:
            raise ValueError(
                f"source must be one of {_VALID_SOURCES!r}, got {source!r}"
            )
        entry = ModelWeightSchema(
            model_id=model_id,
            task_role=task_role,
            weight=weight,
            updated_at=datetime.now(UTC),
            source=source,
        )
        self._data[self._make_key(model_id, task_role)] = entry
        return entry

    def list_by_role(self, task_role: TaskRole) -> list[ModelWeightSchema]:
        results = [
            entry
            for (_, role), entry in self._data.items()
            if role == task_role
        ]
        results.sort(key=lambda e: e.weight, reverse=True)
        return results

    def all_weights(self) -> list[ModelWeightSchema]:
        return list(self._data.values())

    def save(self, filepath: str | Path) -> None:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = [
            entry.model_dump(mode="json") for entry in self._data.values()
        ]
        filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, filepath: str | Path) -> ModelWeightStore:
        filepath = Path(filepath)
        store = cls()
        if not filepath.exists():
            return store
        raw = json.loads(filepath.read_text(encoding="utf-8"))
        for item in raw:
            entry = ModelWeightSchema(**item)
            store._data[store._make_key(entry.model_id, entry.task_role)] = entry
        return store
