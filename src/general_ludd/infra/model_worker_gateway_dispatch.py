"""Publish attested model-worker generations through one universal gateway."""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerEndpoint,
    ProvisionedModelWorkerPool,
)
from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
from general_ludd.models.provider_registry import ProviderRegistry

_MAX_TEXT = 2_048
_MAX_RETIRED_LEASES = 1_024
_OWNED_PROFILE_FIELDS = frozenset(
    {
        "api_base_alias",
        "api_metered",
        "cost_per_input_token",
        "cost_per_output_token",
        "credential_alias",
        "enabled",
        "model_name",
        "model_profile_id",
        "provider",
        "provider_class_hint",
        "provider_package",
        "run_budget_usd",
    }
)


def _bounded_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if any(delimiter in value for delimiter in "\x00\r\n"):
        raise ValueError(f"{field_name} contains a control delimiter")
    return value


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


class ModelWorkerGatewayDispatcher:
    """Own dynamic generations behind one stable provider-neutral profile."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str,
        model_name: str,
        endpoint_gateway_factory: EndpointGatewayFactory | None = None,
        drain_timeout_seconds: float = 120.0,
        profile_options: Mapping[str, object] | None = None,
    ) -> None:
        """Bind one logical profile, endpoint factory, and drain deadline."""
        if not isinstance(gateway, ModelGateway):
            raise ValueError("gateway must be ModelGateway")
        self._profile_id = _bounded_text(profile_id, "profile_id")
        self._model_name = _bounded_text(model_name, "model_name")
        if (
            isinstance(drain_timeout_seconds, bool)
            or not isinstance(drain_timeout_seconds, (int, float))
            or not math.isfinite(float(drain_timeout_seconds))
            or not 0 < float(drain_timeout_seconds) <= 3_600
        ):
            raise ValueError("drain_timeout_seconds is outside 0..3600")
        options = dict(profile_options or {})
        if any(not isinstance(key, str) for key in options):
            raise ValueError("profile_options keys must be text")
        invalid = set(options).difference(ModelProfile.model_fields)
        invalid.update(set(options).intersection(_OWNED_PROFILE_FIELDS))
        if invalid:
            raise ValueError("profile_options contains an unsupported or owned field")
        self._profile = ModelProfile.model_validate(
            {
                "model_profile_id": self._profile_id,
                "provider": "openai",
                "provider_package": "langchain-openai",
                "provider_class_hint": "ChatOpenAI",
                "model_name": self._model_name,
                "api_metered": False,
                "enabled": True,
                **options,
            }
        )
        self._gateway = gateway
        self._factory = endpoint_gateway_factory or _default_endpoint_gateway
        if not callable(self._factory):
            raise ValueError("endpoint_gateway_factory must be callable")
        self._drain_timeout_seconds = float(drain_timeout_seconds)
        self._condition = threading.Condition(threading.RLock())
        self._generations: dict[str, _Generation] = {}
        self._generation_order: list[str] = []
        self._active_lease: str | None = None
        self._retired_leases: OrderedDict[str, None] = OrderedDict()

    @staticmethod
    def _validated_endpoints(
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> tuple[ModelWorkerEndpoint, ...]:
        if not isinstance(deployment, ProvisionedModelWorkerPool):
            raise ModelWorkerDispatchError("deployment_contract")
        if (
            not isinstance(endpoints, tuple)
            or not endpoints
            or any(not isinstance(endpoint, ModelWorkerEndpoint) for endpoint in endpoints)
        ):
            raise ModelWorkerDispatchError("endpoint_contract")
        hosts = {host.host_id: host for host in deployment.hosts}
        if len(endpoints) != len(hosts) or {item.host_id for item in endpoints} != set(hosts):
            raise ModelWorkerDispatchError("endpoint_contract")
        for endpoint in endpoints:
            host = hosts[endpoint.host_id]
            if endpoint.endpoint_url != host.endpoint_url:
                raise ModelWorkerDispatchError("endpoint_contract")
            try:
                parsed = urlsplit(endpoint.endpoint_url)
                port = parsed.port
            except ValueError:
                raise ModelWorkerDispatchError("endpoint_contract") from None
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.hostname is None
                or parsed.hostname.casefold() != host.address.strip("[]").casefold()
                or port is None
            ):
                raise ModelWorkerDispatchError("endpoint_contract")
        return endpoints

    @staticmethod
    def _close_routes(routes: tuple[_EndpointRoute, ...]) -> bool:
        clean = True
        for route in routes:
            try:
                route.caller.close()
            except Exception:
                clean = False
        return clean

    def _new_lease(self) -> str:
        while True:
            candidate = secrets.token_hex(32)
            if candidate not in self._generations and candidate not in self._retired_leases:
                return candidate

    def _build_routes(
        self,
        lease: str,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> tuple[_EndpointRoute, ...]:
        routes: list[_EndpointRoute] = []
        try:
            for index, endpoint in enumerate(endpoints):
                profile_id = f"local-owned-worker-{lease[:16]}-{index}"
                caller = self._factory(
                    endpoint.endpoint_url,
                    profile_id,
                    self._model_name,
                )
                if any(
                    not callable(getattr(caller, method, None))
                    for method in ("call_model", "call_model_stream", "close")
                ):
                    raise TypeError("endpoint caller contract")
                routes.append(_EndpointRoute(profile_id, caller))
        except Exception:
            self._close_routes(tuple(routes))
            raise ModelWorkerDispatchError("endpoint_runtime") from None
        return tuple(routes)

    def publish(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
    ) -> str:
        """Atomically make one fully attested generation the active route."""
        admitted = self._validated_endpoints(deployment, endpoints)
        with self._condition:
            if any(
                generation.deployment_id == deployment.deployment_id
                for generation in self._generations.values()
            ):
                raise ModelWorkerDispatchError("duplicate_deployment")
            lease = self._new_lease()
            routes = self._build_routes(lease, admitted)
            prior_active = self._active_lease
            generation = _Generation(deployment.deployment_id, routes)
            self._generations[lease] = generation
            self._generation_order.append(lease)
            self._active_lease = lease
            if len(self._generations) == 1:
                try:
                    self._gateway.register_runtime_profile(self._profile, self)
                except Exception:
                    self._generations.pop(lease, None)
                    self._generation_order.remove(lease)
                    self._active_lease = prior_active
                    self._close_routes(routes)
                    raise ModelWorkerDispatchError("publish") from None
            return lease

    def _fallback_lease(self) -> str | None:
        for lease in reversed(self._generation_order):
            generation = self._generations.get(lease)
            if generation is not None and generation.accepting:
                return lease
        return None

    def _remember_retired(self, lease: str) -> None:
        self._retired_leases[lease] = None
        self._retired_leases.move_to_end(lease)
        while len(self._retired_leases) > _MAX_RETIRED_LEASES:
            self._retired_leases.popitem(last=False)

    def withdraw(self, dispatch_lease: str) -> None:
        """Stop admission, drain calls, and remove one exact generation."""
        lease = _bounded_text(dispatch_lease, "dispatch_lease")
        routes: tuple[_EndpointRoute, ...]
        with self._condition:
            if lease in self._retired_leases:
                return
            generation = self._generations.get(lease)
            if generation is None:
                raise ModelWorkerDispatchError("lease")
            generation.accepting = False
            if self._active_lease == lease:
                self._active_lease = self._fallback_lease()
            deadline = time.monotonic() + self._drain_timeout_seconds
            while generation.in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelWorkerDispatchError("drain_timeout")
                self._condition.wait(timeout=remaining)
            if len(self._generations) == 1:
                try:
                    removed = self._gateway.remove_runtime_profile(
                        self._profile_id,
                        self,
                    )
                except Exception:
                    raise ModelWorkerDispatchError("profile_remove") from None
                if not removed:
                    raise ModelWorkerDispatchError("profile_ownership")
            self._generations.pop(lease)
            self._generation_order.remove(lease)
            self._remember_retired(lease)
            routes = generation.routes
        if not self._close_routes(routes):
            raise ModelWorkerDispatchError("endpoint_close")

    def _acquire_route(self, profile_id: str) -> tuple[str, _EndpointRoute]:
        if profile_id != self._profile_id:
            raise ModelWorkerDispatchError("profile")
        with self._condition:
            lease = self._active_lease
            generation = self._generations.get(lease or "")
            if generation is None or not generation.accepting:
                raise ModelWorkerDispatchError("unavailable")
            route = generation.routes[generation.next_route % len(generation.routes)]
            generation.next_route = (generation.next_route + 1) % len(generation.routes)
            generation.in_flight += 1
            return lease or "", route

    def _release_route(self, lease: str) -> None:
        with self._condition:
            generation = self._generations.get(lease)
            if generation is None or generation.in_flight <= 0:
                return
            generation.in_flight -= 1
            if generation.in_flight == 0:
                self._condition.notify_all()

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Route one buffered request while retaining its generation lease."""
        lease, route = self._acquire_route(profile_id)
        try:
            return route.caller.call_model(route.profile_id, messages, **kwargs)
        finally:
            self._release_route(lease)

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]:
        """Retain a generation until its upstream stream closes or exhausts."""
        lease, route = self._acquire_route(profile_id)
        iterator: Iterator[object] | None = None
        try:
            iterator = route.caller.call_model_stream(
                route.profile_id,
                messages,
                **kwargs,
            )
            yield from iterator
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
            self._release_route(lease)


__all__ = (
    "EndpointGatewayFactory",
    "ModelWorkerDispatchError",
    "ModelWorkerGatewayDispatcher",
)
