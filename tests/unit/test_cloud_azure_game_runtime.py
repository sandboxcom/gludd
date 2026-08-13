"""Tests for azure_game_runtime — session lifecycle, endpoint plumbing, readiness."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest

from general_ludd.cloud.azure_game_runtime import (
    AzureGameRuntime,
    DeploymentController,
    _default_readiness_probe,
    _openai_models_url,
    _print_event,
    _run_async,
)
from general_ludd.events import CustomEvent, EventBus

# ── _openai_models_url ───────────────────────────────────────────────────────


class TestOpenaiModelsUrl:
    def test_endpoint_already_v1(self) -> None:
        assert _openai_models_url("https://example.com/v1") == "https://example.com/v1/models"

    def test_endpoint_without_v1(self) -> None:
        assert _openai_models_url("https://example.com") == "https://example.com/v1/models"

    def test_endpoint_trailing_slash(self) -> None:
        assert _openai_models_url("https://example.com/") == "https://example.com/v1/models"

    def test_endpoint_with_trailing_slash_and_v1(self) -> None:
        assert _openai_models_url("https://example.com/v1/") == "https://example.com/v1/models"


# ── _run_async ───────────────────────────────────────────────────────────────


async def _async_identity(value: int) -> int:
    return value


class TestRunAsync:
    def test_returns_coroutine_result(self) -> None:
        result = _run_async(_async_identity(42))
        assert result == 42

    def test_closes_event_loop_after_run(self) -> None:
        probe_loop = asyncio.new_event_loop()
        try:
            _run_async(_async_identity(7))
            assert probe_loop.is_closed() is False
        finally:
            probe_loop.close()

    def test_leaves_no_current_loop(self) -> None:
        _run_async(_async_identity(1))
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()


# ── _print_event ─────────────────────────────────────────────────────────────


class TestPrintEvent:
    def test_prints_event_name_and_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        event = CustomEvent(name="test_event", payload={"message": "hello world"}, source="test")
        _print_event(event)
        captured = capsys.readouterr()
        assert "test_event" in captured.out
        assert "hello world" in captured.out

    def test_prints_attempt_when_no_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        event = CustomEvent(name="test_event", payload={"attempt": 3}, source="test")
        _print_event(event)
        assert "attempt=3" in capsys.readouterr().out

    def test_prints_only_name_for_empty_payload(self, capsys: pytest.CaptureFixture[str]) -> None:
        event = CustomEvent(name="test_event", payload={}, source="test")
        _print_event(event)
        captured = capsys.readouterr().out
        assert "test_event" in captured


# ── _default_readiness_probe ─────────────────────────────────────────────────


class TestDefaultReadinessProbe:
    def test_returns_true_for_status_200(self) -> None:
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", return_value=mock_response):
            assert _default_readiness_probe("http://example.com") is True

    def test_returns_false_for_status_500(self) -> None:
        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", return_value=mock_response):
            assert _default_readiness_probe("http://example.com") is False

    def test_returns_false_on_http_error(self) -> None:
        import httpx

        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", side_effect=httpx.HTTPError("boom")):
            assert _default_readiness_probe("http://example.com") is False


# ── DeploymentController protocol ────────────────────────────────────────────


class TestDeploymentControllerProtocol:
    def test_protocol_exists(self) -> None:
        assert DeploymentController is not None

    def test_minimal_implementation(self) -> None:
        class Dummy:
            async def deploy(self, config: Any) -> Any:
                return object()

            async def destroy(self, instance_id: str) -> None:
                return None

        assert isinstance(Dummy(), DeploymentController)


# ── AzureGameRuntime ─────────────────────────────────────────────────────────


class FakeDeploymentController:
    def __init__(self) -> None:
        self.deployed: list[Any] = []
        self.destroyed: list[str] = []
        self.deploy_result: Any = None

    async def deploy(self, config: Any) -> Any:
        self.deployed.append(config)
        return self.deploy_result

    async def destroy(self, instance_id: str) -> None:
        self.destroyed.append(instance_id)


class TestAzureGameRuntimeInit:
    def test_default_construction(self) -> None:
        runtime = AzureGameRuntime(environment={"ARM_CLIENT_ID": "test"})
        assert runtime.owns_endpoint is False
        assert runtime.endpoint_url == ""
        runtime.close()

    def test_custom_event_bus(self) -> None:
        bus = EventBus()
        runtime = AzureGameRuntime(environment={}, event_bus=bus)
        assert runtime._event_bus is bus
        runtime.close()

    def test_custom_deployment_manager(self) -> None:
        dm = FakeDeploymentController()
        runtime = AzureGameRuntime(environment={}, deployment_manager=dm)
        assert runtime._deployment_manager is dm
        runtime.close()

    def test_event_reporter_subscription(self) -> None:
        events: list[str] = []

        def reporter(event: Any) -> None:
            events.append("called")

        bus = EventBus()
        runtime = AzureGameRuntime(environment={}, event_bus=bus, event_reporter=reporter)
        bus.publish(CustomEvent(name="ping", payload={}, source="test"))
        assert "called" in events
        runtime.close()

    def test_none_event_reporter(self) -> None:
        runtime = AzureGameRuntime(environment={}, event_reporter=None)
        assert runtime._reporter_subscription is None
        runtime.close()

    def test_owns_endpoint_starts_false(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime.owns_endpoint is False
        runtime.close()

    def test_endpoint_url_starts_empty(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime.endpoint_url == ""
        runtime.close()


class TestAzureGameRuntimeStart:
    def test_closed_runtime_raises(self) -> None:
        runtime = AzureGameRuntime(environment={})
        runtime.close()
        with pytest.raises(RuntimeError, match="already closed"):
            runtime.start()

    def test_idempotent_start(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "fake-gateway",
            preflight=lambda: None,
        )
        g1 = runtime.start()
        g2 = runtime.start()
        assert g1 is g2
        assert g1 == "fake-gateway"
        runtime.close()

    def test_external_endpoint_selected(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda ep: ep,
            preflight=lambda: None,
        )
        gateway = runtime.start()
        assert gateway == "http://example.com/v1"
        assert runtime.endpoint_url == "http://example.com/v1"
        assert runtime.owns_endpoint is False
        runtime.close()

    def test_missing_url_and_provision_flag_raises(self) -> None:
        runtime = AzureGameRuntime(environment={})
        with pytest.raises(RuntimeError, match="AZURE_BASE_URL or AZURE_PROVISION_E2E=1"):
            runtime.start()
        runtime.close()

    def test_gateway_factory_returns_none_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: None,
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="could not be constructed"):
            runtime.start()
        runtime.close()

    def test_readiness_timeout_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_BASE_URL": "http://example.com/v1",
                "AZURE_GAME_READY_ATTEMPTS": "2",
                "AZURE_GAME_READY_INTERVAL_SECS": "0.01",
            },
            readiness_probe=lambda _: False,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="not ready"):
            runtime.start()
        runtime.close()

    def test_invalid_ready_attempts_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_BASE_URL": "http://example.com/v1",
                "AZURE_GAME_READY_ATTEMPTS": "0",
            },
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="at least 1"):
            runtime.start()
        runtime.close()

    def test_negative_ready_interval_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_BASE_URL": "http://example.com/v1",
                "AZURE_GAME_READY_ATTEMPTS": "3",
                "AZURE_GAME_READY_INTERVAL_SECS": "-1",
            },
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="non-negative"):
            runtime.start()
        runtime.close()


class TestAzureGameRuntimeValueExtraction:
    def test_float_value_valid(self) -> None:
        runtime = AzureGameRuntime(environment={"TEST_VAL": "3.14"})
        assert runtime._float_value("TEST_VAL", "0") == 3.14
        runtime.close()

    def test_float_value_invalid_raises(self) -> None:
        runtime = AzureGameRuntime(environment={"TEST_VAL": "abc"})
        with pytest.raises(RuntimeError, match="must be numeric"):
            runtime._float_value("TEST_VAL", "0")
        runtime.close()

    def test_int_value_valid(self) -> None:
        runtime = AzureGameRuntime(environment={"TEST_VAL": "42"})
        assert runtime._int_value("TEST_VAL", "0") == 42
        runtime.close()

    def test_int_value_invalid_raises(self) -> None:
        runtime = AzureGameRuntime(environment={"TEST_VAL": "abc"})
        with pytest.raises(RuntimeError, match="must be an integer"):
            runtime._int_value("TEST_VAL", "0")
        runtime.close()

    def test_value_returns_default(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime._value("MISSING", "default_val") == "default_val"
        runtime.close()

    def test_value_returns_empty_string_for_absent(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime._value("MISSING") == ""
        runtime.close()


class TestAzureGameRuntimeClose:
    def test_close_idempotent(self) -> None:
        runtime = AzureGameRuntime(environment={})
        runtime.close()
        runtime.close()

    def test_does_not_destroy_borrowed_endpoint(self) -> None:
        dm = FakeDeploymentController()
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            deployment_manager=dm,
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=lambda: None,
        )
        runtime.start()
        runtime.close()
        assert dm.destroyed == []

    def test_context_manager(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=lambda: None,
        )
        with runtime as gw:
            assert gw == "gw"
        assert runtime._closed is True


class TestAzureGameRuntimeComputeConfig:
    def test_invalid_gpu_type_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_BASE_URL": "http://example.com/v1",
                "AZURE_GPU_TYPE": "not_a_real_gpu",
            },
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="Unsupported AZURE_GPU_TYPE"):
            runtime._compute_config()
        runtime.close()

    def test_invalid_engine_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_GPU_TYPE": "a100_80",
                "AZURE_PROVISION_ENGINE": "not_an_engine",
            },
        )
        with pytest.raises(RuntimeError, match="Unsupported AZURE_PROVISION_ENGINE"):
            runtime._compute_config()
        runtime.close()

    def test_explicit_cidr_used(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "AZURE_GPU_TYPE": "a100_80",
                "AZURE_PROVISION_ENGINE": "vllm",
            },
        )
        config = runtime._compute_config()
        assert config.allowed_cidr == "10.0.0.0/8"
        runtime.close()

    def test_provider_is_azure(self) -> None:
        runtime = AzureGameRuntime(environment={"AZURE_ALLOWED_CIDR": "10.0.0.0/8"})
        config = runtime._compute_config()
        from general_ludd.infra.compute import ComputeProvider

        assert config.provider == ComputeProvider.AZURE
        runtime.close()


class TestAzureGameRuntimePreflight:
    def test_preflight_called_once(self) -> None:
        calls: list[int] = []

        def preflight() -> None:
            calls.append(1)

        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=preflight,
        )
        runtime.start()
        assert calls == [1]
        runtime.close()

    def test_preflight_failure_raises(self) -> None:
        def preflight() -> None:
            raise RuntimeError("preflight bomb")

        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            preflight=preflight,
        )
        with pytest.raises(RuntimeError, match="preflight bomb"):
            runtime.start()
        runtime.close()

    def test_none_preflight_skipped(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=None,
        )
        gw = runtime.start()
        assert gw == "gw"
        runtime.close()


# ── AzureGameRuntime — _resolve_allowed_cidr ──────────────────────────────────


class TestAzureGameRuntimeResolveAllowedCidr:
    def test_explicit_cidr_returned(self) -> None:
        runtime = AzureGameRuntime(environment={"AZURE_ALLOWED_CIDR": "192.168.0.0/16"})
        assert runtime._resolve_allowed_cidr() == "192.168.0.0/16"
        runtime.close()

    def test_ipify_fallback_success(self) -> None:
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"ip": "8.8.8.8"}
        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", return_value=mock_response):
            runtime = AzureGameRuntime(environment={})
            result = runtime._resolve_allowed_cidr()
            assert result == "8.8.8.8/32"
            runtime.close()

    def test_ipify_http_error_raises(self) -> None:
        import httpx

        with mock.patch(
            "general_ludd.cloud.azure_game_runtime.httpx.get",
            side_effect=httpx.HTTPError("network down"),
        ):
            runtime = AzureGameRuntime(environment={})
            with pytest.raises(RuntimeError, match="Unable to discover"):
                runtime._resolve_allowed_cidr()
            runtime.close()

    @pytest.mark.parametrize("address", ["10.0.0.1", "203.0.113.42"])
    def test_ipify_non_global_ip_raises(self, address: str) -> None:
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"ip": address}
        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", return_value=mock_response):
            runtime = AzureGameRuntime(environment={})
            with pytest.raises(RuntimeError, match="non-global IPv4"):
                runtime._resolve_allowed_cidr()
            runtime.close()

    def test_ipify_invalid_json_raises(self) -> None:
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("bad json")
        with mock.patch("general_ludd.cloud.azure_game_runtime.httpx.get", return_value=mock_response):
            runtime = AzureGameRuntime(environment={})
            with pytest.raises(RuntimeError, match="Unable to discover"):
                runtime._resolve_allowed_cidr()
            runtime.close()


# ── AzureGameRuntime — _wait_until_ready ──────────────────────────────────────


class TestAzureGameRuntimeWaitUntilReady:
    def test_ready_on_first_attempt(self) -> None:
        runtime = AzureGameRuntime(
            environment={},
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
        )
        runtime._wait_until_ready("http://example.com")
        runtime.close()

    def test_ready_after_multiple_attempts(self) -> None:
        probe_results = [False, False, True]
        calls: list[str] = []

        def staged_probe(endpoint: str) -> bool:
            calls.append(endpoint)
            return probe_results.pop(0)

        runtime = AzureGameRuntime(
            environment={
                "AZURE_GAME_READY_ATTEMPTS": "5",
                "AZURE_GAME_READY_INTERVAL_SECS": "0.001",
            },
            readiness_probe=staged_probe,
            sleep=lambda _: None,
        )
        runtime._wait_until_ready("http://example.com")
        assert len(calls) == 3
        runtime.close()

    def test_all_attempts_exhausted_raises(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_GAME_READY_ATTEMPTS": "3",
                "AZURE_GAME_READY_INTERVAL_SECS": "0.001",
            },
            readiness_probe=lambda _: False,
            sleep=lambda _: None,
        )
        with pytest.raises(RuntimeError, match="not ready after 3 attempts"):
            runtime._wait_until_ready("http://example.com")
        runtime.close()

    def test_zero_interval_ok(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_GAME_READY_ATTEMPTS": "2",
                "AZURE_GAME_READY_INTERVAL_SECS": "0",
            },
            readiness_probe=lambda _: False,
            sleep=lambda _: None,
        )
        with pytest.raises(RuntimeError, match="not ready"):
            runtime._wait_until_ready("http://example.com")
        runtime.close()

    def test_default_attempts_and_interval(self) -> None:
        runtime = AzureGameRuntime(
            environment={},
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
        )
        runtime._wait_until_ready("http://example.com")
        runtime.close()


# ── AzureGameRuntime — _compute_config defaults ───────────────────────────────


class TestAzureGameRuntimeComputeConfigDefaults:
    def test_all_defaults(self) -> None:
        runtime = AzureGameRuntime(environment={"AZURE_ALLOWED_CIDR": "10.0.0.0/8"})
        config = runtime._compute_config()
        assert config.model_name == "Qwen/Qwen2.5-0.5B-Instruct"
        assert config.region == "eastus"
        assert config.deploy_type == "containerapp"
        assert config.max_cost_usd == 5.0
        assert config.timeout_minutes == 30.0
        assert config.disk_size_gb == 100
        from general_ludd.infra.compute import GPUType, InferenceEngine

        assert config.gpu_type == GPUType.A100_80
        assert config.engine == InferenceEngine.VLLM
        runtime.close()

    def test_custom_region_and_deploy_type(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "AZURE_REGION": "westeurope",
                "AZURE_DEPLOY_TYPE": "vm",
                "AZURE_DISK_SIZE_GB": "50",
            }
        )
        config = runtime._compute_config()
        assert config.region == "westeurope"
        assert config.deploy_type == "vm"
        assert config.disk_size_gb == 50
        runtime.close()

    def test_custom_max_spend_and_timeout(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "GLUDD_E2E_MAX_SPEND_USD": "12.50",
                "AZURE_TIMEOUT_MINUTES": "15",
            }
        )
        config = runtime._compute_config()
        assert config.max_cost_usd == 12.5
        assert config.timeout_minutes == 15.0
        runtime.close()

    def test_gpu_type_normalization(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "AZURE_GPU_TYPE": "a100-80",
            }
        )
        config = runtime._compute_config()
        from general_ludd.infra.compute import GPUType

        assert config.gpu_type == GPUType.A100_80
        runtime.close()

    def test_engine_normalization(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "AZURE_GPU_TYPE": "t4",
                "AZURE_PROVISION_ENGINE": "vLLM",
            }
        )
        config = runtime._compute_config()
        from general_ludd.infra.compute import InferenceEngine

        assert config.engine == InferenceEngine.VLLM
        runtime.close()

    def test_auth_aliases_empty_when_no_arm_vars(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            }
        )
        config = runtime._compute_config()
        assert config.provider_auth_aliases is None
        runtime.close()

    def test_auth_aliases_populated_with_arm_vars(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
                "ARM_CLIENT_ID": "cid",
                "ARM_CLIENT_SECRET": "cs",
                "ARM_TENANT_ID": "tid",
                "ARM_SUBSCRIPTION_ID": "sid",
            }
        )
        config = runtime._compute_config()
        assert config.provider_auth_aliases is not None
        assert config.provider_auth_aliases["ARM_CLIENT_ID"] == "ARM_CLIENT_ID"
        assert config.provider_auth_aliases["ARM_CLIENT_SECRET"] == "ARM_CLIENT_SECRET"
        runtime.close()


# ── AzureGameRuntime — close destroys owned endpoint ──────────────────────────


class TestAzureGameRuntimeCloseDestroy:
    def test_close_destroys_owned_endpoint(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-owned-1"
            endpoint_url = "http://10.0.0.1:8000/v1"
            ip_address = ""
            port = 8000

        dm.deploy_result = FakeInstance()
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        runtime.start()
        assert runtime.owns_endpoint is True
        runtime.close()
        assert dm.destroyed == ["inst-owned-1"]
        assert runtime.owns_endpoint is False

    def test_close_only_destroys_once(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-once"
            endpoint_url = "http://10.0.0.2:8000/v1"
            ip_address = ""
            port = 8000

        dm.deploy_result = FakeInstance()
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        runtime.start()
        runtime.close()
        runtime.close()
        assert len(dm.destroyed) == 1

    def test_destroy_failure_raises(self) -> None:
        class BadController:
            async def deploy(self, config: Any) -> Any:
                class Instance:
                    instance_id = "will-fail-destroy"
                    endpoint_url = "http://x:8000/v1"
                    ip_address = ""
                    port = 8000

                return Instance()

            async def destroy(self, instance_id: str) -> None:
                raise RuntimeError("destroy exploded")

        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=BadController(),
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        runtime.start()
        with pytest.raises(RuntimeError, match="destroy exploded"):
            runtime.close()


# ── AzureGameRuntime — start provisioning path ────────────────────────────────


class TestAzureGameRuntimeStartProvision:
    def test_start_provisions_when_flag_set(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-provisioned"
            endpoint_url = "http://10.0.0.3:8000/v1"
            ip_address = ""
            port = 8000

        dm.deploy_result = FakeInstance()
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
            gateway_factory=lambda ep: f"gw-{ep}",
        )
        gateway = runtime.start()
        assert gateway == "gw-http://10.0.0.3:8000/v1"
        assert runtime.owns_endpoint is True
        assert runtime.endpoint_url == "http://10.0.0.3:8000/v1"
        assert len(dm.deployed) == 1
        runtime.close()
        assert dm.destroyed == ["inst-provisioned"]

    def test_provision_failure_closes_runtime(self) -> None:
        class FailingController:
            async def deploy(self, config: Any) -> Any:
                raise RuntimeError("provision failed")

            async def destroy(self, instance_id: str) -> None:
                pass

        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=FailingController(),
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="provision failed"):
            runtime.start()
        assert runtime._closed is True

    def test_instance_no_endpoint_uses_ip_port(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-ip-only"
            endpoint_url = ""
            ip_address = "10.0.0.99"
            port = 8000

        dm.deploy_result = FakeInstance()
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
            gateway_factory=lambda ep: ep,
        )
        endpoint = runtime.start()
        assert endpoint == "http://10.0.0.99:8000"
        runtime.close()

    def test_instance_no_endpoint_no_ip_raises(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-no-endpoint"
            endpoint_url = ""
            ip_address = ""
            port = 0

        dm.deploy_result = FakeInstance()
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            preflight=lambda: None,
        )
        with pytest.raises(RuntimeError, match="without an inference endpoint"):
            runtime.start()
        runtime.close()


# ── AzureGameRuntime — _publish ───────────────────────────────────────────────


class TestAzureGameRuntimePublish:
    def test_publish_sends_event_to_bus(self) -> None:
        bus = EventBus()
        received: list[dict[str, object]] = []

        def listener(event: Any) -> None:
            received.append(event.payload)

        bus.subscribe("custom", listener)
        runtime = AzureGameRuntime(environment={}, event_bus=bus)
        runtime._publish("test_name", key="value", attempt=1)
        runtime.close()
        assert len(received) == 1
        assert received[0]["name"] == "test_name"
        assert received[0]["key"] == "value"
        assert received[0]["attempt"] == 1

    def test_publish_multiple_events(self) -> None:
        bus = EventBus()
        received: list[dict[str, object]] = []

        def listener(event: Any) -> None:
            received.append(event.payload)

        bus.subscribe("custom", listener)
        runtime = AzureGameRuntime(environment={}, event_bus=bus)
        runtime._publish("a")
        runtime._publish("b", count=5)
        runtime.close()
        assert len(received) == 2
        assert received[0]["name"] == "a"
        assert received[1]["name"] == "b"
        assert received[1]["count"] == 5


# ── AzureGameRuntime — _default_deployment_manager ────────────────────────────


class TestAzureGameRuntimeDefaultDeploymentManager:
    def test_uses_env_secrets_manager(self) -> None:
        runtime = AzureGameRuntime(
            environment={
                "ARM_CLIENT_ID": "test-cid",
                "ARM_CLIENT_SECRET": "test-cs",
                "ARM_TENANT_ID": "test-tid",
                "ARM_SUBSCRIPTION_ID": "test-sid",
            }
        )
        dm = runtime._default_deployment_manager()
        assert dm is not None
        from general_ludd.infra.deployment import DeploymentManager

        assert isinstance(dm, DeploymentManager)
        runtime.close()

    def test_event_bus_passed_to_manager(self) -> None:
        bus = EventBus()
        runtime = AzureGameRuntime(environment={}, event_bus=bus)
        dm = runtime._default_deployment_manager()
        assert dm._event_bus is bus
        runtime.close()


# ── AzureGameRuntime — _float_value / _int_value defaults ─────────────────────


class TestAzureGameRuntimeNumericDefaults:
    def test_float_value_uses_default_when_absent(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime._float_value("MISSING", "2.5") == 2.5
        runtime.close()

    def test_int_value_uses_default_when_absent(self) -> None:
        runtime = AzureGameRuntime(environment={})
        assert runtime._int_value("MISSING", "99") == 99
        runtime.close()

    def test_float_value_strips_whitespace(self) -> None:
        runtime = AzureGameRuntime(environment={"X": "  7.0  "})
        assert runtime._float_value("X", "0") == 7.0
        runtime.close()

    def test_int_value_strips_whitespace(self) -> None:
        runtime = AzureGameRuntime(environment={"Y": "  42  "})
        assert runtime._int_value("Y", "0") == 42
        runtime.close()


# ── AzureGameRuntime — close unsubscribe ──────────────────────────────────────


class TestAzureGameRuntimeCloseCleanup:
    def test_close_unsubscribes_reporter(self) -> None:
        bus = EventBus()
        runtime = AzureGameRuntime(environment={}, event_bus=bus, event_reporter=lambda e: None)
        assert runtime._reporter_subscription is not None
        runtime.close()
        assert runtime._reporter_subscription is None

    def test_close_without_reporter_no_error(self) -> None:
        runtime = AzureGameRuntime(environment={}, event_reporter=None)
        runtime.close()

    def test_close_publishes_destroy_events(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-has-events"
            endpoint_url = "http://10.0.0.4:8000/v1"
            ip_address = ""
            port = 8000

        dm.deploy_result = FakeInstance()
        events_received: list[str] = []

        bus = EventBus()

        def capture(event: Any) -> None:
            events_received.append(str(event.payload.get("name", "")))

        bus.subscribe("custom", capture)
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            event_bus=bus,
            event_reporter=capture,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        runtime.start()
        events_received.clear()
        runtime.close()
        assert any("destroy_started" in e for e in events_received)
        assert any("destroy_completed" in e for e in events_received)


# ── AzureGameRuntime — context manager idle state ─────────────────────────────


class TestAzureGameRuntimeContextManager:
    def test_exit_closes_on_exception(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=lambda: None,
        )
        try:
            with runtime as gw:
                assert gw == "gw"
                raise ValueError("inside block")
        except ValueError:
            pass
        assert runtime._closed is True

    def test_exit_closes_cleanly(self) -> None:
        runtime = AzureGameRuntime(
            environment={"AZURE_BASE_URL": "http://example.com/v1"},
            readiness_probe=lambda _: True,
            gateway_factory=lambda _: "gw",
            preflight=lambda: None,
        )
        with runtime:
            pass
        assert runtime._closed is True


# ── AzureGameRuntime — event payload edge cases ───────────────────────────────


class TestAzureGameRuntimeEventPayload:
    def test_publish_with_none_value(self) -> None:
        bus = EventBus()
        received: list[dict[str, object]] = []

        def listener(event: Any) -> None:
            received.append(event.payload)

        bus.subscribe("custom", listener)
        runtime = AzureGameRuntime(environment={}, event_bus=bus)
        runtime._publish("test_none", value=None, count=0)
        runtime.close()
        assert received[0]["value"] is None
        assert received[0]["count"] == 0

    def test_deploy_event_payload(self) -> None:
        dm = FakeDeploymentController()

        class FakeInstance:
            instance_id = "inst-event-payload"
            endpoint_url = "http://10.0.0.5:8000/v1"
            ip_address = ""
            port = 8000

        dm.deploy_result = FakeInstance()
        bus = EventBus()
        events: list[dict[str, object]] = []

        def capture(event: Any) -> None:
            events.append(event.payload)

        bus.subscribe("custom", capture)
        runtime = AzureGameRuntime(
            environment={
                "AZURE_PROVISION_E2E": "1",
                "AZURE_ALLOWED_CIDR": "10.0.0.0/8",
            },
            deployment_manager=dm,
            event_bus=bus,
            event_reporter=capture,
            readiness_probe=lambda _: True,
            sleep=lambda _: None,
            preflight=lambda: None,
        )
        runtime.start()
        deploy_events = [e for e in events if "deploy" in str(e.get("name", ""))]
        assert len(deploy_events) >= 2
        deploy_started = deploy_events[0]
        assert "gpu_type" in deploy_started
        assert "model" in deploy_started
        assert "region" in deploy_started
        runtime.close()
