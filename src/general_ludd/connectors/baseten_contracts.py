"""Typed transport and response contracts for the Baseten connector."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypedDict

HttpRequest = Callable[
    [str, str, Mapping[str, str], "bytes | None"],
    "tuple[int, dict[str, object]]",
]


class BasetenDeployment(TypedDict, total=False):
    """One deployment returned by the Baseten management API."""

    id: str
    model_id: str
    name: str
    status: str
    environment: str
    created_at: str


class BasetenModel(TypedDict, total=False):
    """Subset of the model-list item shape consumed by Gludd."""

    id: str
    name: str
    deployments: list[BasetenDeployment]


class BasetenModelsResponse(TypedDict, total=False):
    """Subset of the model-list top-level response consumed by Gludd."""

    id: str
    items: list[BasetenModel]


class BasetenHealthResult(TypedDict):
    """Result returned by the connector's non-raising health probe."""

    ok: bool
    reachable: bool
    api_key_valid: bool
    detail: str
    source: str


class BasetenConfig(TypedDict, total=False):
    """Environment-pointer and endpoint configuration accepted by the client."""

    name: str
    api_key_env: str
    base_url: str
    management_url: str


ConfigValue = str | int | float | bool | None
HeterogeneousConfig = Mapping[str, ConfigValue]


def normalize_baseten_deployment(
    deployment: Mapping[str, object],
    model_id: object,
    model_name: object,
) -> BasetenDeployment:
    """Copy only typed deployment fields from an untrusted provider item."""
    result: BasetenDeployment = {}
    deployment_id = deployment.get("id")
    if isinstance(deployment_id, str):
        result["id"] = deployment_id
    if isinstance(model_id, str):
        result["model_id"] = model_id
    if isinstance(model_name, str):
        result["name"] = model_name
    status = deployment.get("status")
    if isinstance(status, str):
        result["status"] = status
    environment = deployment.get("environment")
    if isinstance(environment, str):
        result["environment"] = environment
    created_at = deployment.get("created_at")
    if isinstance(created_at, str):
        result["created_at"] = created_at
    return result


def normalize_baseten_deployments(payload: object) -> list[BasetenDeployment]:
    """Flatten a bounded model-list response into canonical deployments."""
    items = payload.get("items") if isinstance(payload, Mapping) else payload
    if not isinstance(items, list):
        return []
    normalized: list[BasetenDeployment] = []
    for model in items:
        if not isinstance(model, Mapping):
            continue
        deployments = model.get("deployments")
        if not isinstance(deployments, list):
            continue
        normalized.extend(
            normalize_baseten_deployment(
                deployment,
                model.get("id"),
                model.get("name"),
            )
            for deployment in deployments
            if isinstance(deployment, Mapping)
        )
    return normalized


__all__ = [
    "BasetenConfig",
    "BasetenDeployment",
    "BasetenHealthResult",
    "BasetenModel",
    "BasetenModelsResponse",
    "ConfigValue",
    "HeterogeneousConfig",
    "HttpRequest",
    "normalize_baseten_deployment",
    "normalize_baseten_deployments",
]
