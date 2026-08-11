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
