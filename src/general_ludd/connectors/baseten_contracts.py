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


__all__ = [
    "BasetenConfig",
    "BasetenDeployment",
    "BasetenHealthResult",
    "BasetenModel",
    "BasetenModelsResponse",
    "ConfigValue",
    "HeterogeneousConfig",
    "HttpRequest",
]
