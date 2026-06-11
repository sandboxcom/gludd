from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from general_ludd.models.router import ModelRouter


class ModelRoutingConfig(BaseModel):
    default_profile: str | None = None
    weak_model_profile: str | None = None
    role_routing: dict[str, str] = {}
    quality_routing: dict[str, str] = {}
    latency_routing: dict[str, str] = {}
    pattern_routing: dict[str, str] = {}
    fallback_chain: list[str] = []


def load_model_routing(path: str | Path) -> ModelRoutingConfig:
    p = Path(path)
    if not p.exists():
        return ModelRoutingConfig()
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return ModelRoutingConfig(**data)


def build_router_from_config(config: ModelRoutingConfig) -> ModelRouter:
    router = ModelRouter(
        role_mapping=dict(config.role_routing),
        default_profile_id=config.default_profile,
        weak_model_profile_id=config.weak_model_profile,
    )
    for class_name, profile_id in config.quality_routing.items():
        router.add_quality_mapping(class_name, profile_id)
    for class_name, profile_id in config.latency_routing.items():
        router.add_latency_mapping(class_name, profile_id)
    for pattern_name, role_name in config.pattern_routing.items():
        router.add_pattern_mapping(pattern_name, role_name)
    return router
