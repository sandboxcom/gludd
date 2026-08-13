"""Session lifecycle for the live Azure FPS game E2E suite.

The runtime has two deliberately distinct modes: it may borrow an operator-
supplied ``AZURE_BASE_URL``, or it may own one endpoint provisioned through
``DeploymentManager``.  Only owned endpoints are destroyed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, TypeVar, runtime_checkable

import httpx

from general_ludd.cloud.deploy_strategy import DEFAULT_AZURE_MODEL, build_azure_gateway
from general_ludd.events import CustomEvent, Event, EventBus
from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeInstance,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager

_ARM_ENV_VARS = (
    "ARM_CLIENT_ID",
    "ARM_CLIENT_SECRET",
    "ARM_TENANT_ID",
    "ARM_SUBSCRIPTION_ID",
    "ARM_USE_MSI",
)
_T = TypeVar("_T")


@runtime_checkable
class DeploymentController(Protocol):
    def deploy(self, config: ComputeConfig) -> Awaitable[ComputeInstance]: ...

    def destroy(self, instance_id: str) -> Awaitable[None]: ...


def _run_async(operation: Awaitable[_T]) -> _T:
    """Run one deployment coroutine without leaking a closed current loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(operation)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _openai_models_url(endpoint: str) -> str:
    base_url = endpoint.rstrip("/")
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    return f"{base_url}/v1/models"


def _default_readiness_probe(endpoint: str) -> bool:
    try:
        response = httpx.get(_openai_models_url(endpoint), timeout=10.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _print_event(event: Event) -> None:
    name = str(event.payload.get("name", "azure_game_event"))
    detail = event.payload.get("message")
    if detail is None and "attempt" in event.payload:
        detail = f"attempt={event.payload['attempt']}"
    suffix = f" {detail}" if detail not in (None, "") else ""
    print(f"[azure-game-event] {name}{suffix}", flush=True)


class AzureGameRuntime:
    """Own and reuse exactly one Azure inference gateway for an E2E session."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        deployment_manager: DeploymentController | None = None,
        event_bus: EventBus | None = None,
        gateway_factory: Callable[[str], object | None] | None = None,
        readiness_probe: Callable[[str], bool] = _default_readiness_probe,
        sleep: Callable[[float], None] = time.sleep,
        event_reporter: Callable[[Event], object] | None = _print_event,
        preflight: Callable[[], object] | None = None,
    ) -> None:
        self._environment = dict(os.environ if environment is None else environment)
        self._event_bus = event_bus or EventBus()
        self._gateway_factory = gateway_factory
        self._readiness_probe = readiness_probe
        self._sleep = sleep
        self._preflight = preflight
        self._preflight_completed = False
        self._reporter_subscription: str | None = None
        if event_reporter is not None:
            self._reporter_subscription = self._event_bus.subscribe("custom", event_reporter)
        self._deployment_manager = deployment_manager or self._default_deployment_manager()
        self._gateway: object | None = None
        self._instance: ComputeInstance | None = None
        self._owns_endpoint = False
        self._closed = False
        self._destroy_attempted = False
        self._endpoint_url = ""

    @property
    def owns_endpoint(self) -> bool:
        return self._owns_endpoint

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    def _value(self, name: str, default: str = "") -> str:
        return (self._environment.get(name) or default).strip()

    def _default_deployment_manager(self) -> DeploymentManager:
        secrets = EnvSecretsManager()
        secrets.allow_env(*_ARM_ENV_VARS, "AZURE_SUBSCRIPTION_ID")
        return DeploymentManager(secrets_resolver=secrets, event_bus=self._event_bus)

    def _publish(self, name: str, **payload: object) -> None:
        self._event_bus.publish(
            CustomEvent(
                name=name,
                payload=dict(payload),
                source="azure_game_runtime",
            )
        )

    def _float_value(self, name: str, default: str) -> float:
        raw = self._value(name, default)
        try:
            return float(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be numeric, got {raw!r}") from error

    def _int_value(self, name: str, default: str) -> int:
        raw = self._value(name, default)
        try:
            return int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be an integer, got {raw!r}") from error

    def _resolve_allowed_cidr(self) -> str:
        explicit = self._value("AZURE_ALLOWED_CIDR")
        if explicit:
            return explicit
        try:
            response = httpx.get(
                "https://api4.ipify.org",
                params={"format": "json"},
                timeout=10.0,
            )
            response.raise_for_status()
            address = ipaddress.ip_address(str(response.json()["ip"]))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                "Unable to discover the E2E runner public IPv4 before Azure spend; "
                "set AZURE_ALLOWED_CIDR explicitly"
            ) from error
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_global:
            raise RuntimeError(
                "Public-IP discovery returned a non-global IPv4; set AZURE_ALLOWED_CIDR explicitly"
            )
        return f"{address}/32"

    def _compute_config(self) -> ComputeConfig:
        gpu_name = self._value("AZURE_GPU_TYPE", "a100_80").lower().replace("-", "_")
        engine_name = self._value("AZURE_PROVISION_ENGINE", "vllm").lower()
        try:
            gpu_type = GPUType(gpu_name)
        except ValueError as error:
            raise RuntimeError(f"Unsupported AZURE_GPU_TYPE={gpu_name!r}") from error
        try:
            engine = InferenceEngine(engine_name)
        except ValueError as error:
            raise RuntimeError(f"Unsupported AZURE_PROVISION_ENGINE={engine_name!r}") from error
        auth_aliases = {name: name for name in _ARM_ENV_VARS if self._value(name)}
        return ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=gpu_type,
            engine=engine,
            model_name=self._value("AZURE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct"),
            region=self._value("AZURE_REGION", "eastus"),
            deploy_type=self._value("AZURE_DEPLOY_TYPE", "containerapp"),
            max_cost_usd=self._float_value("GLUDD_E2E_MAX_SPEND_USD", "5"),
            timeout_minutes=self._float_value("AZURE_TIMEOUT_MINUTES", "30"),
            disk_size_gb=self._int_value("AZURE_DISK_SIZE_GB", "100"),
            allowed_cidr=self._resolve_allowed_cidr(),
            provider_auth_aliases=auth_aliases or None,
        )

    def _wait_until_ready(self, endpoint: str) -> None:
        attempts = self._int_value("AZURE_GAME_READY_ATTEMPTS", "60")
        interval = self._float_value("AZURE_GAME_READY_INTERVAL_SECS", "10")
        if attempts < 1:
            raise RuntimeError("AZURE_GAME_READY_ATTEMPTS must be at least 1")
        if interval < 0:
            raise RuntimeError("AZURE_GAME_READY_INTERVAL_SECS must be non-negative")
        for attempt in range(1, attempts + 1):
            self._publish("azure_game_readiness_probe", attempt=attempt)
            if self._readiness_probe(endpoint):
                self._publish("azure_game_endpoint_ready", attempt=attempt)
                return
            if attempt < attempts:
                self._sleep(interval)
        raise RuntimeError(f"Azure game endpoint was not ready after {attempts} attempts")

    def _gateway_for(self, endpoint: str, *, model_name: str) -> object:
        gateway: object | None
        if self._gateway_factory is None:
            gateway = build_azure_gateway(endpoint, model_name=model_name)
        else:
            gateway = self._gateway_factory(endpoint)
        if gateway is None:
            raise RuntimeError("Azure gateway could not be constructed for the selected endpoint")
        return gateway

    def _run_preflight(self) -> None:
        if self._preflight is None or self._preflight_completed:
            return
        self._publish("azure_game_preflight_started")
        try:
            self._preflight()
        except BaseException as error:
            self._publish("azure_game_preflight_failed", error=str(error))
            raise
        self._preflight_completed = True
        self._publish("azure_game_preflight_completed")

    def start(self) -> object:
        """Return the session gateway, provisioning only on the first call."""
        if self._closed:
            raise RuntimeError("Azure game runtime is already closed")
        if self._gateway is not None:
            return self._gateway

        self._run_preflight()

        external_endpoint = self._value("AZURE_BASE_URL")
        if external_endpoint:
            self._endpoint_url = external_endpoint
            self._publish("azure_game_external_endpoint_selected")
            self._wait_until_ready(external_endpoint)
            model_name = self._value("AZURE_MODEL", DEFAULT_AZURE_MODEL)
            self._gateway = self._gateway_for(external_endpoint, model_name=model_name)
            return self._gateway

        if self._value("AZURE_PROVISION_E2E") != "1":
            raise RuntimeError(
                "Azure game E2E requires AZURE_BASE_URL or AZURE_PROVISION_E2E=1; "
                "hosted-provider fallback is forbidden"
            )

        config = self._compute_config()
        self._publish(
            "azure_game_deploy_started",
            gpu_type=config.gpu_type.value,
            model=config.model_name,
            region=config.region or "",
        )
        try:
            instance = _run_async(self._deployment_manager.deploy(config))
            self._instance = instance
            self._owns_endpoint = True
            endpoint = instance.endpoint_url
            if not endpoint and instance.ip_address:
                endpoint = f"http://{instance.ip_address}:{instance.port}"
            if not endpoint:
                raise RuntimeError("Azure deployment completed without an inference endpoint")
            self._endpoint_url = endpoint
            self._publish(
                "azure_game_deploy_completed",
                instance_id=instance.instance_id,
                endpoint=endpoint,
            )
            self._wait_until_ready(endpoint)
            self._gateway = self._gateway_for(endpoint, model_name=config.model_name)
            return self._gateway
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Destroy the owned endpoint at most once; borrowed endpoints are untouched."""
        if self._closed:
            return
        self._closed = True
        instance = self._instance
        if self._owns_endpoint and instance is not None and not self._destroy_attempted:
            self._destroy_attempted = True
            self._publish("azure_game_destroy_started", instance_id=instance.instance_id)
            try:
                _run_async(self._deployment_manager.destroy(instance.instance_id))
            except BaseException as error:
                self._publish(
                    "azure_game_destroy_failed",
                    instance_id=instance.instance_id,
                    error=str(error),
                )
                raise
            else:
                self._publish("azure_game_destroy_completed", instance_id=instance.instance_id)
            finally:
                self._owns_endpoint = False
        if self._reporter_subscription is not None:
            self._event_bus.unsubscribe(self._reporter_subscription)
            self._reporter_subscription = None

    def __enter__(self) -> object:
        return self.start()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


__all__ = ["AzureGameRuntime", "DeploymentController"]
