"""Contracts and endpoint construction for owned model-worker dispatch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
from general_ludd.models.provider_registry import ProviderRegistry

_MAX_TEXT = 2_048


def _bounded_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} contains a control delimiter")
    return value


@runtime_checkable
class _EndpointCaller(Protocol):
    """One endpoint-specific gateway owned by a published generation."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse: ...

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]: ...

    def close(self) -> None: ...


EndpointGatewayFactory = Callable[[str, str, str], _EndpointCaller]


@dataclass(frozen=True, slots=True)
class _StaticEndpointResolver:
    alias: str
    endpoint_url: str

    def resolve(self, alias_name: str) -> str | None:
        return self.endpoint_url if alias_name == self.alias else None


@dataclass(frozen=True, slots=True)
class _EndpointRoute:
    profile_id: str
    caller: _EndpointCaller


@dataclass(slots=True)
class _Generation:
    deployment_id: str
    routes: tuple[_EndpointRoute, ...]
    accepting: bool = True
    in_flight: int = 0
    next_route: int = 0


class ModelWorkerDispatchError(RuntimeError):
    """Censored publication or drain failure with one stable phase."""

    def __init__(self, phase: str) -> None:
        """Retain one bounded phase while excluding endpoint/provider detail."""
        self.phase = _bounded_text(phase, "phase")
        super().__init__(f"model worker dispatch failed during {self.phase}")


def _default_endpoint_gateway(
    endpoint_url: str,
    endpoint_profile_id: str,
    model_name: str,
) -> ModelGateway:
    alias = f"owned_model_endpoint_{hashlib.sha256(endpoint_profile_id.encode()).hexdigest()}"
    profile = ModelProfile(
        model_profile_id=endpoint_profile_id,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=model_name,
        api_base_alias=alias,
        api_metered=False,
        enabled=True,
    )
    return ModelGateway(
        [profile],
        provider_registry=ProviderRegistry.from_profiles([profile]),
        secrets_manager=_StaticEndpointResolver(alias, endpoint_url),
    )


__all__ = (
    "EndpointGatewayFactory",
    "ModelWorkerDispatchError",
)
