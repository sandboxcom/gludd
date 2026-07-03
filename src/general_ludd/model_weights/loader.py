"""Loader — seed data and routing-weight application for the model_weights package."""

from __future__ import annotations

from importlib import resources

from general_ludd.model_weights.store import ModelWeightStore


def load_seed_data() -> ModelWeightStore:
    """Load cold-start model weights from the bundled seed_data.json.

    Falls back to an empty store if the seed file is missing or malformed.
    """
    try:
        json_text = (
            resources.files("general_ludd.model_weights")
            .joinpath("seed_data.json")
            .read_text(encoding="utf-8")
        )
        import json

        items = json.loads(json_text)
        store = ModelWeightStore()
        for item in items:
            store.set(
                model_id=item["model_id"],
                task_role=item["task_role"],
                weight=item["weight"],
                source=item.get("source", "benchmark"),
            )
        return store
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return ModelWeightStore()


def apply_routing_weights(store: ModelWeightStore) -> ModelWeightStore:
    """Apply the loaded weights to the routing system.

    Currently returns the store as-is. In the future this will feed
    weights into AdaptiveRouter as a cold-start prior when
    insufficient empirical data exists.
    """
    return store
