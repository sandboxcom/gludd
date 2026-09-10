#!/usr/bin/env python3
"""Select an immutable Azure model from task, policy, and validated evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelRegistry,
    ModelSearchResult,
)
from general_ludd.self_improve.azure_model_selection import (
    AzureModelSelectionPolicy,
    discover_and_select_azure_model,
    write_azure_model_selection,
)
from general_ludd.self_improve.candidate_classification import (
    CandidateTaskClassification,
    classify_candidate_task,
)
from general_ludd.self_improve.candidate_routing import (
    load_calibration_attempts_for_task,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_MAX_CONFIG_BYTES = 1_048_576
_POLICY_KEYS = frozenset(
    {
        "allowed_licenses",
        "allowed_publishers",
        "blocked_tags",
        "container_image",
        "kv_cache_mib",
        "max_hourly_cost_microusd",
        "minimum_context_tokens",
        "profile_capacities",
        "required_tags",
        "runtime_overhead_mib",
        "schema_version",
        "search_limit",
        "task_queries",
    }
)
_MODEL_KEYS = frozenset(
    {
        "context_tokens",
        "downloads",
        "library_name",
        "license_id",
        "model_id",
        "parameter_count",
        "pipeline_tag",
        "revision",
        "storage_bytes",
        "tags",
        "weight_bits",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--policy-file", required=True, type=Path)
    parser.add_argument("--evidence-file", required=True, type=Path)
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument("--registry-cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _load_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError(f"{label} exceeds its size bound")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"{label} must be a readable JSON object") from None
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{label} must be an array of strings")
    return tuple(value)


def _profile_capacities(value: object) -> tuple[AzureProfileCapacity, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("profile_capacities must be a non-empty array")
    capacities: list[AzureProfileCapacity] = []
    for raw in value:
        capacities.append(AzureProfileCapacity.from_payload(raw))
    return tuple(capacities)


def _load_policy(
    path: Path,
    classification: CandidateTaskClassification,
) -> AzureModelSelectionPolicy:
    payload = _load_object(path, "model selection policy")
    if set(payload) != _POLICY_KEYS or payload.get("schema_version") != 1:
        raise ValueError("model selection policy has an unsupported schema")
    queries = payload["task_queries"]
    if not isinstance(queries, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in queries.items()
    ):
        raise ValueError("model selection policy task_queries are invalid")
    try:
        query = queries[classification.task_kind]
    except KeyError:
        raise ValueError("model selection policy has no query for this task") from None
    return AzureModelSelectionPolicy(
        search_query=cast(str, query),
        search_limit=cast(int, payload["search_limit"]),
        allowed_publishers=_string_tuple(
            payload["allowed_publishers"], "allowed_publishers"
        ),
        allowed_licenses=_string_tuple(
            payload["allowed_licenses"], "allowed_licenses"
        ),
        required_tags=_string_tuple(payload["required_tags"], "required_tags"),
        blocked_tags=_string_tuple(payload["blocked_tags"], "blocked_tags"),
        minimum_context_tokens=cast(int, payload["minimum_context_tokens"]),
        container_image=cast(str, payload["container_image"]),
        profile_capacities=_profile_capacities(payload["profile_capacities"]),
        max_hourly_cost_microusd=cast(int, payload["max_hourly_cost_microusd"]),
        kv_cache_mib=cast(int, payload["kv_cache_mib"]),
        runtime_overhead_mib=cast(int, payload["runtime_overhead_mib"]),
    )


class CatalogModelRegistry:
    """Hermetic registry snapshot used only when explicitly requested."""

    def __init__(self, path: Path) -> None:
        payload = _load_object(path, "model catalog")
        if set(payload) != {"models", "schema_version"} or payload.get(
            "schema_version"
        ) != 1:
            raise ValueError("model catalog has an unsupported schema")
        raw_models = payload["models"]
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError("model catalog requires at least one model")
        models: dict[str, ModelDeploymentMetadata] = {}
        for raw in raw_models:
            if not isinstance(raw, Mapping) or set(raw) != _MODEL_KEYS:
                raise ValueError("model catalog entry has an unsupported schema")
            model = ModelDeploymentMetadata(
                model_id=cast(str, raw["model_id"]),
                revision=cast(str, raw["revision"]),
                parameter_count=cast(int, raw["parameter_count"]),
                context_tokens=cast(int, raw["context_tokens"]),
                storage_bytes=cast(int, raw["storage_bytes"]),
                weight_bits=cast(int, raw["weight_bits"]),
                license_id=cast(str, raw["license_id"]),
                tags=_string_tuple(raw["tags"], "model catalog tags"),
                pipeline_tag=cast(str, raw["pipeline_tag"]),
                library_name=cast(str, raw["library_name"]),
                downloads=cast(int, raw["downloads"]),
            )
            if model.model_id in models:
                raise ValueError("model catalog contains duplicate model IDs")
            models[model.model_id] = model
        self._models = models

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        sort: str = "downloads",
        limit: int = 20,
        author: str | None = None,
    ) -> list[ModelSearchResult]:
        """Apply the bounded live-registry query shape to snapshot metadata."""
        if sort != "downloads":
            raise ValueError("model catalog supports downloads sorting only")
        required = set(tags or ())
        needle = query.casefold()
        matches = tuple(
            model
            for model in self._models.values()
            if (not needle or needle in model.model_id.casefold())
            and required <= set(model.tags)
            and (author is None or model.model_id.split("/", 1)[0] == author)
        )
        ordered = sorted(matches, key=lambda model: (-model.downloads, model.model_id))
        return [
            ModelSearchResult(
                model_id=model.model_id,
                author=model.model_id.split("/", 1)[0],
                downloads=model.downloads,
                tags=list(model.tags),
                pipeline_tag=model.pipeline_tag,
                library_name=model.library_name,
            )
            for model in ordered[:limit]
        ]

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
        """Return exact immutable metadata for one snapshot entry."""
        try:
            return self._models[model_id]
        except KeyError:
            raise ValueError("model is absent from the catalog snapshot") from None


def _trace(event: Mapping[str, object]) -> None:
    print(json.dumps(dict(event), separators=(",", ":"), sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Classify work, select one model, and write no raw task or model name to logs."""
    args = _parser().parse_args(argv)
    classification = classify_candidate_task(
        args.task_file.read_text(encoding="utf-8")
    )
    _trace(classification.event_payload())
    policy = _load_policy(args.policy_file, classification)
    registry: CatalogModelRegistry | ModelRegistry
    if args.catalog_file is None:
        registry = ModelRegistry(cache_dir=str(args.registry_cache_dir))
    else:
        registry = CatalogModelRegistry(args.catalog_file)
    evidence = CapabilityEvidenceStore(str(args.evidence_file))
    selection = discover_and_select_azure_model(
        registry,
        classification,
        policy,
        attempts=load_calibration_attempts_for_task(evidence, classification),
        trace_sink=_trace,
    )
    write_azure_model_selection(args.output, selection)
    print("AZURE_MODEL_SELECTION_WRITTEN secret_output=false", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
