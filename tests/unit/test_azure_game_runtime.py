"""Lifecycle tests for the shared Azure game E2E runtime."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import general_ludd.cloud.azure_game_runtime as azure_game_runtime
from general_ludd.cloud.azure_game_runtime import AzureGameRuntime
from general_ludd.cloud.deploy_strategy import build_azure_gateway
from general_ludd.events import EventBus
from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider, GPUType


class FakeDeploymentManager:
    def __init__(self, instance: ComputeInstance) -> None:
        self.instance = instance
        self.deploy_calls = 0
        self.configs: list[ComputeConfig] = []
        self.destroy_calls: list[str] = []
        self.destroy_error: BaseException | None = None

    async def deploy(self, config: ComputeConfig) -> ComputeInstance:
        self.deploy_calls += 1
        self.configs.append(config)
        return self.instance

    async def destroy(self, instance_id: str) -> None:
        self.destroy_calls.append(instance_id)
        if self.destroy_error is not None:
            raise self.destroy_error


def _instance() -> ComputeInstance:
    return ComputeInstance(
        instance_id="azure-game-1",
        provider=ComputeProvider.AZURE,
        status="running",
        gpu_type=GPUType.A100_80,
        endpoint_url="https://games.example.test",
    )


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "AZURE_PROVISION_E2E": "1",
        "AZURE_MODEL": "Qwen/Qwen2.5-0.5B-Instruct",
        "AZURE_GPU_TYPE": "a100_80",
        "AZURE_PROVISION_ENGINE": "vllm",
        "AZURE_DEPLOY_TYPE": "containerapp",
        "AZURE_REGION": "eastus",
        "AZURE_ALLOWED_CIDR": "198.51.100.10/32",
        "GLUDD_E2E_MAX_SPEND_USD": "5",
    }
    values.update(overrides)
    return values


def test_owned_runtime_deploys_once_reuses_gateway_and_destroys_once() -> None:
    manager = FakeDeploymentManager(_instance())
    gateway = object()
    endpoints: list[str] = []
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: endpoints.append(endpoint) or gateway,
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    assert runtime.start() is gateway
    assert runtime.start() is gateway
    runtime.close()
    runtime.close()

    assert manager.deploy_calls == 1
    assert endpoints == ["https://games.example.test"]
    assert manager.destroy_calls == ["azure-game-1"]
    assert runtime.owns_endpoint is False


def test_runtime_cleans_up_owned_endpoint_when_readiness_fails() -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(AZURE_GAME_READY_ATTEMPTS="2"),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: False,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="not ready"):
        runtime.start()

    assert manager.destroy_calls == ["azure-game-1"]
    runtime.close()
    assert manager.destroy_calls == ["azure-game-1"]


def test_runtime_cleans_up_owned_endpoint_when_gateway_creation_fails() -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: None,
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="Azure gateway"):
        runtime.start()

    assert manager.destroy_calls == ["azure-game-1"]


def test_owned_runtime_retries_readiness_and_uses_instance_ip_endpoint() -> None:
    instance = _instance().model_copy(
        update={"endpoint_url": "", "ip_address": "203.0.113.9", "port": 8123}
    )
    manager = FakeDeploymentManager(instance)
    probe_results = iter((False, True))
    probed: list[str] = []
    slept: list[float] = []
    runtime = AzureGameRuntime(
        environment=_environment(
            ARM_CLIENT_ID="client",
            AZURE_DISK_SIZE_GB="120",
            AZURE_GAME_READY_ATTEMPTS="3",
            AZURE_GAME_READY_INTERVAL_SECS="0.25",
            AZURE_TIMEOUT_MINUTES="45",
        ),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: {"endpoint": endpoint},
        readiness_probe=lambda endpoint: probed.append(endpoint) or next(probe_results),
        sleep=slept.append,
    )

    assert runtime.start() == {"endpoint": "http://203.0.113.9:8123"}
    assert runtime.endpoint_url == "http://203.0.113.9:8123"
    assert probed == ["http://203.0.113.9:8123", "http://203.0.113.9:8123"]
    assert slept == [0.25]
    assert manager.configs[0].disk_size_gb == 120
    assert manager.configs[0].timeout_minutes == 45.0
    assert manager.configs[0].provider_auth_aliases == {"ARM_CLIENT_ID": "ARM_CLIENT_ID"}
    runtime.close()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"AZURE_GPU_TYPE": "imaginary"}, "Unsupported AZURE_GPU_TYPE"),
        ({"AZURE_PROVISION_ENGINE": "imaginary"}, "Unsupported AZURE_PROVISION_ENGINE"),
        ({"GLUDD_E2E_MAX_SPEND_USD": "many"}, "must be numeric"),
        ({"AZURE_DISK_SIZE_GB": "huge"}, "must be an integer"),
    ],
)
def test_runtime_rejects_invalid_compute_configuration(
    override: dict[str, str],
    message: str,
) -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(**override),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match=message):
        runtime.start()

    assert manager.deploy_calls == 0


def test_closed_runtime_never_returns_a_gateway_for_a_destroyed_endpoint() -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )
    runtime.start()
    runtime.close()

    with pytest.raises(RuntimeError, match="already closed"):
        runtime.start()


def test_destroy_failure_is_streamed_and_never_retried() -> None:
    manager = FakeDeploymentManager(_instance())
    manager.destroy_error = RuntimeError("destroy failed")
    events: list[str] = []
    bus = EventBus()
    bus.subscribe("custom", lambda event: events.append(str(event.payload["name"])))
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        event_bus=bus,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
        event_reporter=None,
    )
    runtime.start()

    with pytest.raises(RuntimeError, match="destroy failed"):
        runtime.close()
    runtime.close()

    assert manager.destroy_calls == ["azure-game-1"]
    assert "azure_game_destroy_failed" in events


def test_default_readiness_probe_normalizes_endpoint_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def fake_get(url: str, *, timeout: float) -> SimpleNamespace:
        requested.append(url)
        return SimpleNamespace(status_code=200 if len(requested) < 3 else 503)

    monkeypatch.setattr(azure_game_runtime.httpx, "get", fake_get)

    assert azure_game_runtime._default_readiness_probe("https://games.test") is True
    assert azure_game_runtime._default_readiness_probe("https://games.test/v1/") is True
    assert azure_game_runtime._default_readiness_probe("https://games.test") is False
    assert requested == [
        "https://games.test/v1/models",
        "https://games.test/v1/models",
        "https://games.test/v1/models",
    ]

    def fail_get(url: str, *, timeout: float) -> SimpleNamespace:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(azure_game_runtime.httpx, "get", fail_get)
    assert azure_game_runtime._default_readiness_probe("https://games.test") is False


def test_runtime_discovers_global_runner_cidr_before_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ip": "8.8.8.8"})
    monkeypatch.setattr(azure_game_runtime.httpx, "get", lambda *args, **kwargs: response)
    manager = FakeDeploymentManager(_instance())
    environment = _environment()
    environment.pop("AZURE_ALLOWED_CIDR")
    runtime = AzureGameRuntime(
        environment=environment,
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    runtime.start()
    runtime.close()

    assert manager.configs[0].allowed_cidr == "8.8.8.8/32"


def test_runtime_rejects_non_global_discovered_cidr_before_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ip": "192.0.2.1"})
    monkeypatch.setattr(azure_game_runtime.httpx, "get", lambda *args, **kwargs: response)
    manager = FakeDeploymentManager(_instance())
    environment = _environment()
    environment.pop("AZURE_ALLOWED_CIDR")
    runtime = AzureGameRuntime(
        environment=environment,
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="non-global IPv4"):
        runtime.start()

    assert manager.deploy_calls == 0


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"AZURE_GAME_READY_ATTEMPTS": "0"}, "at least 1"),
        ({"AZURE_GAME_READY_INTERVAL_SECS": "-1"}, "non-negative"),
    ],
)
def test_runtime_rejects_invalid_readiness_bounds_and_cleans_up(
    override: dict[str, str],
    message: str,
) -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(**override),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match=message):
        runtime.start()

    assert manager.destroy_calls == ["azure-game-1"]


def test_runtime_cleans_up_deployment_without_an_endpoint() -> None:
    instance = _instance().model_copy(update={"endpoint_url": "", "ip_address": "", "port": None})
    manager = FakeDeploymentManager(instance)
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="without an inference endpoint"):
        runtime.start()

    assert manager.destroy_calls == ["azure-game-1"]


def test_runtime_context_manager_closes_owned_endpoint() -> None:
    manager = FakeDeploymentManager(_instance())
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with runtime as gateway:
        assert gateway is not None

    assert manager.destroy_calls == ["azure-game-1"]


def test_external_endpoint_is_reused_and_never_destroyed() -> None:
    manager = FakeDeploymentManager(_instance())
    gateway = object()
    runtime = AzureGameRuntime(
        environment=_environment(
            AZURE_BASE_URL="https://external.example.test/v1",
            AZURE_PROVISION_E2E="0",
        ),
        deployment_manager=manager,
        gateway_factory=lambda endpoint: gateway,
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    assert runtime.start() is gateway
    assert runtime.start() is gateway
    runtime.close()

    assert manager.deploy_calls == 0
    assert manager.destroy_calls == []


def test_runtime_never_falls_back_to_a_hosted_provider() -> None:
    manager = FakeDeploymentManager(_instance())
    factory_calls: list[str] = []
    runtime = AzureGameRuntime(
        environment={},
        deployment_manager=manager,
        gateway_factory=lambda endpoint: factory_calls.append(endpoint),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="AZURE_BASE_URL or AZURE_PROVISION_E2E=1"):
        runtime.start()

    assert factory_calls == []
    assert manager.deploy_calls == 0


def test_runtime_streams_deploy_readiness_and_cleanup_events() -> None:
    manager = FakeDeploymentManager(_instance())
    events: list[str] = []
    event_bus = EventBus()
    event_bus.subscribe("custom", lambda event: events.append(str(event.payload["name"])))
    runtime = AzureGameRuntime(
        environment=_environment(),
        deployment_manager=manager,
        event_bus=event_bus,
        gateway_factory=lambda endpoint: object(),
        readiness_probe=lambda endpoint: True,
        sleep=lambda seconds: None,
        event_reporter=lambda event: None,
    )

    runtime.start()
    runtime.close()

    assert events == [
        "azure_game_deploy_started",
        "azure_game_deploy_completed",
        "azure_game_readiness_probe",
        "azure_game_endpoint_ready",
        "azure_game_destroy_started",
        "azure_game_destroy_completed",
    ]


def test_gateway_factory_uses_explicit_azure_endpoint_without_hosted_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    monkeypatch.setenv("AZURE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    monkeypatch.setenv("AZURE_API_KEY", "azure-test-key")

    gateway = build_azure_gateway("https://games.example.test")

    assert gateway is not None
    assert gateway.get_profile("default") is not None
    assert gateway.get_profile("azure_self_improve") is not None
    assert gateway.get_profile("default").provider == "openai"
    assert gateway._secrets.resolve("AZURE_BASE_URL") == "https://games.example.test/v1"
    assert gateway._secrets.resolve("AZURE_API_KEY") == "azure-test-key"


def test_gateway_factory_requires_an_azure_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    assert build_azure_gateway() is None
